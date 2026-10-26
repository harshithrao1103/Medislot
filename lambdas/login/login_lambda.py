import bcrypt
from auth_common import *

# Loaded once when the Lambda execution environment starts.
JWT_SIGNING_SECRET = signing_secret()

def lambda_handler(event, context):
    correlation = cid(event)
    try:
        data = body_json(event)
        email, password = data.get('email'), data.get('password')
        role = (event.get('path') or event.get('requestContext', {}).get('path') or '').split('/auth/')[-1].split('/')[0]
        if role not in ('doctors', 'patients'):
            return response(400, {'error': 'Invalid login route'}, correlation)
        role = role[:-1]  # doctors -> doctor, patients -> patient
        if not isinstance(email, str) or not isinstance(password, str):
            return response(400, {'error': 'Email and password required'}, correlation)
        connection = db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT user_id, password_hash, role FROM users WHERE email=%s', (email.strip().lower(),))
                user = cursor.fetchone()
        finally:
            connection.close()
        if not user or user['role'] != role.upper() or not bcrypt.checkpw(password.encode(), user['password_hash'].encode()):
            audit('login_denied', correlation, role=role)
            return response(401, {'error': 'Invalid credentials'}, correlation)
        access, expiry = issue_access(user['user_id'], role)
        refresh, refresh_expiry = create_refresh(user['user_id'], role)
        audit('login_success', correlation, role=role, userId=str(user['user_id']))
        return response(200, {'accessToken': access, 'refreshToken': refresh, 'tokenType': 'Bearer', 'expiresAt': expiry, 'refreshExpiresAt': refresh_expiry}, correlation)
    except ValueError as ex:
        return response(400, {'error': str(ex)}, correlation)
    except Exception:
        audit('login_failure', correlation, error='internal')
        return response(500, {'error': 'Internal server error'}, correlation)
