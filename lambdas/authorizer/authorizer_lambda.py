# ============================================================
# 1. IMPORTS
# ============================================================

import time
import jwt
from auth_common import *


# ============================================================
# 2. JWT SIGNING SECRET INITIALIZATION
# ============================================================

# Loaded once when the Lambda execution environment starts.
JWT_SIGNING_SECRET = signing_secret()


# ============================================================
# 3. LAMBDA AUTHORIZER HANDLER
# ============================================================

def lambda_handler(event, context):

    # --------------------------------------------------------
    # 4. EXTRACT JWT ACCESS TOKEN
    # --------------------------------------------------------

    token = (
        (event.get('authorizationToken') or '')
        .removeprefix('Bearer ')
        .strip()
    )

    try:

        # ----------------------------------------------------
        # 5. VERIFY JWT ACCESS TOKEN
        # ----------------------------------------------------

        claims = verify_access(token)

        # ----------------------------------------------------
        # 6. CHECK TOKEN REVOCATION IN DYNAMODB
        # ----------------------------------------------------

        if revoked_table().get_item(
            Key={
                'jti': claims['jti']
            },
            ConsistentRead=True
        ).get('Item'):

            raise jwt.InvalidTokenError('Revoked token')

    # --------------------------------------------------------
    # 7. HANDLE INVALID OR REVOKED TOKENS
    # --------------------------------------------------------

    except (jwt.InvalidTokenError, ValueError):
        raise Exception('Unauthorized')

    # --------------------------------------------------------
    # 8. GENERATE IAM ALLOW POLICY
    # --------------------------------------------------------

    # IAM Allow for valid JWT; the protected handler
    # performs role check to return HTTP 403.

    return {
        'principalId': claims['sub'],

        'policyDocument': {
            'Version': '2012-10-17',

            'Statement': [
                {
                    'Action': 'execute-api:Invoke',
                    'Effect': 'Allow',
                    'Resource': event['methodArn']
                }
            ]
        },

        # ----------------------------------------------------
        # 9. PASS USER DETAILS TO PROTECTED LAMBDA
        # ----------------------------------------------------

        'context': {
            'userId': claims['sub'],
            'role': claims['role'],
            'jti': claims['jti']
        }
    }