"""Milestone 2 offline unit tests (no AWS calls)."""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
import jwt
import bcrypt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lambdas' / '_shared'))
import auth_common as ac


def test_password_policy(monkeypatch):
    monkeypatch.setattr(ac, 'config', lambda key, decrypt=False: '10')
    for password in ('short1', 'abcdefghij', '1234567890', ''):
        with pytest.raises(ValueError):
            ac.validate_registration({'name': 'Test', 'email': 'test@example.org', 'password': password})
    assert ac.validate_registration({'name': ' Test ', 'email': 'Test@Example.org', 'password': 'correctpass1'})[:2] == ('Test', 'test@example.org')


def test_bcrypt_work_factor():
    hashed = bcrypt.hashpw(b'password1234', bcrypt.gensalt(rounds=12))
    assert bcrypt.checkpw(b'password1234', hashed)
    assert not bcrypt.checkpw(b'wrongpassword1', hashed)
    assert hashed.startswith(b'$2') and int(hashed.split(b'$')[2]) >= 12


def test_access_claims_and_signature(monkeypatch):
    monkeypatch.setattr(ac, 'config', lambda key, decrypt=False: {'access-token-minutes': '15', 'jwt-signing-secret': 'unit-test-only-secret'}[key])
    token, expiry = ac.issue_access(17, 'doctor')
    assert set(jwt.decode(token, 'unit-test-only-secret', algorithms=['HS256'])) == {'sub', 'role', 'iat', 'exp', 'jti'}
    assert ac.verify_access(token)['sub'] == '17'
    with pytest.raises(jwt.InvalidTokenError):
        ac.verify_access(token + 'x')
    expired = jwt.encode({'sub': '17', 'role': 'doctor', 'iat': int(time.time())-900, 'exp': int(time.time())-100, 'jti': 'expired'}, 'unit-test-only-secret', algorithm='HS256')
    with pytest.raises(jwt.ExpiredSignatureError):
        ac.verify_access(expired)


def test_refresh_token_hash_is_not_raw():
    assert ac.token_hash('sample') != 'sample'
    assert len(ac.token_hash('sample')) == 64


def test_doctor_role_rejects_patient():
    path = ROOT / 'lambdas' / 'auth_test' / 'auth_test_lambda.py'
    spec = importlib.util.spec_from_file_location('auth_test_lambda', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    event = {'requestContext': {'authorizer': {'role': 'patient', 'userId': '5'}}}
    assert module.lambda_handler(event, None)['statusCode'] == 403
    event['requestContext']['authorizer']['role'] = 'doctor'
    assert module.lambda_handler(event, None)['statusCode'] == 200


def test_authorizer_denies_revoked(monkeypatch):
    path = ROOT / 'lambdas' / 'authorizer' / 'authorizer_lambda.py'
    spec = importlib.util.spec_from_file_location('authorizer_lambda', path)
    module = importlib.util.module_from_spec(spec)
    with patch.object(ac, 'signing_secret', return_value='test-only-secret'):
        spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'verify_access', lambda token: {'sub': '7', 'role': 'doctor', 'jti': 'revoked'})
    monkeypatch.setattr(module, 'revoked_table', lambda: MagicMock(get_item=lambda **kw: {'Item': {'jti': 'revoked'}}))
    with pytest.raises(Exception, match='Unauthorized'):
        module.lambda_handler({'authorizationToken': 'Bearer sample', 'methodArn': 'arn:aws:execute-api:us-east-1:111111111111:abc/dev/GET/doctor-auth-check'}, None)


def test_strict_claims(monkeypatch):
    monkeypatch.setattr(ac, 'config', lambda key, decrypt=False: 'test-only-secret')
    base = {'sub': '5', 'role': 'patient', 'iat': int(time.time()), 'exp': int(time.time()) + 900, 'jti': 'unique'}
    for patch_claims in ({'role': 'admin'}, {'issuer': 'unexpected'}, {'sub': ''}, {'iat': 'not-integer'}):
        claims = {**base, **patch_claims}
        token = jwt.encode(claims, 'test-only-secret', algorithm='HS256')
        with pytest.raises(jwt.InvalidTokenError):
            ac.verify_access(token)

def test_refresh_token_is_random_and_hashed(monkeypatch):
    monkeypatch.setattr(ac, 'config', lambda key, decrypt=False: '7')
    saved = []
    monkeypatch.setattr(ac, 'refresh_table', lambda: MagicMock(put_item=lambda **kwargs: saved.append(kwargs)))
    first, expiry = ac.create_refresh('5', 'patient')
    second, _ = ac.create_refresh('5', 'patient')
    assert first != second
    assert saved[0]['Item']['tokenHash'] == ac.token_hash(first)
    assert first not in repr(saved)
    assert expiry > int(time.time())
