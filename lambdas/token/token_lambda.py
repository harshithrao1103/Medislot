
import time
# Used to get the current time and check token expiry.

import jwt
# Used to work with JWT access tokens.

from botocore.exceptions import ClientError
# Used for AWS errors. Not directly used in this code.

from auth_common import *
# Imports common functions used for authentication,
# database access, token creation and AWS operations.


# Get the JWT secret from SSM Parameter Store.
# This runs when the Lambda starts.
JWT_SIGNING_SECRET = signing_secret()


def lambda_handler(event, context):

    # Get the request ID to identify this request in logs.
    correlation = cid(event)

    # Get the API URL from the request.
    path = (
        event.get('path')
        or event.get('requestContext', {}).get('path')
        or ''
    )

    try:

        # Convert the request body from JSON into a Python dictionary.
        data = body_json(event)


        # ==================================================
        # 1. REFRESH ACCESS TOKEN
        # ==================================================

        # Check whether the API URL ends with '/refresh'.
        if path.endswith('/refresh'):

            # Get the refresh token sent by the frontend.
            old = data.get('refreshToken')

            # Check whether the refresh token is provided and valid.
            if not isinstance(old, str) or not old:
                return response(
                    400,
                    {'error': 'refreshToken required'},
                    correlation
                )

            # Convert the refresh token into a SHA-256 hash.
            # We stored this hash in DynamoDB during login.
            # We use the hash to find the same token record.
            key = token_hash(old)

            # Get the DynamoDB table that stores refresh tokens.
            table = refresh_table()

            # Search DynamoDB using the token hash.
            # ConsistentRead=True gets the latest saved data.
            item = table.get_item(
                Key={'tokenHash': key},
                ConsistentRead=True
            ).get('Item')

            # Check whether the token exists and has not expired.
            # If it is missing or expired, reject the request.
            if not item or int(item.get('expiresAt', 0)) <= int(time.time()):
                return response(
                    401,
                    {'error': 'Invalid or expired refresh token'},
                    correlation
                )

            # Generate a new access token using the user's ID and role.
            access, expiry = issue_access(
                item['userId'],
                item['role']
            )

            # Save a log showing that the token was refreshed.
            audit(
                'token_refreshed',
                correlation,
                userId=item['userId']
            )

            # Send the new access token back to the frontend.
            # The existing refresh token is not changed or deleted.
            return response(200, {
                'accessToken': access,
                'tokenType': 'Bearer',
                'expiresAt': expiry,
                'refreshExpiresAt': int(item['expiresAt'])
            }, correlation)


        # ==================================================
        # 2. LOGOUT
        # ==================================================

        # Check whether the API URL ends with '/logout'.
        if path.endswith('/logout'):

            # Get the access token from the Authorization header
            # and check whether it is valid.
            # If valid, get its details (user ID, role, expiry, jti).
            claims = verify_access(extract_bearer(event))

            # Check whether this access token is already revoked.
            # We use its unique jti value to search DynamoDB.
            if revoked_table().get_item(
                Key={'jti': claims['jti']},
                ConsistentRead=True
            ).get('Item'):

                # If the token is already revoked, reject the request.
                return response(
                    401,
                    {'error': 'Access token revoked'},
                    correlation
                )

            # Get the refresh token sent by the frontend.
            refresh = data.get('refreshToken')

            # Check whether the refresh token is provided.
            if not isinstance(refresh, str) or not refresh:
                return response(
                    400,
                    {'error': 'refreshToken required'},
                    correlation
                )

            # Convert the refresh token into a hash.
            # We use this hash to find its record in DynamoDB.
            key = token_hash(refresh)

            # Search for the refresh token in DynamoDB.
            item = refresh_table().get_item(
                Key={'tokenHash': key},
                ConsistentRead=True
            ).get('Item')

            # Check whether the access token and refresh token
            # belong to the same user and have the same role.
            #
            # If they belong to different users, reject the request.
            if item and (
                item['userId'] != claims['sub']
                or item['role'] != claims['role']
            ):
                return response(
                    403,
                    {'error': 'Refresh token belongs to another user'},
                    correlation
                )

            # Add the access token's unique ID (jti) to the
            # revoked-token DynamoDB table.
            #
            # This tells the system not to accept this access token again.
            # expiresAt stores when the original JWT expires.
            revoked_table().put_item(
                Item={
                    'jti': claims['jti'],
                    'expiresAt': int(claims['exp'])
                }
            )

            # Delete the refresh token from DynamoDB.
            # It can no longer be used to generate new access tokens.
            refresh_table().delete_item(
                Key={'tokenHash': key}
            )

            # Save a log showing that the user logged out successfully.
            audit(
                'logout_success',
                correlation,
                userId=claims['sub']
            )

            # Send logout success message to the frontend.
            return response(
                200,
                {'message': 'Logged out'},
                correlation
            )


        # ==================================================
        # 3. INVALID API URL
        # ==================================================

        # If the URL is neither '/refresh' nor '/logout',
        # return a 404 Not Found response.
        return response(
            404,
            {'error': 'Not found'},
            correlation
        )


    # ======================================================
    # 4. ERROR HANDLING
    # ======================================================

    # If the JWT is invalid or a ValueError occurs,
    # return an authentication error.
    except (jwt.InvalidTokenError, ValueError):
        return response(
            401,
            {'error': 'Invalid or expired access token'},
            correlation
        )

    # Handle other unexpected errors.
    # Save an error log and return a server error message.
    except Exception:
        audit(
            'token_operation_failure',
            correlation,
            error='internal'
        )

        return response(
            500,
            {'error': 'Internal server error'},
            correlation
        )
