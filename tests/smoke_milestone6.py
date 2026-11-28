"""Unauthenticated API Gateway route smoke checks after deployment."""
import os
import sys
import requests

base = os.environ.get('MEDISLOT_API_BASE_URL', '').rstrip('/')
if not base:
    print('MEDISLOT_API_BASE_URL is required')
    sys.exit(2)

checks = [
    ('GET', '/patients/me/waitlists', None),
    ('POST', '/slots/__milestone6_smoke_invalid_slot__/waitlist', {}),
    ('DELETE', '/slots/__milestone6_smoke_invalid_slot__/waitlist', {}),
    ('POST', '/waitlist-offers/__invalid_offer__/accept', {}),
    ('PATCH', '/appointments/0', {'slotId': '__invalid_slot__'}),
    ('GET', '/reschedule-executions?executionArn=invalid', None),
]
for method, path, payload in checks:
    response = requests.request(method, base + path, json=payload, timeout=20)
    if response.status_code != 401:
        raise SystemExit(f'{method} {path}: expected 401 from the configured authorizer (a missing API route usually returns 403), got {response.status_code}: {response.text[:300]}')
    print(f'PASS {method} {path}: route reached authorizer ({response.status_code})')
