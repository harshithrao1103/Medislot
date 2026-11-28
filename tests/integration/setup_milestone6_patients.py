"""Create disposable patient accounts and export fresh short-lived JWTs for CI.

No patient JWT is stored in GitHub Secrets. Every deployment run registers and
logs in a new set of isolated test patients immediately before Milestone 6 tests.
"""
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
import uuid

BASE = os.environ['MEDISLOT_API_BASE_URL'].rstrip('/')
COUNT = int(os.getenv('M6_TEST_PATIENT_COUNT', '20'))
PASSWORD = os.getenv('M6_TEST_PATIENT_PASSWORD', 'M6-CI-' + secrets.token_urlsafe(24) + '!')


def request(path, body):
    payload = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(
        BASE + path,
        data=payload,
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', errors='replace')
        try:
            detail = json.loads(raw)
        except ValueError:
            detail = raw[:1000]
        raise RuntimeError(f'{path} returned HTTP {exc.code}: {detail}') from exc


def main():
    if COUNT < 8:
        raise SystemExit('M6_TEST_PATIENT_COUNT must be at least 8')
    run_id = uuid.uuid4().hex[:12]
    tokens = []
    for index in range(COUNT):
        email = f'm6-ci-{run_id}-{index}@example.com'
        registration = {
            'first_name': 'MediSlot',
            'last_name': f'CI Patient {index + 1}',
            'email': email,
            'password': PASSWORD,
            'date_of_birth': '1998-05-20',
            'gender': 'Other',
            'phone': f'{9000000000 + index:010d}',
            'city': 'Hyderabad',
            'emergency_contact': f'{9100000000 + index:010d}',
            'notes': 'Disposable automated Milestone 6 integration-test account',
        }
        status, registered = request('/auth/patients/register', registration)
        if status != 201:
            raise RuntimeError(f'Patient registration failed for CI account {index + 1}: {registered}')
        _, login = request('/auth/patients/login', {'email': email, 'password': PASSWORD})
        access = login.get('accessToken')
        if not access:
            raise RuntimeError(f'Login response for CI account {index + 1} did not contain accessToken')
        tokens.append(access)

    env_file = os.getenv('GITHUB_ENV')
    if not env_file:
        raise SystemExit('GITHUB_ENV is not set; run this script from the GitHub Actions workflow')
    with open(env_file, 'a', encoding='utf-8') as handle:
        handle.write('M6_TEST_PATIENT_TOKENS_JSON=' + json.dumps(tokens, separators=(',', ':')) + '\n')
    print(f'Created and authenticated {len(tokens)} disposable patient accounts; fresh access tokens exported to this workflow run.')
    print('JWT values are not printed or written to repository files.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'::error::Milestone 6 patient setup failed: {exc}', file=sys.stderr)
        raise
