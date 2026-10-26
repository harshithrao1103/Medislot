import time
import jwt
from auth_common import *

# Loaded once when the Lambda execution environment starts.
JWT_SIGNING_SECRET = signing_secret()

def lambda_handler(event, context):
    token = (event.get('authorizationToken') or '').removeprefix('Bearer ').strip()
    try:
        claims = verify_access(token)
        if revoked_table().get_item(Key={'jti': claims['jti']}, ConsistentRead=True).get('Item'):
            raise jwt.InvalidTokenError('Revoked token')
    except (jwt.InvalidTokenError, ValueError):
        raise Exception('Unauthorized')
    # IAM Allow for valid JWT; the protected handler performs role check to return HTTP 403.
    return {'principalId': claims['sub'], 'policyDocument': {'Version': '2012-10-17', 'Statement': [{'Action': 'execute-api:Invoke', 'Effect': 'Allow', 'Resource': event['methodArn']}]}, 'context': {'userId': claims['sub'], 'role': claims['role'], 'jti': claims['jti']}}
