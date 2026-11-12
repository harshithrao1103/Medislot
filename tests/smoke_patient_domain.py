"""Deployed milestone 4 checks; creates and deletes two disposable test patients."""
import json, os, secrets, urllib.request, urllib.error
from datetime import date, timedelta
BASE=os.environ['MEDISLOT_API_BASE_URL'].rstrip('/')
def call(path,method='GET',body=None,token=None):
    headers={'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    req=urllib.request.Request(BASE+path,method=method,headers=headers,
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req,timeout=25) as res:return res.status,json.loads(res.read())
    except urllib.error.HTTPError as ex:
        raw=ex.read()
        try: data=json.loads(raw)
        except ValueError:data={'raw':raw.decode(errors='replace')}
        return ex.code,data
suffix=secrets.token_hex(7)
password='MediSlotPatient123!'
accounts=[]
for n in range(2):
    email=f'm4-{suffix}-{n}@example.com'
    payload={'first_name':'Test','last_name':f'Patient {n}','email':email,'password':password,'date_of_birth':'1998-05-20',
        'gender':'Other','phone':'9876543210','city':'Hyderabad','emergency_contact':'9876543211','notes':''}
    status,data=call('/auth/patients/register','POST',payload)
    assert status==201,('register',status,data)
    status,login=call('/auth/patients/login','POST',{'email':email,'password':password})
    assert status==200,('login',status,login)
    accounts.append((data['userId'],login['accessToken']))
try:
    for user_id,token in accounts:
        status,data=call('/patients/me',token=token)
        assert status==200 and str(data['user_id'])==str(user_id),('isolation',status,data)
        status,data=call('/patients/me','PUT',{'city':'Bengaluru'},token)
        assert status==200,('update',status,data)
        status,data=call('/patients/me',token=token)
        assert status==200 and data['city']=='Bengaluru'
        # A supplied ID must never allow reading another patient.
        status,data=call('/patients/'+str(accounts[1][0] if user_id==accounts[0][0] else accounts[0][0]),token=token)
        assert status in (403,404),('cross-patient route',status,data)
    import itertools,urllib.parse
    filters={'name':'Test','hospital':'Hospital','specialization':'General','city':'Hyderabad'}
    for count in range(5):
        for keys in itertools.combinations(filters,count):
            query=urllib.parse.urlencode({k:filters[k] for k in keys}|{'page':1,'page_size':10})
            status,data=call('/doctors?'+query)
            assert status==200 and isinstance(data['items'],list),('search',keys,status,data)
    status,data=call('/doctors?page=1&page_size=10')
    if data['items']:
        did=data['items'][0]['doctor_id']
        status,doctor=call(f'/doctors/{did}')
        assert status==200 and 'consultation_types' in doctor and 'email' not in doctor
        start=date.today()+timedelta(days=1)
        query=urllib.parse.urlencode({'start_date':start.isoformat(),'end_date':(start+timedelta(days=6)).isoformat()})
        status,slots=call(f'/doctors/{did}/slots?'+query)
        assert status==200 and isinstance(slots['slots'],list),(status,slots)
finally:
    for user_id,token in accounts:
        status,data=call('/patients/me','DELETE',token=token)
        assert status==200,('cleanup',status,data)
print('Milestone 4 deployed integration checks passed')
