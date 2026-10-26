"""Live Milestone 2 smoke tests. Set MEDISLOT_API_BASE_URL (no trailing slash)."""
import json
import os
import secrets
import urllib.error
import urllib.request

BASE = os.environ['MEDISLOT_API_BASE_URL'].rstrip('/')

def call(path, method='POST', body=None, access=None):
    headers = {'Content-Type': 'application/json'}
    if access:
        headers['Authorization'] = 'Bearer ' + access
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as ex:
        return ex.code, json.loads(ex.read())

suffix = secrets.token_hex(6)
password = 'Milestone2Secure123'
for role in ('doctors', 'patients'):
    body = {'name': 'CI Test', 'email': f'm2-{role}-{suffix}@example.invalid', 'password': password}
    if role == 'doctors':
        body['specialization'] = 'General Medicine'
    status, data = call(f'/auth/{role}/register', body=body)
    assert status == 201, (role, status, data)
    status, data = call(f'/auth/{role}/login', body={'email': body['email'], 'password': password})
    assert status == 200, (role, status, data)
    assert 'accessToken' in data and 'refreshToken' in data
    access, refresh = data['accessToken'], data['refreshToken']
    status, _ = call('/doctor-auth-check', method='GET', access=access)
    assert status == (200 if role == 'doctors' else 403), (role, status)
    status, _ = call('/doctor-auth-check', method='GET', access=access + 'tampered')
    assert status == 401, (role, status)
    status, rotated = call('/auth/refresh', body={'refreshToken': refresh})
    assert status == 200, (role, status, rotated)
    status, _ = call('/auth/refresh', body={'refreshToken': refresh})
    assert status == 401, (role, status)
    status, _ = call('/auth/logout', body={'refreshToken': rotated['refreshToken']}, access=rotated['accessToken'])
    assert status == 200, (role, status)
    status, _ = call('/doctor-auth-check', method='GET', access=rotated['accessToken'])
    assert status == 401, (role, status)
print('Live authentication smoke tests passed (both roles, refresh, tamper, role enforcement, logout).')
