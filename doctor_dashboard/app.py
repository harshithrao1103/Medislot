# Standard library, HTTP client, Flask, and server-side session imports.
"""MediSlot doctor portal. All business data is fetched from API Gateway."""
import os
import json
import secrets
from functools import wraps
from datetime import date, datetime, timezone, timedelta
from urllib.parse import urlencode
import requests
import boto3
from botocore.exceptions import BotoCoreError, ClientError
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
# Refreshes the doctor's access token using the refresh token stored in the Flask session.
# Calls the Token Lambda through API Gateway and updates the session with the new JWT and expiry.
# If the refresh token is invalid or expired, clears the session and returns False.

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
# Sends authenticated requests from Flask to API Gateway using the doctor's JWT.
# Automatically refreshes the access token if it is missing or expires within 60 seconds.
# If the API returns HTTP 401, attempts token refresh and retries the request once.

def api(method, path, **kwargs):
    """Attach the access token, refresh it near expiry, and retry once after HTTP 401."""
    headers = kwargs.pop('headers', {}).copy()
    authenticated = bool(session.get('role') in ('doctor','patient') and session.get('refresh_token'))
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
# Protects doctor dashboard pages by validating the user's session.
def protected(fn):
    """Require a doctor session with a non-expired refresh token before loading a protected page."""

    @wraps(fn)
    def inner(*args, **kwargs):

        # Redirect to login if the user is not a doctor or has no refresh token.
        if session.get('role') != 'doctor' or not session.get('refresh_token'):
            return redirect(url_for('login'))

        # If the refresh token has expired, clear the session and require login again.
        if session.get('refresh_expires_at') and int(session['refresh_expires_at']) <= int(datetime.now(timezone.utc).timestamp()):
            session.clear()
            flash('Your refresh token has expired. Please sign in again.')
            return redirect(url_for('login'))

        # Session checks passed; execute the requested page function.
        return fn(*args, **kwargs)

    return inner

# ====================================================================
# CSRF TOKEN GENERATION(Cross-Site Request Forgery)
# ====================================================================
# Generate a random CSRF token once per session to protect HTML forms.
def csrf():
    """Create one random CSRF token per session for use in HTML forms."""
    if not session.get('csrf'):
        session['csrf'] = secrets.token_urlsafe(32)
    return session['csrf']

# Make csrf_token() accessible inside Flask HTML templates.
app.jinja_env.globals['csrf_token'] = csrf


# ====================================================================
# CSRF REQUEST VALIDATION
# ====================================================================
# Validate CSRF tokens before processing incoming POST form requests.
@app.before_request
def check_csrf():
    """Reject POST form submissions when their CSRF token does not match the session token."""

    # Reject POST requests if the submitted token differs from the session token.
    if request.method == 'POST' and request.form.get('csrf_token') != csrf():
        return 'Invalid CSRF token', 403


# ====================================================================
# ROOT REDIRECT
# ====================================================================
@app.route('/')
def index():
    """Send signed-in doctors to their profile and visitors to the welcome page."""
    return redirect(url_for('profile') if session.get('role')=='doctor' and session.get('refresh_token') else url_for('patient_profile') if session.get('role')=='patient' and session.get('refresh_token') else url_for('welcome'))


# ====================================================================
# WELCOME PAGE
# ====================================================================
@app.route('/doctor/welcome')
def welcome():
    """Display the public welcome page or redirect an authenticated doctor."""
    if session.get('role') == 'doctor' and session.get('refresh_token'):
        return redirect(url_for('profile'))
    if session.get('role') == 'patient' and session.get('refresh_token'):
        return redirect(url_for('patient_profile'))
    return render_template('welcome.html')


def load_specializations():
    """Load the authoritative dropdown choices from the public API."""
    try:
        result = requests.get(API + '/specializations', timeout=15)
        result.raise_for_status()
        return result.json()['specializations']
    except (requests.RequestException, ValueError, KeyError, TypeError):
        flash('Specializations unavailable. Please try again later.')
        return []


# ====================================================================
# DOCTOR REGISTRATION
# ====================================================================
# Displays the doctor registration form and processes submitted details.
@app.route('/doctor/register', methods=['GET', 'POST'])
def register():
    """Collect registration form data and submit it to the doctor registration API."""

    if request.method == 'POST':
        try:
            # Collect doctor details from the HTML form and remove extra spaces.
            payload = {k: request.form.get(k, '').strip() for k in
                       ('first_name','last_name','email','password','licence_number','qualifications',
                        'hospital_name','clinic_address','city','timezone')}

            payload['specialization_id'] = int(request.form['specialization_id'])

            # Convert experience to an integer and languages into a list.
            payload['experience_years'] = int(request.form['experience_years'])
            payload['languages'] = [v.strip() for v in request.form.get('languages','').split(',') if v.strip()]

            # Send registration details to Doctor Register Lambda through API Gateway.
            r = requests.post(API + '/auth/doctors/register', json=payload, timeout=15)

            if r.ok:
                flash('Registration successful. Please sign in to continue.')
                return redirect(url_for('login'))

            flash(api_error(r))

        except (ValueError, requests.RequestException):
            flash('Please check your registration details or try again later.')

    return render_template('register.html', specializations=load_specializations())


# ====================================================================
# DOCTOR LOGIN
# ====================================================================
# Display the doctor login page and authenticate submitted credentials.
@app.route('/doctor/login', methods=['GET','POST'])
def login():
    """Authenticate doctor credentials and save access and refresh tokens in the session."""
    if request.method=='POST':
        try:
            # Send doctor credentials to the Login Lambda through API Gateway.
            r=requests.post(API + '/auth/doctors/login',json={'email':request.form['email'],'password':request.form['password']},timeout=15)
            data=r.json()

            if r.ok:
                # Clear the previous session and store the new authentication tokens.
                session.clear()
                session['access_token']=data.get('accessToken') or data.get('access_token')
                session['refresh_token']=data.get('refreshToken') or data.get('refresh_token')
                session['access_expires_at']=data.get('expiresAt',0)
                session['refresh_expires_at']=data.get('refreshExpiresAt',0)

                # Redirect to the doctor profile only when both tokens are present.
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
# Fetch data from protected backend APIs and handle authentication or API errors.
def page_get(path):
    """Fetch JSON for a page and display errors using Flask flash messages."""
    try:
        # Make an authenticated GET request using the shared api() helper.
        r = api('GET', path)

        # Clear the session if authentication still fails.
        if r.status_code == 401:
            session.clear()
            flash('Your session has expired. Please sign in again.')
            return None

        # Display backend errors and return an empty dictionary.
        if not r.ok:
            flash(api_error(r))
            return {}

        # Return the successfully fetched JSON data.
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
# Display the doctor's profile and process profile updates.
@app.route('/doctor/profile',methods=['GET','POST'])
@protected
def profile():
    """Display doctor profile data and submit edited professional details."""

    if request.method=='POST':
        # Collect the updated profile fields from the HTML form.
        payload={key:request.form[key] for key in ('first_name','last_name','qualifications','hospital_name','clinic_address','city','timezone')}

        try:
            payload['specialization_id'] = int(request.form['specialization_id'])
        except (ValueError, KeyError):
            flash('Select a valid specialization')
            return redirect(url_for('profile'))

        # Validate and convert experience years to an integer.
        try:
            payload['experience_years']=int(request.form['experience_years'])
        except (ValueError, KeyError):
            flash('Experience years must be a valid number')
            return redirect(url_for('profile'))

        # Convert comma-separated languages into a list.
        payload['languages']=[x.strip() for x in request.form['languages'].split(',') if x.strip()]

        # Send the updated profile details to the backend API.
        page_post('PUT','/doctors/me',payload)
        return redirect(url_for('profile'))

    # Fetch the doctor's existing profile details.
    data=page_get('/doctors/me')

    if data is None:return redirect(url_for('login'))

    # Display the fetched profile information in the HTML template.
    return render_template('profile.html',data=data,specializations=load_specializations())

# ====================================================================
# CONSULTATION TYPES
# ====================================================================
# ====================================================================
# CONSULTATION TYPES
# ====================================================================
@app.route('/doctor/consultations',methods=['GET','POST'])
@protected
def consultations():
    """List, create, update, or delete predefined consultation types."""
    if request.method=='POST':

        # Delete an existing consultation type if a delete ID is provided.
        if request.form.get('delete_id'):
            page_post('DELETE','/doctors/me/consultation-types/'+request.form['delete_id'],{})
        else:
            try:
                # Define allowed consultation types with duration and capacity limits.
                presets={'Quick Consult':(15,1,1),'Standard Consult':(30,1,1),
                         'Detailed Consult':(45,1,1),'Health Camp Session':(60,2,10)}

                # Get the selected consultation and its predefined limits.
                name=request.form['name']
                duration,low,high=presets[name]

                # Determine capacity and validate it against the allowed range.
                capacity=int(request.form.get('capacity',1)) if low != high else low
                if not low <= capacity <= high: raise ValueError('Invalid capacity')

                # Prepare consultation details for the backend API.
                payload={'name':name,'duration_minutes':duration,
                         'fee':request.form['fee'],'capacity':capacity}

            except (ValueError, KeyError):
                flash('Please enter valid consultation details')
                return redirect(url_for('consultations'))

            # Update an existing consultation or create a new one.
            item_id=request.form.get('item_id', '').strip()
            if item_id and (not item_id.isdecimal() or int(item_id) < 1):
                flash('Invalid consultation selected for editing')
                return redirect(url_for('consultations'))
            page_post('PUT' if item_id else 'POST','/doctors/me/consultation-types'+('/'+item_id if item_id else ''),payload)

        return redirect(url_for('consultations'))

    # Fetch existing consultation types and display them in the HTML page.
    data=page_get('/doctors/me/consultation-types')
    return render_template('consultations.html',items=(data or {}).get('items',[]))

def request_slot_reconciliation():
    """Queue a 30-day reconciliation after a successful dashboard availability change.

    The Lambda uses its own current IST date. The daily scheduler remains a
    recovery mechanism if an asynchronous invocation fails after acceptance.
    """
    function_name = os.environ.get('MEDISLOT_MATERIALISER_FUNCTION')
    if not function_name:
        flash('Availability saved, but automatic slot refresh is not configured. Slots will refresh on the next scheduled run.')
        return
    try:
        result = boto3.client('lambda').invoke(
            FunctionName=function_name,
            InvocationType='Event',
            Payload=b'{}',
        )
        if result.get('StatusCode') != 202:
            flash('Availability saved, but slot refresh was not accepted. Please contact support.')
    except (BotoCoreError, ClientError, OSError):
        app.logger.exception('Failed to queue slot reconciliation')
        flash('Availability saved, but slot refresh could not be queued. Please contact support.')


# ====================================================================
# WEEKLY AVAILABILITY
# ====================================================================
@app.route('/doctor/availability',methods=['GET','POST'])
@protected
def availability():
    """List and manage the doctor weekly availability rules."""
    if request.method == 'POST':
        try:
            if request.form.get('delete_id'):
                ok = page_post('DELETE', '/doctors/me/availability/' + request.form['delete_id'], {})
            else:
                item = {
                    'consultation_type_id': int(request.form['consultation_type_id']),
                    'day_of_week': int(request.form['day_of_week']),
                    'start_time': request.form['start_time'],
                    'end_time': request.form['end_time'],
                }
                item_id = request.form.get('item_id', '').strip()
                ok = page_post(
                    'PUT' if item_id else 'POST',
                    '/doctors/me/availability' + ('/' + item_id if item_id else ''),
                    item,
                )
            if ok:
                request_slot_reconciliation()
        except (ValueError, KeyError):
            flash('Please enter valid availability details')
        return redirect(url_for('availability'))

    # Fetch weekly availability rules and consultation types from the backend.
    data=page_get('/doctors/me/availability')
    consults=page_get('/doctors/me/consultation-types')

    # Redirect to login if authentication has expired.
    if data is None or consults is None: return redirect(url_for('login'))

    # Display existing availability rules and consultation options.
    return render_template('availability.html',items=(data or {}).get('items',[]),
                           consultations=(consults or {}).get('items',[]))

# ====================================================================
# DOCTOR BLACKOUT DATES
# ====================================================================
# ====================================================================
# DOCTOR BLACKOUT DATES
# ====================================================================
@app.route('/doctor/blackouts',methods=['GET','POST'])
@protected
def blackouts():
    """List, add, or remove dates when the doctor is unavailable."""
    if request.method=='POST':

        # Delete an existing blackout date if a delete ID is provided.
        if request.form.get('delete_id'):
            page_post('DELETE','/doctors/me/blackouts/'+request.form['delete_id'],{})

        else:
            # Add a new blackout date with an optional reason.
            page_post('POST','/doctors/me/blackouts',{'blackout_date':request.form['blackout_date'],
                'reason':request.form.get('reason','')})

        return redirect(url_for('blackouts'))

    # Fetch existing blackout dates and display them in the HTML page.
    data=page_get('/doctors/me/blackouts')
    return render_template('blackouts.html',items=(data or {}).get('items',[]))


# ====================================================================
# DAY SCHEDULE
# ====================================================================
# ====================================================================
# DAY SCHEDULE
# ====================================================================
@app.route('/doctor/schedule')
@protected
def schedule():
    """Calendar UI and authenticated, per-day slot data for lazy calendar loading."""
    from flask import jsonify
    from zoneinfo import ZoneInfo

    day = request.args.get('date', date.today().isoformat())
    try:
        day = date.fromisoformat(day).isoformat()
    except (ValueError, TypeError):
        if request.args.get('format') == 'json':
            return jsonify({'error': 'Invalid date'}), 400
        day = date.today().isoformat()

    if request.args.get('format') == 'json':
        raw = page_get('/doctors/me/schedule?' + urlencode({'date': day}))
        if raw is None:
            return jsonify({'error': 'Could not load schedule'}), 502
        slots = []
        for item in raw.get('slots', []):
            try:
                start = datetime.fromisoformat(item['startTimeUTC'].replace('Z', '+00:00'))
                end = datetime.fromisoformat(item['endTimeUTC'].replace('Z', '+00:00'))
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                local_start = start.astimezone(ZoneInfo('Asia/Kolkata'))
                local_end = end.astimezone(ZoneInfo('Asia/Kolkata'))
                if local_start.date().isoformat() != day:
                    continue
                slots.append({
                    'slotId': str(item.get('slotId', '')),
                    'start': local_start.strftime('%H:%M'),
                    'end': local_end.strftime('%H:%M'),
                    'consultationTypeId': str(item.get('consultationTypeId', '')),
                    'capacity': int(item.get('capacity', 0)),
                    'bookedCount': int(item.get('bookedCount', 0)),
                    'heldCount': int(item.get('heldCount', 0)),
                    'status': str(item.get('status', 'AVAILABLE')),
                    'appointments': [
                        {'patient_name': str(a.get('patient_name', '')),
                         'status': str(a.get('status', ''))}
                        for a in item.get('appointments', [])
                    ],
                })
            except (KeyError, ValueError, TypeError, OverflowError):
                continue
        return jsonify({'date': day, 'slots': slots})

    consults = page_get('/doctors/me/consultation-types')
    names = {str(x['consultation_type_id']): x['name']
             for x in (consults or {}).get('items', [])}
    return render_template('schedule.html', day=day, consultation_names=names)


# ====================================================================
# PROFILE PHOTO UPLOAD
# ====================================================================
# ====================================================================
# PROFILE PHOTO UPLOAD
# ====================================================================
@app.route('/doctor/photo', methods=['POST'])
@protected
def upload_photo():
    """Validate an uploaded image and send it using a presigned upload form."""

    # Get the uploaded photo and validate its MIME type.
    photo=request.files.get('photo')
    if not photo or photo.mimetype not in ('image/jpeg','image/png','image/webp'):
        flash('Choose a JPEG, PNG or WebP image')
        return redirect(url_for('profile'))

    # Read the photo and ensure its size is between 1 byte and 5 MB.
    data=photo.read(5*1024*1024+1)
    if not 0<len(data)<=5*1024*1024:
        flash('Image must be 5 MB or smaller')
        return redirect(url_for('profile'))

    try:
        # Request a presigned S3 upload form from the Doctor API Lambda.
        presigned=api('POST','/doctors/me/photo',json={'contentType':photo.mimetype,'sizeBytes':len(data)})
        presigned.raise_for_status()

        # Extract the S3 upload URL and required form fields.
        upload=presigned.json()['upload']
        fields=upload['fields']

        # Upload the image directly to S3 using the presigned form.
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
        # Request a presigned photo download URL from the Doctor API Lambda.
        r=api('GET','/doctors/me/photo')
        if not r.ok:
            return '', 404

        # Extract the S3 download URL from the API response.
        url=r.json().get('url')
        if not url:
            return '', 404

        # Download the image directly from S3 using the presigned URL.
        image=requests.get(url,timeout=15,stream=True)
        image.raise_for_status()
        content=image.content

        # Reject images larger than 5 MB.
        if len(content)>5*1024*1024:
            return '', 413

        # Return the image to the browser with caching and security headers.
        return Response(content,content_type=image.headers.get('Content-Type','image/jpeg'),
                        headers={'Cache-Control':'private, max-age=120','X-Content-Type-Options':'nosniff'})

    except (requests.RequestException,ValueError):
        return '', 404
# ====================================================================
# MILESTONE 4: PATIENT PORTAL ON THE SAME EC2 FLASK APPLICATION
# ====================================================================

def patient_protected(fn):
    @wraps(fn)
    def inner(*args, **kwargs):
        if session.get('role') != 'patient' or not session.get('refresh_token'):
            return redirect(url_for('patient_login'))
        if session.get('refresh_expires_at') and int(session['refresh_expires_at']) <= int(datetime.now(timezone.utc).timestamp()):
            session.clear()
            flash('Session expired. Please sign in again.')
            return redirect(url_for('patient_login'))
        return fn(*args, **kwargs)
    return inner

@app.route('/patient/register',methods=['GET','POST'])
def patient_register():
    if request.method=='POST':
        fields=('first_name','last_name','email','password','date_of_birth','gender','phone','city','emergency_contact','notes')
        payload={k:request.form.get(k,'').strip() for k in fields}
        try:
            r=requests.post(API+'/auth/patients/register',json=payload,timeout=15)
            if r.ok:
                flash('Patient registered. Please sign in.')
                return redirect(url_for('patient_login'))
            flash(api_error(r))
        except requests.RequestException:
            flash('Registration service unavailable')
    return render_template('patient_register.html')

@app.route('/patient/login',methods=['GET','POST'])
def patient_login():
    if request.method=='POST':
        try:
            r=requests.post(API+'/auth/patients/login',json={'email':request.form.get('email',''),'password':request.form.get('password','')},timeout=15)
            if r.ok:
                data=r.json()
                if not data.get('accessToken') or not data.get('refreshToken'):
                    flash('Login tokens missing')
                else:
                    session.clear()
                    session.update(role='patient',access_token=data['accessToken'],refresh_token=data['refreshToken'],
                        access_expires_at=data.get('expiresAt',0),refresh_expires_at=data.get('refreshExpiresAt',0))
                    return redirect(url_for('patient_search'))
            else:flash(api_error(r))
        except (requests.RequestException,ValueError):flash('Login unavailable')
    return render_template('patient_login.html')

@app.route('/patient/profile',methods=['GET','POST'])
@patient_protected
def patient_profile():
    if request.method=='POST':
        if request.form.get('delete_profile'):
            r=api('DELETE','/patients/me')
            if r.ok:
                session.clear()
                flash('Profile deleted')
                return redirect(url_for('welcome'))
            flash(api_error(r))
        else:
            fields=('first_name','last_name','date_of_birth','gender','phone','city','emergency_contact','notes')
            r=api('PUT','/patients/me',json={k:request.form.get(k,'').strip() for k in fields})
            flash('Profile saved' if r.ok else api_error(r))
        return redirect(url_for('patient_profile'))
    r=api('GET','/patients/me')
    if not r.ok:
        flash(api_error(r))
        return redirect(url_for('patient_search'))
    return render_template('patient_profile.html',data=r.json())

@app.route('/patient/doctors')
@patient_protected
def patient_search():
    filters={k:request.args.get(k,'').strip() for k in ('name','hospital','city')}
    filters['specialization_id']=request.args.get('specialization_id','').strip()
    page=request.args.get('page','1')
    specializations=load_specializations()
    try:
        params={k:v for k,v in filters.items() if v}
        params.update({'page':page,'page_size':10})
        r=api('GET','/doctors',params=params)
        data=r.json() if r.ok else {}
        if not r.ok:flash(api_error(r))
    except (requests.RequestException,ValueError):
        data={};flash('Doctor search unavailable')
    return render_template('patient_search.html',data=data,filters=filters,
                           specializations=specializations)

@app.route('/patient/doctors/<int:doctor_id>')
@patient_protected
def patient_doctor(doctor_id):
    try:
        r=api('GET',f'/doctors/{doctor_id}')
        if not r.ok:
            flash(api_error(r));return redirect(url_for('patient_search'))
        doctor=r.json()
        start=request.args.get('start_date',date.today().isoformat())
        end=request.args.get('end_date',(date.today()+timedelta(days=7)).isoformat())
        slots_response=api('GET',f'/doctors/{doctor_id}/slots',params={'start_date':start,'end_date':end})
        slots=slots_response.json().get('slots',[]) if slots_response.ok else []
        if not slots_response.ok:flash(api_error(slots_response))

        # Resolve each slot's internal consultation type ID to patient-friendly
        # details from the doctor's public profile response. Keep the ID intact
        # in the slot object for any future booking action; do not display it.
        consultation_types = {
            str(item.get('consultation_type_id')): item
            for item in doctor.get('consultation_types', [])
            if item.get('consultation_type_id') is not None
        }
        for slot in slots:
            local_dt = datetime.fromisoformat(slot['startTimeUTC'].replace('Z','+00:00')).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Kolkata'))
            slot['local_start'] = local_dt.strftime('%d %b %Y, %I:%M %p')
            slot['local_date'] = local_dt.strftime('%Y-%m-%d')
            slot['local_date_label'] = local_dt.strftime('%a, %d %B %Y')
            slot['local_time'] = local_dt.strftime('%I:%M %p')
            slot['free_seats'] = int(slot.get('freeSeats', 0) or 0)
            consultation = consultation_types.get(str(slot.get('consultationTypeId')), {})
            slot['consultation_type_name'] = consultation.get('name', 'Consultation')
            slot['consultation_duration'] = consultation.get('duration_minutes')
            slot['consultation_fee'] = consultation.get('fee')

        return render_template('patient_doctor.html',doctor=doctor,slots=slots,start=start,end=end)
    except (requests.RequestException,ValueError):
        flash('Doctor details unavailable')
        return redirect(url_for('patient_search'))
