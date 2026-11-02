"""MediSlot doctor portal. All business data is fetched from API Gateway."""
import os
import json
import secrets
from functools import wraps
from datetime import date
import requests
from flask import Flask, render_template, request, redirect, session, url_for, flash
from flask_session import Session

app = Flask(__name__)
app.config.update(SECRET_KEY=os.environ['FLASK_SECRET_KEY'], SESSION_TYPE='filesystem',
                  SESSION_FILE_DIR='/var/lib/medislot/sessions', SESSION_PERMANENT=False,
                  SESSION_USE_SIGNER=True, SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE','false')=='true')
Session(app)
API = os.environ['MEDISLOT_API_BASE_URL'].rstrip('/')

def api(method, path, **kwargs):
    headers = kwargs.pop('headers', {})
    if session.get('access_token'):
        headers['Authorization'] = 'Bearer ' + session['access_token']
    return requests.request(method, API + path, headers=headers, timeout=15, **kwargs)

def protected(fn):
    @wraps(fn)
    def inner(*args, **kwargs):
        if session.get('role') != 'doctor' or not session.get('access_token'):
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return inner

def csrf():
    if not session.get('csrf'):
        session['csrf'] = secrets.token_urlsafe(32)
    return session['csrf']
app.jinja_env.globals['csrf_token'] = csrf

@app.before_request
def check_csrf():
    if request.method == 'POST' and request.form.get('csrf_token') != csrf():
        return 'Invalid CSRF token', 403

@app.route('/')
def index():
    return redirect(url_for('profile') if session.get('role')=='doctor' else url_for('login'))

@app.route('/doctor/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        try:
            r=api('POST','/auth/doctors/login',json={'email':request.form['email'],'password':request.form['password']})
            data=r.json()
            if r.ok:
                session.clear()
                session['access_token']=data.get('accessToken') or data.get('access_token')
                session['refresh_token']=data.get('refreshToken') or data.get('refresh_token')
                if not session['access_token']:
                    flash('Login response did not include access token')
                else:
                    session['role']='doctor'
                    return redirect(url_for('profile'))
            else:flash(data.get('error','Login failed'))
        except requests.RequestException:flash('API unavailable')
    return render_template('login.html')

@app.route('/doctor/logout', methods=['POST'])
def logout():
    try:
        api('POST','/auth/logout',json={'refreshToken':session.get('refresh_token')})
    except requests.RequestException:pass
    session.clear()
    return redirect(url_for('login'))

def page_get(path):
    try:
        r=api('GET',path)
        if r.status_code==401:
            session.clear()
            return None
        if not r.ok:
            flash(r.json().get('error','Request failed'))
            return {}
        return r.json()
    except requests.RequestException:
        flash('API unavailable')
        return {}

def page_post(method,path,payload):
    try:
        r=api(method,path,json=payload)
        flash('Saved successfully' if r.ok else r.json().get('error','Request failed'))
        return r.ok
    except requests.RequestException:
        flash('API unavailable')
        return False

@app.route('/doctor/profile',methods=['GET','POST'])
@protected
def profile():
    if request.method=='POST':
        payload={key:request.form[key] for key in ('name','specialization','qualifications','hospital_name','clinic_address','city')}
        payload['experience_years']=int(request.form['experience_years'])
        payload['languages']=[x.strip() for x in request.form['languages'].split(',') if x.strip()]
        page_post('PUT','/doctors/me',payload)
        return redirect(url_for('profile'))
    data=page_get('/doctors/me')
    if data is None:return redirect(url_for('login'))
    return render_template('profile.html',data=data)

@app.route('/doctor/consultations',methods=['GET','POST'])
@protected
def consultations():
    if request.method=='POST':
        if request.form.get('delete_id'):
            page_post('DELETE','/doctors/me/consultation-types/'+request.form['delete_id'],{})
        else:
            payload={'name':request.form['name'],'duration_minutes':int(request.form['duration_minutes']),
                     'fee':request.form['fee'],'capacity':int(request.form['capacity'])}
            item_id=request.form.get('item_id')
            page_post('PUT' if item_id else 'POST','/doctors/me/consultation-types'+('/'+item_id if item_id else ''),payload)
        return redirect(url_for('consultations'))
    data=page_get('/doctors/me/consultation-types')
    return render_template('consultations.html',items=(data or {}).get('items',[]))

@app.route('/doctor/availability',methods=['GET','POST'])
@protected
def availability():
    if request.method=='POST':
        try:patterns=json.loads(request.form['patterns'])
        except ValueError:
            flash('Invalid JSON availability patterns')
            return redirect(url_for('availability'))
        page_post('PUT','/doctors/me/availability',{'patterns':patterns})
        return redirect(url_for('availability'))
    data=page_get('/doctors/me/availability')
    return render_template('availability.html',patterns=json.dumps((data or {}).get('items',[]),indent=2,default=str))

@app.route('/doctor/blackouts',methods=['GET','POST'])
@protected
def blackouts():
    if request.method=='POST':
        if request.form.get('delete_id'):
            page_post('DELETE','/doctors/me/blackouts/'+request.form['delete_id'],{})
        else:
            page_post('POST','/doctors/me/blackouts',{'blackout_date':request.form['blackout_date'],
                'reason':request.form.get('reason','')})
        return redirect(url_for('blackouts'))
    data=page_get('/doctors/me/blackouts')
    return render_template('blackouts.html',items=(data or {}).get('items',[]))

@app.route('/doctor/schedule')
@protected
def schedule():
    day=request.args.get('date',date.today().isoformat())
    data=page_get('/doctors/me/schedule?date='+day)
    return render_template('schedule.html',data=data or {},day=day)

@app.route('/doctor/photo', methods=['POST'])
@protected
def upload_photo():
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
