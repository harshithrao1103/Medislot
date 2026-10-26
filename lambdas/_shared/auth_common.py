"""Shared MediSlot Milestone 2 authentication helpers."""
import hashlib
import json
import logging
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone

import boto3
import bcrypt
import jwt
import pymysql
from botocore.exceptions import ClientError

log = logging.getLogger('medislot')
log.setLevel(logging.INFO)
_ssm = boto3.client('ssm')
_sm = boto3.client('secretsmanager')
_ddb = boto3.resource('dynamodb')
_config = {}


def config(key, decrypt=False):
    # Lambda module globals are populated on cold start, not per request.
    if key not in _config:
        _config[key] = _ssm.get_parameter(Name=os.environ['AUTH_SSM_PREFIX'] + '/' + key, WithDecryption=decrypt)['Parameter']['Value']
    return _config[key]


def signing_secret():
    return config('jwt-signing-secret', True)


def response(status, body, correlation=None):
    return {'statusCode': status, 'headers': {'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'X-Correlation-ID': correlation or ''}, 'body': json.dumps(body)}


def cid(event):
    headers = {k.lower(): v for k, v in (event.get('headers') or {}).items()}
    candidate = headers.get('x-correlation-id', '')
    return candidate if re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', candidate) else str(uuid.uuid4())


def audit(action, correlation, **fields):
    log.info(json.dumps({'event': action, 'correlationId': correlation, **fields}, separators=(',', ':')))


def body_json(event):
    try:
        body = json.loads(event.get('body') or '{}')
        if not isinstance(body, dict):
            raise ValueError('Object required')
        return body
    except (ValueError, TypeError):
        raise ValueError('Invalid JSON request body')


def validate_registration(data):
    name = data.get('name')
    email = data.get('email')
    password = data.get('password')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError('Name must be 1-100 characters')
    if not isinstance(email, str) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email.strip()) or len(email) > 255:
        raise ValueError('Invalid email address')
    if not isinstance(password, str) or len(password) < int(config('password-min-length')) or len(password.encode('utf-8')) > 72 or not re.search(r'[A-Za-z]', password) or not re.search(r'[0-9]', password):
        raise ValueError('Password must contain at least 10 characters, one letter and one digit')
    return name.strip(), email.strip().lower(), password


def db_connection():
    secret = json.loads(_sm.get_secret_value(SecretId=os.environ['DB_SECRET_ARN'])['SecretString'])
    return pymysql.connect(host=os.environ['DB_HOST'], port=int(os.environ['DB_PORT']), user=secret['username'], password=secret['password'], database=os.environ['DB_NAME'], connect_timeout=8, read_timeout=8, write_timeout=8, autocommit=False, cursorclass=pymysql.cursors.DictCursor)


def register(event, role):
    correlation = cid(event)
    try:
        data = body_json(event)
        name, email, password = validate_registration(data)
        specialization = data.get('specialization')
        if role == 'doctor' and (not isinstance(specialization, str) or not specialization.strip() or len(specialization) > 100):
            raise ValueError('Doctor specialization is required (maximum 100 characters)')
        cost = int(config('bcrypt-work-factor'))
        if cost < 12:
            raise ValueError('Configured bcrypt work factor must be at least 12')
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(rounds=cost)).decode('ascii')
    except ValueError as ex:
        return response(400, {'error': str(ex)}, correlation)
    connection = None
    try:
        connection = db_connection()
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO users (name,email,password_hash,role) VALUES (%s,%s,%s,%s)', (name, email, password_hash, role.upper()))
            user_id = cursor.lastrowid
            if role == 'doctor':
                cursor.execute('INSERT INTO doctors (user_id,specialization) VALUES (%s,%s)', (user_id, specialization.strip()))
            else:
                cursor.execute('INSERT INTO patients (user_id) VALUES (%s)', (user_id,))
        connection.commit()
        audit('registration_success', correlation, role=role, userId=str(user_id))
        return response(201, {'userId': str(user_id), 'role': role}, correlation)
    except pymysql.err.IntegrityError:
        if connection:
            connection.rollback()
        return response(409, {'error': 'Email already registered'}, correlation)
    except Exception:
        if connection:
            connection.rollback()
        audit('registration_failure', correlation, error='internal')
        return response(500, {'error': 'Internal server error'}, correlation)
    finally:
        if connection:
            connection.close()


def issue_access(user_id, role):
    now = int(time.time())
    expires = now + int(config('access-token-minutes')) * 60
    claims = {'sub': str(user_id), 'role': role, 'iat': now, 'exp': expires, 'jti': str(uuid.uuid4())}
    return jwt.encode(claims, signing_secret(), algorithm='HS256'), expires


def verify_access(token):
    claims = jwt.decode(token, signing_secret(), algorithms=['HS256'], options={'require': ['sub', 'role', 'iat', 'exp', 'jti']}, leeway=0)
    if (set(claims) != {'sub', 'role', 'iat', 'exp', 'jti'}
            or claims['role'] not in ('doctor', 'patient')
            or not isinstance(claims['sub'], str) or not claims['sub']
            or not isinstance(claims['jti'], str) or not claims['jti']
            or type(claims['iat']) is not int or type(claims['exp']) is not int
            or claims['exp'] <= claims['iat']):
        raise jwt.InvalidTokenError('Invalid claims')
    return claims


def token_hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def refresh_table():
    return _ddb.Table(os.environ['REFRESH_TABLE'])


def revoked_table():
    return _ddb.Table(os.environ['REVOKED_TABLE'])


def create_refresh(user_id, role):
    token = secrets.token_urlsafe(48)
    expires = int(time.time()) + int(config('refresh-token-days')) * 86400
    refresh_table().put_item(Item={'tokenHash': token_hash(token), 'userId': str(user_id), 'role': role, 'expiresAt': expires}, ConditionExpression='attribute_not_exists(tokenHash)')
    return token, expires


def extract_bearer(event):
    headers = {k.lower(): v for k, v in (event.get('headers') or {}).items()}
    value = headers.get('authorization', '')
    if not isinstance(value, str) or not value.startswith('Bearer '):
        raise ValueError('Bearer access token required')
    return value[7:]
