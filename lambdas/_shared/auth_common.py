
"""Shared MediSlot Milestone 2 authentication helpers."""

# ============================================================
# 1. IMPORTS
# ============================================================

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


# ============================================================
# 2. LOGGING AND AWS CLIENT INITIALIZATION
# ============================================================

log = logging.getLogger('medislot')
log.setLevel(logging.INFO)

_ssm = boto3.client('ssm')
_sm = boto3.client('secretsmanager')
_ddb = boto3.resource('dynamodb')

_config = {}


# ============================================================
# 3. SSM PARAMETER STORE CONFIGURATION
# ============================================================

def config(key, decrypt=False):
    # Lambda module globals are populated on cold start, not per request.
    if key not in _config:
        _config[key] = _ssm.get_parameter(
            Name=os.environ['AUTH_SSM_PREFIX'] + '/' + key,
            WithDecryption=decrypt
        )['Parameter']['Value']

    return _config[key]


# ============================================================
# 4. JWT SIGNING SECRET
# ============================================================

def signing_secret():
    return config('jwt-signing-secret', True)


# ============================================================
# 5. HTTP RESPONSE HELPER
# ============================================================

def response(status, body, correlation=None):
    return {
        'statusCode': status,
        'headers': {
            'Content-Type': 'application/json',
            'Cache-Control': 'no-store',
            'X-Correlation-ID': correlation or ''
        },
        'body': json.dumps(body)
    }


# ============================================================
# 6. CORRELATION ID GENERATION
# ============================================================

def cid(event):
    headers = {
        k.lower(): v
        for k, v in (event.get('headers') or {}).items()
    }

    candidate = headers.get('x-correlation-id', '')

    return (
        candidate
        if re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', candidate)
        else str(uuid.uuid4())
    )


# ============================================================
# 7. AUDIT LOGGING
# ============================================================

def audit(action, correlation, **fields):
    log.info(
        json.dumps(
            {
                'event': action,
                'correlationId': correlation,
                **fields
            },
            separators=(',', ':')
        )
    )


# ============================================================
# 8. REQUEST BODY JSON PARSING
# ============================================================

def body_json(event):
    try:
        body = json.loads(event.get('body') or '{}')

        if not isinstance(body, dict):
            raise ValueError('Object required')

        return body

    except (ValueError, TypeError):
        raise ValueError('Invalid JSON request body')


# ============================================================
# 9. USER REGISTRATION VALIDATION
# ============================================================

def validate_registration(data):
    name = data.get('name')
    email = data.get('email')
    password = data.get('password')

    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError('Name must be 1-100 characters')

    if (
        not isinstance(email, str)
        or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email.strip())
        or len(email) > 255
    ):
        raise ValueError('Invalid email address')

    if (
        not isinstance(password, str)
        or len(password) < int(config('password-min-length'))
        or len(password.encode('utf-8')) > 72
        or not re.search(r'[A-Za-z]', password)
        or not re.search(r'[0-9]', password)
    ):
        raise ValueError(
            'Password must contain at least 10 characters, one letter and one digit'
        )

    return name.strip(), email.strip().lower(), password


# ============================================================
# 10. RDS MYSQL DATABASE CONNECTION
# ============================================================

def db_connection():
    secret = json.loads(
        _sm.get_secret_value(
            SecretId=os.environ['DB_SECRET_ARN']
        )['SecretString']
    )

    return pymysql.connect(
        host=os.environ['DB_HOST'],
        port=int(os.environ['DB_PORT']),
        user=secret['username'],
        password=secret['password'],
        database=os.environ['DB_NAME'],
        connect_timeout=8,
        read_timeout=8,
        write_timeout=8,
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor
    )


# ============================================================
# 11. DOCTOR / PATIENT REGISTRATION
# ============================================================

def _required_text(data, key, limit):
    value = data.get(key)

    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f'{key} is required (1-{limit} characters)')

    return value.strip()


def _registration_profile(data, role):
    """Validate profile fields before opening the registration transaction."""

    if role == 'doctor':
        fields = {
            key: _required_text(data, key, limit)
            for key, limit in (
                ('licence_number', 100),
                ('qualifications', 65535), ('hospital_name', 200),
                ('clinic_address', 500), ('city', 100), ('timezone', 64)
            )
        }

        fields['specialization_id'] = data.get('specialization_id')
        if type(fields['specialization_id']) is not int or fields['specialization_id'] < 1:
            raise ValueError('Select a valid specialization')

        # Medical licence number format: MED-YYYY-NNNNNN
        # Example: MED-2026-123456
        if not re.fullmatch(
            r'MED-[0-9]{4}-[0-9]{6}',
            fields['licence_number']
        ):
            raise ValueError(
                'licence_number must follow MED-YYYY-NNNNNN'
            )

        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(fields['timezone'])
        except (KeyError, ValueError):
            raise ValueError('timezone must be a valid IANA timezone')

        years = data.get('experience_years')

        if type(years) is not int or not 0 <= years <= 100:
            raise ValueError('experience_years must be an integer from 0 to 100')

        languages = data.get('languages')

        if (
            not isinstance(languages, list)
            or not languages
            or any(
                not isinstance(v, str)
                or not v.strip()
                or len(v) > 100
                for v in languages
            )
        ):
            raise ValueError(
                'languages must be a non-empty list of language names'
            )

        photo_key = data.get('photo_key')

        if photo_key is not None and (
            not isinstance(photo_key, str)
            or len(photo_key) > 512
        ):
            raise ValueError(
                'photo_key must be a string up to 512 characters'
            )

        fields.update(
            experience_years=years,
            languages=json.dumps(languages),
            photo_key=photo_key
        )

        return fields

    fields = {
        key: _required_text(data, key, limit)
        for key, limit in (
            ('gender', 30),
            ('phone', 20),
            ('city', 100),
            ('emergency_contact', 100)
        )
    }

    dob = data.get('date_of_birth')

    try:
        from datetime import date
        birth_date = date.fromisoformat(dob) if isinstance(dob, str) else None
    except ValueError:
        birth_date = None

    if birth_date is None or birth_date > date.today():
        raise ValueError(
            'date_of_birth must be a valid YYYY-MM-DD date not in the future'
        )

    notes = data.get('notes')

    if notes is not None and (
        not isinstance(notes, str)
        or len(notes) > 65535
    ):
        raise ValueError('notes must be a string')

    fields.update(
        date_of_birth=dob,
        notes=notes
    )

    return fields


def register(event, role):
    correlation = cid(event)

    try:
        data = body_json(event)
        name, email, password = validate_registration(data)
        profile = _registration_profile(data, role)

        cost = int(config('bcrypt-work-factor'))

        if cost < 12:
            raise ValueError(
                'Configured bcrypt work factor must be at least 12'
            )

        password_hash = bcrypt.hashpw(
            password.encode('utf-8'),
            bcrypt.gensalt(rounds=cost)
        ).decode('ascii')

    except ValueError as ex:
        return response(400, {'error': str(ex)}, correlation)

    connection = None

    try:
        connection = db_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO users (name,email,password_hash,role) VALUES (%s,%s,%s,%s)',
                (name, email, password_hash, role.upper())
            )

            user_id = cursor.lastrowid

            if role == 'doctor':
                cursor.execute('SELECT specialization_id FROM specializations WHERE specialization_id=%s', (profile['specialization_id'],))
                if cursor.fetchone() is None:
                    connection.rollback()
                    return response(400, {'error': 'Invalid specialization_id'}, correlation)
                cursor.execute(
                    """INSERT INTO doctors
                    (user_id,licence_number,specialization_id,experience_years,qualifications,
                     hospital_name,clinic_address,city,languages,timezone,photo_key)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        user_id,
                        profile['licence_number'],
                        profile['specialization_id'],
                        profile['experience_years'],
                        profile['qualifications'],
                        profile['hospital_name'],
                        profile['clinic_address'],
                        profile['city'],
                        profile['languages'],
                        profile['timezone'],
                        profile['photo_key']
                    )
                )
            else:
                cursor.execute(
                    """INSERT INTO patients
                    (user_id,date_of_birth,gender,phone,city,emergency_contact,notes)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        user_id,
                        profile['date_of_birth'],
                        profile['gender'],
                        profile['phone'],
                        profile['city'],
                        profile['emergency_contact'],
                        profile['notes']
                    )
                )

        connection.commit()

        audit(
            'registration_success',
            correlation,
            role=role,
            userId=str(user_id)
        )

        return response(
            201,
            {'userId': str(user_id), 'role': role},
            correlation
        )

    except pymysql.err.IntegrityError:
        if connection:
            connection.rollback()

        return response(
            409,
            {'error': 'Email or unique profile field already registered'},
            correlation
        )

    except Exception:
        if connection:
            connection.rollback()

        audit('registration_failure', correlation, error='internal')

        return response(
            500,
            {'error': 'Internal server error'},
            correlation
        )

    finally:
        if connection:
            connection.close()


# ============================================================
# 12. JWT ACCESS TOKEN GENERATION
# ============================================================

def issue_access(user_id, role):
    now = int(time.time())

    expires = now + int(config('access-token-minutes')) * 60

    claims = {
        'sub': str(user_id),
        'role': role,
        'iat': now,
        'exp': expires,
        'jti': str(uuid.uuid4())
    }

    return jwt.encode(
        claims,
        signing_secret(),
        algorithm='HS256'
    ), expires


# ============================================================
# 13. JWT ACCESS TOKEN VERIFICATION
# ============================================================

def verify_access(token):
    claims = jwt.decode(
        token,
        signing_secret(),
        algorithms=['HS256'],
        options={
            'require': ['sub', 'role', 'iat', 'exp', 'jti']
        },
        leeway=0
    )

    if (
        set(claims) != {'sub', 'role', 'iat', 'exp', 'jti'}
        or claims['role'] not in ('doctor', 'patient')
        or not isinstance(claims['sub'], str)
        or not claims['sub']
        or not isinstance(claims['jti'], str)
        or not claims['jti']
        or type(claims['iat']) is not int
        or type(claims['exp']) is not int
        or claims['exp'] <= claims['iat']
    ):
        raise jwt.InvalidTokenError('Invalid claims')

    return claims


# ============================================================
# 14. TOKEN HASHING USING SHA-256
# ============================================================

def token_hash(token):
    return hashlib.sha256(
        token.encode('utf-8')
    ).hexdigest()


# ============================================================
# 15. DYNAMODB REFRESH TOKEN TABLE
# ============================================================

def refresh_table():
    return _ddb.Table(os.environ['REFRESH_TABLE'])


# ============================================================
# 16. DYNAMODB REVOKED TOKEN TABLE
# ============================================================

def revoked_table():
    return _ddb.Table(os.environ['REVOKED_TABLE'])


# ============================================================
# 17. REFRESH TOKEN GENERATION AND STORAGE
# ============================================================

def create_refresh(user_id, role):
    token = secrets.token_urlsafe(48)

    expires = (
        int(time.time())
        + int(config('refresh-token-days')) * 86400
    )

    refresh_table().put_item(
        Item={
            'tokenHash': token_hash(token),
            'userId': str(user_id),
            'role': role,
            'createdAt': int(time.time()),
            'expiresAt': expires
        },
        ConditionExpression='attribute_not_exists(tokenHash)'
    )

    return token, expires


# ============================================================
# 18. EXTRACT BEARER TOKEN FROM AUTHORIZATION HEADER
# ============================================================

def extract_bearer(event):
    headers = {
        k.lower(): v
        for k, v in (event.get('headers') or {}).items()
    }

    value = headers.get('authorization', '')

    if not isinstance(value, str) or not value.startswith('Bearer '):
        raise ValueError('Bearer access token required')

    return value[7:]
