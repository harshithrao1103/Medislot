

"""Shared MediSlot Milestone 2 authentication helpers."""

# ============================================================

# 1. IMPORTS

# ============================================================

# hashlib creates a fixed-length hash from a value. We use it to hash refresh tokens.
import hashlib

import json

import logging

import os

import re

# secrets generates secure random values, such as refresh tokens.
import secrets

import time

import uuid

from datetime import datetime, timezone  # Imported here but not directly used in the shown helper functions.

# boto3 lets this Lambda call AWS services such as SSM, Secrets Manager and DynamoDB.
import boto3

# bcrypt safely hashes passwords before they are saved in MySQL.
import bcrypt

# jwt creates and checks JWT access tokens.
import jwt

# pymysql is used to connect to the RDS MySQL database.
import pymysql

from botocore.exceptions import ClientError  # Imported for AWS error handling; not directly used in this file.

# ============================================================

# 2. LOGGING AND AWS CLIENT INITIALIZATION

# ============================================================

# Create a logger so this module can write messages to CloudWatch Logs.
log = logging.getLogger('medislot')

log.setLevel(logging.INFO)

# AWS client used to read configuration values from Systems Manager Parameter Store.
_ssm = boto3.client('ssm')

# AWS client used to retrieve the RDS username and password from Secrets Manager.
_sm = boto3.client('secretsmanager')

# DynamoDB resource used to access the refresh-token and revoked-token tables.
_ddb = boto3.resource('dynamodb')

# Keep configuration values in memory so they do not need to be fetched from SSM every time.
_config = {}

# ============================================================

# 3. SSM PARAMETER STORE CONFIGURATION

# ============================================================

def config(key, decrypt=False):
    # Read a setting from SSM only the first time it is requested.
    # AUTH_SSM_PREFIX tells us which group of parameters belongs to this environment.

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
    # Get the JWT signing secret from SSM.
    # decrypt=True tells AWS to decrypt a SecureString parameter.

    return config('jwt-signing-secret', True)

# ============================================================

# 5. HTTP RESPONSE HELPER

# ============================================================

def response(status, body, correlation=None):
    # Build the response format expected by API Gateway.

    return {

        'statusCode': status,

        'headers': {

            'Content-Type': 'application/json',

            'Cache-Control': 'no-store',  # Tell clients and proxies not to cache authentication responses.

            'X-Correlation-ID': correlation or ''

        },

        'body': json.dumps(body)

    }

# ============================================================

# 6. CORRELATION ID GENERATION

# ============================================================

def cid(event):
    # Use the correlation ID sent with the request if it is safe.
    # Otherwise, create a new unique ID for tracing this request.

    headers = {

        k.lower(): v

        for k, v in (event.get('headers') or {}).items()

    }

    candidate = headers.get('x-correlation-id', '')  # Read the caller's correlation ID, if provided.

    return (

        candidate

        if re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', candidate)

        else str(uuid.uuid4())

    )

# ============================================================

# 7. AUDIT LOGGING

# ============================================================

def audit(action, correlation, **fields):
    # Write a structured JSON log entry to CloudWatch Logs.
    # **fields allows each caller to add extra details such as userId or role.

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
    # Read the request body and turn its JSON text into a Python dictionary.

    try:

        body = json.loads(event.get('body') or '{}')

        if not isinstance(body, dict):
            # The API expects a JSON object, not a list, number or string.

            raise ValueError('Object required')

        return body

    except (ValueError, TypeError):

        raise ValueError('Invalid JSON request body')

# ============================================================

# 9. USER REGISTRATION VALIDATION

# ============================================================

def validate_registration(data):
    # Check the common registration fields used by both doctors and patients.

    # New clients submit separate first_name and last_name fields. The legacy

    # name field remains accepted temporarily for older API clients.

    first_name = data.get('first_name')  # Read the first name supplied by the frontend.

    last_name = data.get('last_name')

    legacy_name = data.get('name')  # Older clients may still send one full-name field.

    if first_name is None and last_name is None and isinstance(legacy_name, str):

        parts = legacy_name.strip().split(None, 1)  # Split the old full name into first and last name.

        first_name = parts[0] if parts else ''

        last_name = parts[1] if len(parts) > 1 else ''

    if not isinstance(first_name, str) or not 1 <= len(first_name.strip()) <= 50:

        raise ValueError('First name must be 1-50 characters')

    if not isinstance(last_name, str) or not 1 <= len(last_name.strip()) <= 50:

        raise ValueError('Last name must be 1-50 characters')

    first_name, last_name = first_name.strip(), last_name.strip()

    name = f'{first_name} {last_name}'

    if len(name) > 100:

        raise ValueError('Full name must not exceed 100 characters')

    # Read credentials from the registration payload before validating them.

    email = data.get('email')  # Read the email address from the request.

    password = data.get('password')  # Read the plain password; it will be hashed before storage.

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

    return first_name, last_name, email.strip().lower(), password

# ============================================================

# 10. RDS MYSQL DATABASE CONNECTION

# ============================================================

def db_connection():
    # Get the database login details from Secrets Manager and connect to RDS MySQL.

    secret = json.loads(

        _sm.get_secret_value(

            SecretId=os.environ['DB_SECRET_ARN']  # ARN of the secret containing the database credentials.

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

        autocommit=False,  # Changes must be committed explicitly; errors can be rolled back.

        cursorclass=pymysql.cursors.DictCursor  # Query results are returned as dictionaries using column names.

    )

# ============================================================

# 11. DOCTOR / PATIENT REGISTRATION

# ============================================================

def _required_text(data, key, limit):
    # Check that a required text field exists and is within its allowed length.

    value = data.get(key)

    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:

        raise ValueError(f'{key} is required (1-{limit} characters)')

    return value.strip()

def _registration_profile(data, role):
    # Validate fields that are specific to a doctor or patient.

    """Validate profile fields before opening the registration transaction."""

    if role == 'doctor':
                # Save the doctor's extra profile details in the doctors table.
        # Doctor registration requires medical and clinic information.

        fields = {

            key: _required_text(data, key, limit)

            for key, limit in (

                ('licence_number', 100),

                ('qualifications', 65535), ('hospital_name', 200),

                ('clinic_address', 500), ('city', 100), ('timezone', 64)

            )

        }

        fields['specialization_id'] = data.get('specialization_id')  # Read the selected specialization ID.

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

            ZoneInfo(fields['timezone'])  # Check that the timezone name is a real IANA timezone.

        except (KeyError, ValueError):

            raise ValueError('timezone must be a valid IANA timezone')

        years = data.get('experience_years')

        if type(years) is not int or not 0 <= years <= 100:

            raise ValueError('experience_years must be an integer from 0 to 100')

        languages = data.get('languages')  # Doctors must provide at least one language.

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

        fields.update(  # Add the remaining validated doctor fields to the profile.

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

    dob = data.get('date_of_birth')  # Read the patient's date of birth.

    try:

        from datetime import date

        birth_date = date.fromisoformat(dob) if isinstance(dob, str) else None  # Convert YYYY-MM-DD text into a date.

    except ValueError:

        birth_date = None

    if birth_date is None or birth_date > date.today():

        raise ValueError(

            'date_of_birth must be a valid YYYY-MM-DD date not in the future'

        )

    notes = data.get('notes')  # Notes are optional for a patient.

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
    # Shared registration process used by both Doctor Register and Patient Register Lambdas.

    correlation = cid(event)

    try:

        data = body_json(event)

        first_name, last_name, email, password = validate_registration(data)  # Validate common account details.

        profile = _registration_profile(data, role)  # Validate the doctor/patient-specific details.

        cost = int(config('bcrypt-work-factor'))  # Read the bcrypt hashing strength from SSM.

        if cost < 12:

            raise ValueError(

                'Configured bcrypt work factor must be at least 12'

            )

        # Convert the password into a bcrypt hash. The original password is not stored in MySQL.
        password_hash = bcrypt.hashpw(

            password.encode('utf-8'),

            bcrypt.gensalt(rounds=cost)

        ).decode('ascii')

    except ValueError as ex:

        return response(400, {'error': str(ex)}, correlation)

    connection = None

    try:

        connection = db_connection()  # Open a connection to the RDS MySQL database.

        with connection.cursor() as cursor:

            cursor.execute(

                'INSERT INTO users (name,first_name,last_name,email,password_hash,role) VALUES (%s,%s,%s,%s,%s,%s)',

                (f'{first_name} {last_name}', first_name, last_name, email, password_hash, role.upper())

            )

            user_id = cursor.lastrowid  # Get the ID generated for the new users table row.

            if role == 'doctor':

                cursor.execute('SELECT specialization_id FROM specializations WHERE specialization_id=%s', (profile['specialization_id'],))

                if cursor.fetchone() is None:

                    connection.rollback()  # Undo database changes made by this registration attempt.

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

        connection.commit()  # Save all inserts permanently in the database.

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
        # A unique value (such as email or licence number) already exists.

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
    # Create a signed JWT access token for the logged-in user.

    now = int(time.time())  # Current time as a Unix timestamp in seconds.

    expires = now + int(config('access-token-minutes')) * 60  # Convert the configured token lifetime from minutes to seconds.

    claims = {

        'sub': str(user_id),  # Subject: identifies the user who owns this token.

        'role': role,  # Stores whether the user is a doctor or patient.

        'iat': now,  # Time when the token was created.

        'exp': expires,  # Time when the token stops being valid.

        'jti': str(uuid.uuid4())  # Unique ID for this individual access token.

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
    # Check the JWT signature, expiry and required claims, then return its data.

    claims = jwt.decode(

        token,

        signing_secret(),

        algorithms=['HS256'],  # Accept only tokens signed with the HS256 algorithm.

        options={

            'require': ['sub', 'role', 'iat', 'exp', 'jti']  # All these claims must be present.

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
    # Create a SHA-256 hash so the original refresh token does not need to be stored.

    return hashlib.sha256(

        token.encode('utf-8')

    ).hexdigest()

# ============================================================

# 15. DYNAMODB REFRESH TOKEN TABLE

# ============================================================

def refresh_table():
    # Return a Boto3 reference to the refresh-token DynamoDB table.

    return _ddb.Table(os.environ['REFRESH_TABLE'])

# ============================================================

# 16. DYNAMODB REVOKED TOKEN TABLE

# ============================================================

def revoked_table():
    # Return a Boto3 reference to the revoked-token DynamoDB table.

    return _ddb.Table(os.environ['REVOKED_TABLE'])

# ============================================================

# 17. REFRESH TOKEN GENERATION AND STORAGE

# ============================================================

def create_refresh(user_id, role):
    # Generate a refresh token, store only its hash in DynamoDB, and return the original token.

    token = secrets.token_urlsafe(48)  # Generate a hard-to-guess random refresh token.

    expires = (

        int(time.time())

        + int(config('refresh-token-days')) * 86400  # Convert the configured number of days into seconds.

    )

    refresh_table().put_item(

        Item={

            'tokenHash': token_hash(token),  # Store the hash, not the original refresh token.

            'userId': str(user_id),

            'role': role,

            'createdAt': int(time.time()),

            'expiresAt': expires

        },

        ConditionExpression='attribute_not_exists(tokenHash)'  # Insert only if this key does not already exist; do not overwrite an item.

    )

    return token, expires

# ============================================================

# 18. EXTRACT BEARER TOKEN FROM AUTHORIZATION HEADER

# ============================================================

def extract_bearer(event):
    # Read the Authorization header and return only the JWT after 'Bearer '.

    headers = {

        k.lower(): v

        for k, v in (event.get('headers') or {}).items()

    }

    value = headers.get('authorization', '')

    if not isinstance(value, str) or not value.startswith('Bearer '):  # Require the Bearer token format.

        raise ValueError('Bearer access token required')

    return value[7:]  # Remove the seven characters in 'Bearer ' and return the token.
