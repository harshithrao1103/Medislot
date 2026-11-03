# Standard library, HTTP client, Flask, and server-side session imports.
"""MediSlot doctor portal. All business data is fetched from API Gateway."""
import os
import json
import secrets
from functools import wraps
from datetime import date, datetime, timezone, timedelta
from urllib.parse import urlencode
import requests
from flask import Flask, render_template, request, redirect, session, url_for, flash, Response
from flask_session import Session

# --------------------------------------------------------------------
# FLASK APPLICATION CONFIGURATION
# Sessions are stored server-side; secrets and API URL come from env vars.
# --------------------------------------------------------------------
app = Flask(__name__)
app.config.update(SECRET_KEY=os.environ['FLASK_SECRET_KEY'], SESSION_TYPE='filesystem',
                  SESSION_FILE_DIR='/var/lib/medislot/sessions', SESSION_PERMANENT=True, PERMANENT_SESSION_LIFETIME=timedelta(days=7),
                  SESSION_USE_SIGNER=True, SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE','false')=='true')
Session(app)
API = os.environ['MEDISLOT_API_BASE_URL'].rstrip('/')


# ====================================================================
# TOKEN REFRESH
# ====================================================================
def refresh_access():
    """Exchange the session refresh token for a new access token and update expiry timestamps."""
    token = session.get('refresh_token')
    if not token:
        return False
    try:
        r = requests.post(API + '/auth/refresh', json={'refreshToken': token}, timeout=15)
        if not r.ok:
            if r.status_code in (400, 401, 403):
                session.clear()
            return False
        data = r.json()
        if not data.get('accessToken'):
            return False
        session['access_token'] = data['accessToken']
        session['access_expires_at'] = int(data.get('expiresAt', 0))
        session['refresh_expires_at'] = int(data.get('refreshExpiresAt', session.get('refresh_expires_at', 0)))
        return True
    except (requests.RequestException, ValueError, TypeError):
        return False


# ====================================================================
# AUTHENTICATED API REQUESTS
# ====================================================================
def api(method, path, **kwargs):
    """Attach the access token, refresh it near expiry, and retry once after HTTP 401."""
    headers = kwargs.pop('headers', {}).copy()
    authenticated = bool(session.get('role') == 'doctor' and session.get('refresh_token'))
    if authenticated and (not session.get('access_token') or
                          int(session.get('access_expires_at') or 0) <= int(datetime.now(timezone.utc).timestamp()) + 60):
        refresh_access()
    if session.get('access_token'):
        headers['Authorization'] = 'Bearer ' + session['access_token']
    r = requests.request(method, API + path, headers=headers, timeout=15, **kwargs)
    if r.status_code == 401 and authenticated and path != '/auth/refresh' and refresh_access():
        headers['Authorization'] = 'Bearer ' + session['access_token']
        r = requests.request(method, API + path, headers=headers, timeout=15, **kwargs)
    return r


# ====================================================================
# DOCTOR ROUTE GUARD
# ====================================================================
def protected(fn):
    """Require a doctor session with a non-expired refresh token before loading a protected page."""
    @wraps(fn)
    def inner(*args, **kwargs):
        if session.get('role') != 'doctor' or not session.get('refresh_token'):
            return redirect(url_for('login'))
        if session.get('refresh_expires_at') and int(session['refresh_expires_at']) <= int(datetime.now(timezone.utc).timestamp()):
            session.clear()
            flash('Your refresh token has expired. Please sign in again.')
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return inner


# ====================================================================
# CSRF TOKEN GENERATION
# ====================================================================
def csrf():
    """Create one random CSRF token per session for use in HTML forms."""
    if not session.get('csrf'):
        session['csrf'] = secrets.token_urlsafe(32)
    return session['csrf']
app.jinja_env.globals['csrf_token'] = csrf


# ====================================================================
# CSRF REQUEST VALIDATION
# ====================================================================
@app.before_request
def check_csrf():
    """Reject POST form submissions when their CSRF token does not match the session token."""
    if request.method == 'POST' and request.form.get('csrf_token') != csrf():
        return 'Invalid CSRF token', 403


# ====================================================================
# ROOT REDIRECT
# ====================================================================
@app.route('/')
def index():
    """Send signed-in doctors to their profile and visitors to the welcome page."""
    return redirect(url_for('profile') if session.get('role')=='doctor' and session.get('refresh_token') else url_for('welcome'))


# ====================================================================
# WELCOME PAGE
# ====================================================================
@app.route('/doctor/welcome')
def welcome():
    """Display the public welcome page or redirect an authenticated doctor."""
    if session.get('role') == 'doctor' and session.get('refresh_token'):
        return redirect(url_for('profile'))
    return render_template('welcome.html')


# ====================================================================
# DOCTOR REGISTRATION
# ====================================================================
@app.route('/doctor/register', methods=['GET', 'POST'])
def register():
    """Collect registration form data and submit it to the doctor registration API."""
    if request.method == 'POST':
        try:
            payload = {k: request.form.get(k, '').strip() for k in
                       ('name','email','password','licence_number','specialization','qualifications',
                        'hospital_name','clinic_address','city','timezone')}
            payload['experience_years'] = int(request.form['experience_years'])
            payload['languages'] = [v.strip() for v in request.form.get('languages','').split(',') if v.strip()]
            r = requests.post(API + '/auth/doctors/register', json=payload, timeout=15)
            if r.ok:
                flash('Registration successful. Please sign in to continue.')
                return redirect(url_for('login'))
            flash(api_error(r))
        except (ValueError, requests.RequestException):
            flash('Please check your registration details or try again later.')
    return render_template('register.html')


# ====================================================================
# DOCTOR LOGIN
# ====================================================================
@app.route('/doctor/login', methods=['GET','POST'])
def login():
    """Authenticate doctor credentials and save access and refresh tokens in the session."""
    if request.method=='POST':
        try:
            r=requests.post(API + '/auth/doctors/login',json={'email':request.form['email'],'password':request.form['password']},timeout=15)
            data=r.json()
            if r.ok:
                session.clear()
                session['access_token']=data.get('accessToken') or data.get('access_token')
                session['refresh_token']=data.get('refreshToken') or data.get('refresh_token')
                session['access_expires_at']=data.get('expiresAt',0)
                session['refresh_expires_at']=data.get('refreshExpiresAt',0)
                if not session['access_token'] or not session['refresh_token']:
                    flash('Login response did not include access token')
                else:
                    session['role']='doctor'
                    return redirect(url_for('profile'))
            else:flash(data.get('error','Login failed'))
        except (requests.RequestException, ValueError):flash('Login API unavailable or returned an invalid response')
    return render_template('login.html')


# ====================================================================
# DOCTOR LOGOUT
# ====================================================================
@app.route('/doctor/logout', methods=['POST'])
def logout():
    """Request token revocation, clear the local session, and return to login."""
    try:
        api('POST','/auth/logout',json={'refreshToken':session.get('refresh_token')})
    except requests.RequestException:pass
    session.clear()
    return redirect(url_for('login'))


# ====================================================================
# API ERROR MESSAGES
# ====================================================================
def api_error(r):
    """Extract an API error or message, falling back to its HTTP status."""
    try:
        body = r.json()
        if isinstance(body, dict):
            return str(body.get('error') or body.get('message') or f'API request failed (HTTP {r.status_code})')
    except ValueError:
        pass
    return f'API request failed (HTTP {r.status_code})'


# ====================================================================
# PAGE DATA FETCHING
# ====================================================================
def page_get(path):
    """Fetch JSON for a page and display errors using Flask flash messages."""
    try:
        r = api('GET', path)
        if r.status_code == 401:
            session.clear()
            flash('Your session has expired. Please sign in again.')
            return None
        if not r.ok:
            flash(api_error(r))
            return {}
        return r.json()
    except (requests.RequestException, ValueError):
        flash('Unable to load data from the API')
        return {}


# ====================================================================
# PAGE DATA MODIFICATIONS
# ====================================================================
def page_post(method, path, payload):
    """Send a JSON update request and display a success or error message."""
    try:
        r = api(method, path, json=payload)
        if r.status_code == 401:
            session.clear()
            flash('Your session has expired. Please sign in again.')
            return False
        flash('Saved successfully' if r.ok else api_error(r))
        return r.ok
    except requests.RequestException:
        flash('API unavailable')
        return False


# ====================================================================
# DOCTOR PROFILE
# ====================================================================
@app.route('/doctor/profile',methods=['GET','POST'])
@protected
def profile():
    """Display doctor profile data and submit edited professional details."""
    if request.method=='POST':
        payload={key:request.form[key] for key in ('name','specialization','qualifications','hospital_name','clinic_address','city','timezone')}
        try:
            payload['experience_years']=int(request.form['experience_years'])
        except (ValueError, KeyError):
            flash('Experience years must be a valid number')
            return redirect(url_for('profile'))
        payload['languages']=[x.strip() for x in request.form['languages'].split(',') if x.strip()]
        page_post('PUT','/doctors/me',payload)
        return redirect(url_for('profile'))
    data=page_get('/doctors/me')
    if data is None:return redirect(url_for('login'))
    return render_template('profile.html',data=data)


# ====================================================================
# CONSULTATION TYPES
# ====================================================================
@app.route('/doctor/consultations',methods=['GET','POST'])
@protected
def consultations():
    """List, create, update, or delete predefined consultation types."""
    if request.method=='POST':
        if request.form.get('delete_id'):
            page_post('DELETE','/doctors/me/consultation-types/'+request.form['delete_id'],{})
        else:
            try:
                presets={'Quick Consult':(15,1,1),'Standard Consult':(30,1,1),
                         'Detailed Consult':(45,1,1),'Health Camp Session':(60,2,10)}
                name=request.form['name']
                duration,low,high=presets[name]
                capacity=int(request.form.get('capacity',1)) if low != high else low
                if not low <= capacity <= high: raise ValueError('Invalid capacity')
                payload={'name':name,'duration_minutes':duration,
                         'fee':request.form['fee'],'capacity':capacity}
            except (ValueError, KeyError):
                flash('Please enter valid consultation details')
                return redirect(url_for('consultations'))
            item_id=request.form.get('item_id')
            page_post('PUT' if item_id else 'POST','/doctors/me/consultation-types'+('/'+item_id if item_id else ''),payload)
        return redirect(url_for('consultations'))
    data=page_get('/doctors/me/consultation-types')
    return render_template('consultations.html',items=(data or {}).get('items',[]))


# ====================================================================
# WEEKLY AVAILABILITY
# ====================================================================
@app.route('/doctor/availability',methods=['GET','POST'])
@protected
def availability():
    """List and manage the doctor weekly availability rules."""
    if request.method=='POST':
        try:
            if request.form.get('delete_id'):
                page_post('DELETE','/doctors/me/availability/'+request.form['delete_id'],{})
            else:
                item={'consultation_type_id':int(request.form['consultation_type_id']),
                      'day_of_week':int(request.form['day_of_week']),
                      'start_time':request.form['start_time'], 'end_time':request.form['end_time']}
                item_id=request.form.get('item_id')
                page_post('PUT' if item_id else 'POST', '/doctors/me/availability'+('/'+item_id if item_id else ''),item)
        except (ValueError, KeyError):
            flash('Please enter valid availability details')
        return redirect(url_for('availability'))
    data=page_get('/doctors/me/availability')
    consults=page_get('/doctors/me/consultation-types')
    if data is None or consults is None: return redirect(url_for('login'))
    return render_template('availability.html',items=(data or {}).get('items',[]),
                           consultations=(consults or {}).get('items',[]))


# ====================================================================
# DOCTOR BLACKOUT DATES
# ====================================================================
@app.route('/doctor/blackouts',methods=['GET','POST'])
@protected
def blackouts():
    """List, add, or remove dates when the doctor is unavailable."""
    if request.method=='POST':
        if request.form.get('delete_id'):
            page_post('DELETE','/doctors/me/blackouts/'+request.form['delete_id'],{})
        else:
            page_post('POST','/doctors/me/blackouts',{'blackout_date':request.form['blackout_date'],
                'reason':request.form.get('reason','')})
        return redirect(url_for('blackouts'))
    data=page_get('/doctors/me/blackouts')
    return render_template('blackouts.html',items=(data or {}).get('items',[]))


# ====================================================================
# DAY SCHEDULE
# ====================================================================
@app.route('/doctor/schedule')
@protected
def schedule():
    """Validate the selected date and load its slots and consultation names."""
    day=request.args.get('date',date.today().isoformat())
    try:
        day = date.fromisoformat(day).isoformat()
    except ValueError:
        flash('Invalid schedule date')
        day = date.today().isoformat()
    data=page_get('/doctors/me/schedule?' + urlencode({'date': day}))
    consults=page_get('/doctors/me/consultation-types')
    names={str(x['consultation_type_id']):x['name'] for x in (consults or {}).get('items',[])}
    return render_template('schedule.html',data=data or {},day=day,consultation_names=names)


# ====================================================================
# PROFILE PHOTO UPLOAD
# ====================================================================
@app.route('/doctor/photo', methods=['POST'])
@protected
def upload_photo():
    """Validate an uploaded image and send it using a presigned upload form."""
    photo=request.files.get('photo')
    if not photo or photo.mimetype not in ('image/jpeg','image/png','image/webp'):
        flash('Choose a JPEG, PNG or WebP image')
        return redirect(url_for('profile'))
    data=photo.read(5*1024*1024+1)
    if not 0<len(data)<=5*1024*1024:
        flash('Image must be 5 MB or smaller')
        return redirect(url_for('profile'))
    try:
        presigned=api('POST','/doctors/me/photo',json={'contentType':photo.mimetype,'sizeBytes':len(data)})
        presigned.raise_for_status()
        upload=presigned.json()['upload']
        fields=upload['fields']
        result=requests.post(upload['url'],data=fields,files={'file':(photo.filename,data,photo.mimetype)},timeout=30)
        result.raise_for_status()
        flash('Profile photo uploaded')
    except (requests.RequestException,KeyError):
        flash('Profile photo upload failed')
    return redirect(url_for('profile'))


# ====================================================================
# PROFILE PHOTO DISPLAY
# ====================================================================
@app.route('/doctor/photo/view')
@protected
def view_photo():
    """Fetch a doctor photo using its signed URL and return it as an image response."""
    try:
        r=api('GET','/doctors/me/photo')
        if not r.ok:
            return '', 404
        url=r.json().get('url')
        if not url:
            return '', 404
        image=requests.get(url,timeout=15,stream=True)
        image.raise_for_status()
        content=image.content
        if len(content)>5*1024*1024:
            return '', 413
        return Response(content,content_type=image.headers.get('Content-Type','image/jpeg'),
                        headers={'Cache-Control':'private, max-age=120','X-Content-Type-Options':'nosniff'})
    except (requests.RequestException,ValueError):
        return '', 404
