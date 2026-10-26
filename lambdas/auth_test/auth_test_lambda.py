from auth_common import cid, response, audit

def lambda_handler(event, context):
    correlation = cid(event)
    auth = (event.get('requestContext') or {}).get('authorizer') or {}
    if auth.get('role') != 'doctor':
        return response(403, {'error': 'Doctor role required'}, correlation)
    audit('doctor_access_granted', correlation, userId=auth.get('userId'))
    return response(200, {'message': 'Doctor-only endpoint', 'userId': auth.get('userId')}, correlation)
