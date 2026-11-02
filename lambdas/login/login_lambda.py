import bcrypt
from auth_common import *


# ============================================================
# JWT CONFIGURATION
# ============================================================

# Load the JWT signing secret once per Lambda execution environment.
JWT_SIGNING_SECRET = signing_secret()


# ============================================================
# LAMBDA HANDLER — DOCTOR / PATIENT LOGIN
# ============================================================

def lambda_handler(event, context):
    correlation = cid(event)

    try:
        # ----------------------------------------------------
        # 1. PARSE REQUEST BODY
        # ----------------------------------------------------

        data = body_json(event)

        email = data.get("email")
        password = data.get("password")

        # ----------------------------------------------------
        # 2. IDENTIFY USER ROLE FROM API ROUTE
        # ----------------------------------------------------

        path = (
            event.get("path")
            or event.get("requestContext", {}).get("path")
            or ""
        )

        role = path.split("/auth/")[-1].split("/")[0]

        if role not in ("doctors", "patients"):
            return response(
                400,
                {"error": "Invalid login route"},
                correlation
            )

        # Convert doctors -> doctor, patients -> patient
        role = role[:-1]

        # ----------------------------------------------------
        # 3. VALIDATE LOGIN INPUT
        # ----------------------------------------------------

        if not isinstance(email, str) or not isinstance(password, str):
            return response(
                400,
                {"error": "Email and password required"},
                correlation
            )

        # ----------------------------------------------------
        # 4. FETCH USER FROM RDS MYSQL
        # ----------------------------------------------------

        connection = db_connection()

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT user_id, password_hash, role
                    FROM users
                    WHERE email = %s
                    """,
                    (email.strip().lower(),)
                )

                user = cursor.fetchone()

        finally:
            connection.close()

        # ----------------------------------------------------
        # 5. VERIFY USER CREDENTIALS
        # ----------------------------------------------------

        if (
            not user
            or user["role"] != role.upper()
            or not bcrypt.checkpw(
                password.encode(),
                user["password_hash"].encode()
            )
        ):
            audit(
                "login_denied",
                correlation,
                role=role
            )

            return response(
                401,
                {"error": "Invalid credentials"},
                correlation
            )

        # ----------------------------------------------------
        # 6. GENERATE JWT ACCESS TOKEN
        # ----------------------------------------------------

        access, expiry = issue_access(
            user["user_id"],
            role
        )

        # ----------------------------------------------------
        # 7. GENERATE REFRESH TOKEN
        # ----------------------------------------------------

        refresh, refresh_expiry = create_refresh(
            user["user_id"],
            role
        )

        # ----------------------------------------------------
        # 8. AUDIT SUCCESSFUL LOGIN
        # ----------------------------------------------------

        audit(
            "login_success",
            correlation,
            role=role,
            userId=str(user["user_id"])
        )

        # ----------------------------------------------------
        # 9. RETURN ACCESS AND REFRESH TOKENS
        # ----------------------------------------------------

        return response(
            200,
            {
                "accessToken": access,
                "refreshToken": refresh,
                "tokenType": "Bearer",
                "expiresAt": expiry,
                "refreshExpiresAt": refresh_expiry
            },
            correlation
        )

    # --------------------------------------------------------
    # 10. HANDLE INVALID REQUESTS
    # --------------------------------------------------------

    except ValueError as ex:
        return response(
            400,
            {"error": str(ex)},
            correlation
        )

    # --------------------------------------------------------
    # 11. HANDLE INTERNAL SERVER ERRORS
    # --------------------------------------------------------

    except Exception:
        audit(
            "login_failure",
            correlation,
            error="internal"
        )

        return response(
            500,
            {"error": "Internal server error"},
            correlation
        )