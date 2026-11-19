
"""Deployed milestone 4 checks; creates and deletes two disposable test patients."""
import json
import os
import secrets
import urllib.request
import urllib.error
import itertools
import urllib.parse
from datetime import date, timedelta

BASE = os.environ['MEDISLOT_API_BASE_URL'].rstrip('/')


def call(path, method='GET', body=None, token=None):
    headers = {'Content-Type': 'application/json'}

    if token:
        headers['Authorization'] = 'Bearer ' + token

    req = urllib.request.Request(
        BASE + path,
        method=method,
        headers=headers,
        data=json.dumps(body).encode() if body is not None else None
    )

    try:
        with urllib.request.urlopen(req, timeout=25) as res:
            return res.status, json.loads(res.read())

    except urllib.error.HTTPError as ex:
        raw = ex.read()
        try:
            data = json.loads(raw)
        except ValueError:
            data = {'raw': raw.decode(errors='replace')}

        return ex.code, data


suffix = secrets.token_hex(7)
password = 'MediSlotPatient123!'
accounts = []

try:
    # ------------------------------------------------------------
    # 1. Register and login two disposable patients
    # ------------------------------------------------------------
    for n in range(2):
        email = f'm4-{suffix}-{n}@example.com'

        payload = {
            'first_name': 'Test',
            'last_name': f'Patient {n}',
            'email': email,
            'password': password,
            'date_of_birth': '1998-05-20',
            'gender': 'Other',
            'phone': '9876543210',
            'city': 'Hyderabad',
            'emergency_contact': '9876543211',
            'notes': ''
        }

        status, data = call(
            '/auth/patients/register',
            'POST',
            payload
        )
        assert status == 201, ('register', status, data)

        user_id = data['userId']

        status, login = call(
            '/auth/patients/login',
            'POST',
            {'email': email, 'password': password}
        )
        assert status == 200, ('login', status, login)

        accounts.append((user_id, login['accessToken']))

    # ------------------------------------------------------------
    # 2. Patient profile, update, and isolation checks
    # ------------------------------------------------------------
    for index, (user_id, token) in enumerate(accounts):

        # Verify own profile
        status, data = call('/patients/me', token=token)

        assert status == 200 and str(data['user_id']) == str(user_id), (
            'isolation', status, data
        )

        # Both first_name and last_name are mandatory for profile updates.
        update_payload = {
            'first_name': 'Updated',
            'last_name': f'Patient {index}',
            'city': 'Bengaluru'
        }

        status, data = call(
            '/patients/me',
            'PUT',
            update_payload,
            token
        )

        assert status == 200, ('update', status, data)

        # Verify updated profile details
        status, data = call('/patients/me', token=token)

        assert status == 200, ('profile after update', status, data)
        assert data['city'] == 'Bengaluru', ('city update', data)
        assert data['first_name'] == 'Updated', ('first_name update', data)
        assert data['last_name'] == f'Patient {index}', (
            'last_name update', data
        )
        assert data['name'] == f'Updated Patient {index}', (
            'combined name update', data
        )

        # A supplied ID must never allow reading another patient.
        other_user_id = (
            accounts[1][0]
            if str(user_id) == str(accounts[0][0])
            else accounts[0][0]
        )

        status, data = call(
            '/patients/' + str(other_user_id),
            token=token
        )

        assert status in (403, 404), (
            'cross-patient route', status, data
        )

    # ------------------------------------------------------------
    # 3. Doctor search filter combinations
    # ------------------------------------------------------------
    filters = {
        'name': 'Test',
        'hospital': 'Hospital',
        'specialization': 'General',
        'city': 'Hyderabad'
    }

    for count in range(5):
        for keys in itertools.combinations(filters, count):
            query_params = {
                key: filters[key] for key in keys
            } | {
                'page': 1,
                'page_size': 10
            }

            query = urllib.parse.urlencode(query_params)

            status, data = call('/doctors?' + query)

            assert status == 200 and isinstance(data['items'], list), (
                'search', keys, status, data
            )

    # ------------------------------------------------------------
    # 4. Doctor details and available slots
    # ------------------------------------------------------------
    status, data = call('/doctors?page=1&page_size=10')

    assert status == 200, ('doctor listing', status, data)

    if data['items']:
        doctor_id = data['items'][0]['doctor_id']

        status, doctor = call(f'/doctors/{doctor_id}')

        assert status == 200, ('doctor details', status, doctor)
        assert 'consultation_types' in doctor
        assert 'email' not in doctor

        start = date.today() + timedelta(days=1)

        query = urllib.parse.urlencode({
            'start_date': start.isoformat(),
            'end_date': (start + timedelta(days=6)).isoformat()
        })

        status, slots = call(
            f'/doctors/{doctor_id}/slots?' + query
        )

        assert status == 200 and isinstance(slots['slots'], list), (
            'doctor slots', status, slots
        )

finally:
    # ------------------------------------------------------------
    # 5. Delete disposable test patients
    # ------------------------------------------------------------
    for user_id, token in accounts:
        status, data = call(
            '/patients/me',
            'DELETE',
            token=token
        )

        assert status == 200, ('cleanup', status, data)


print('Milestone 4 deployed integration checks passed')
