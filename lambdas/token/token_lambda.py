import time
import jwt
from botocore.exceptions import ClientError
from auth_common import *

# Loaded once when the Lambda execution environment starts.
JWT_SIGNING_SECRET = signing_secret()

def lambda_handler(event, context):
    correlation = cid(event)
    path = event.get('path') or event.get('requestContext', {}).get('path') or ''
    try:
        data = body_json(event)
        if path.endswith('/refresh'):
            old = data.get('refreshToken')
            if not isinstance(old, str) or not old:
                return response(400, {'error': 'refreshToken required'}, correlation)
            key = token_hash(old)
            table = refresh_table()
            # Atomic consume prevents concurrent refresh replays; no tokens in logs.
            try:
                result = table.delete_item(Key={'tokenHash': key}, ConditionExpression='attribute_exists(tokenHash) AND expiresAt > :now', ExpressionAttributeValues={':now': int(time.time())}, ReturnValues='ALL_OLD')
            except ClientError as ex:
                if ex.response['Error']['Code'] == 'ConditionalCheckFailedException':
                    return response(401, {'error': 'Invalid or expired refresh token'}, correlation)
                raise
            item = result['Attributes']
            access, expiry = issue_access(item['userId'], item['role'])
            new_refresh, refresh_expiry = create_refresh(item['userId'], item['role'])
            audit('token_refreshed', correlation, userId=item['userId'])
            return response(200, {'accessToken': access, 'refreshToken': new_refresh, 'tokenType': 'Bearer', 'expiresAt': expiry, 'refreshExpiresAt': refresh_expiry}, correlation)
        if path.endswith('/logout'):
            claims = verify_access(extract_bearer(event))
            if revoked_table().get_item(Key={'jti': claims['jti']}, ConsistentRead=True).get('Item'):
                return response(401, {'error': 'Access token revoked'}, correlation)
            refresh = data.get('refreshToken')
            if not isinstance(refresh, str) or not refresh:
                return response(400, {'error': 'refreshToken required'}, correlation)
            key = token_hash(refresh)
            item = refresh_table().get_item(Key={'tokenHash': key}, ConsistentRead=True).get('Item')
            if item and (item['userId'] != claims['sub'] or item['role'] != claims['role']):
                return response(403, {'error': 'Refresh token belongs to another user'}, correlation)
            # Denylist before refresh deletion: once logout succeeds, JWT is unusable.
            revoked_table().put_item(Item={'jti': claims['jti'], 'expiresAt': int(claims['exp'])})
            refresh_table().delete_item(Key={'tokenHash': key})
            audit('logout_success', correlation, userId=claims['sub'])
            return response(200, {'message': 'Logged out'}, correlation)
        return response(404, {'error': 'Not found'}, correlation)
    except (jwt.InvalidTokenError, ValueError):
        return response(401, {'error': 'Invalid or expired access token'}, correlation)
    except Exception:
        audit('token_operation_failure', correlation, error='internal')
        return response(500, {'error': 'Internal server error'}, correlation)
