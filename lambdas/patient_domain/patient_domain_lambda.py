"""Patient domain Lambda: patient profile, public doctor discovery, and slot lookup.

This function does not create, update, or cancel appointments.
"""

import datetime as dt
import json
import logging
import os
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key

from auth_common import body_json, cid, db_connection, response


class BadRequest(Exception):
    """API error containing the HTTP status and client-facing message."""

    def __init__(self, status, message):
        self.status = status
        self.message = message


def result(status, data, correlation):
    """Build a standard API response."""
    return response(status, data, correlation)


def positive(value, name):
    """Validate a value that must be a positive integer."""
    try:
        number = int(value)

        if number < 1 or isinstance(value, bool):
            raise ValueError

        return number

    except (TypeError, ValueError):
        raise BadRequest(400, "Invalid " + name)


def text(value, name, limit, optional=False):
    """Trim and validate text input, enforcing its maximum length."""
    if optional and value in (None, ""):
        return ""

    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > limit
    ):
        raise BadRequest(400, "Invalid " + name)

    return value.strip()


def patient_identity(event):
    """Get the patient ID from the trusted authorizer context."""
    context = (event.get("requestContext") or {}).get("authorizer") or {}

    if str(context.get("role", "")).lower() != "patient":
        raise BadRequest(403, "Patient role required")

    return positive(context.get("userId"), "authorized identity")


def patient_profile(event, method, correlation):
    """Get, update, or delete the signed-in patient's profile."""
    user_id = patient_identity(event)
    conn = db_connection()  

    try:
        with conn.cursor() as cur:

            # GET: return account details and patient profile fields.
            if method == "GET":
                cur.execute(
                    """
                    SELECT
                        u.user_id,
                        u.name,
                        u.first_name,
                        u.last_name,
                        u.email,
                        p.date_of_birth,
                        p.gender,
                        p.phone,
                        p.city,
                        p.emergency_contact,
                        p.notes
                    FROM users AS u
                    JOIN patients AS p
                        ON p.user_id = u.user_id
                    WHERE
                        u.user_id = %s
                        AND u.role = 'PATIENT'
                    """,
                    (user_id,),
                )

                item = cur.fetchone()

                if not item:
                    raise BadRequest(404, "Patient not found")

                item["date_of_birth"] = item["date_of_birth"].isoformat()

                return result(200, item, correlation)

            # PUT: validate the submitted fields and update allowed columns.
            if method == "PUT":
                data = body_json(event)

                if not isinstance(data, dict):
                    raise BadRequest(400, "Invalid JSON object")

                allowed_fields = {
                    "name",
                    "first_name",
                    "last_name",
                    "date_of_birth",
                    "gender",
                    "phone",
                    "city",
                    "emergency_contact",
                    "notes",
                }

                if not data or any(
                    field not in allowed_fields for field in data
                ):
                    raise BadRequest(400, "Invalid profile fields")

                # Split the full name only when first_name/last_name were
                # not supplied separately.
                if "name" in data and not {
                    "first_name",
                    "last_name",
                } & set(data):
                    name_parts = text(data["name"], "name", 100).split(
                        None, 1
                    )

                    data["first_name"] = name_parts[0]
                    data["last_name"] = (
                        name_parts[1] if len(name_parts) > 1 else ""
                    )

                # Update the account name when either name component changes.
                if "first_name" in data or "last_name" in data:
                    cur.execute(
                        """
                        SELECT first_name, last_name
                        FROM users
                        WHERE
                            user_id = %s
                            AND role = 'PATIENT'
                        """,
                        (user_id,),
                    )

                    current = cur.fetchone()

                    if not current:
                        raise BadRequest(404, "Patient not found")

                    first_name = text(
                        data.get("first_name", current["first_name"]),
                        "first_name",
                        50,
                    )
                    last_name = text(
                        data.get("last_name", current["last_name"]),
                        "last_name",
                        50,
                    )

                    cur.execute(
                        """
                        UPDATE users
                        SET
                            first_name = %s,
                            last_name = %s,
                            name = %s
                        WHERE
                            user_id = %s
                            AND role = 'PATIENT'
                        """,
                        (
                            first_name,
                            last_name,
                            f"{first_name} {last_name}",
                            user_id,
                        ),
                    )

                # Build the patient-profile UPDATE from fields in the request.
                updates = []
                args = []

                field_limits = [
                    ("gender", 30),
                    ("phone", 20),
                    ("city", 100),
                    ("emergency_contact", 100),
                    ("notes", 2000),
                ]

                for field, limit in field_limits:
                    if field in data:
                        updates.append(field + "=%s")
                        args.append(
                            text(
                                data[field],
                                field,
                                limit,
                                optional=(field == "notes"),
                            )
                        )

                # Validate the date separately because it must be an ISO date
                # and cannot be in the future.
                if "date_of_birth" in data:
                    try:
                        date_of_birth = dt.date.fromisoformat(
                            data["date_of_birth"]
                        )

                        if date_of_birth > dt.date.today():
                            raise ValueError

                    except (TypeError, ValueError):
                        raise BadRequest(400, "Invalid date_of_birth")

                    updates.append("date_of_birth=%s")
                    args.append(date_of_birth)

                if updates:
                    cur.execute(
                        "UPDATE patients SET "
                        + ", ".join(updates)
                        + " WHERE user_id = %s",
                        (*args, user_id),
                    )

                conn.commit()

                return result(
                    200,
                    {"message": "Profile updated"},
                    correlation,
                )

            # DELETE: do not delete a patient who has existing appointments.
            if method == "DELETE":
                cur.execute(
                    """
                    SELECT 1
                    FROM appointments
                    WHERE patient_id = %s
                    LIMIT 1
                    """,
                    (user_id,),
                )

                if cur.fetchone():
                    raise BadRequest(
                        409,
                        "Profile has appointments; contact support",
                    )

                cur.execute(
                    """
                    DELETE FROM users
                    WHERE
                        user_id = %s
                        AND role = 'PATIENT'
                    """,
                    (user_id,),
                )

                if not cur.rowcount:
                    raise BadRequest(404, "Patient not found")

                conn.commit()

                return result(
                    200,
                    {"message": "Profile deleted; please sign out"},
                    correlation,
                )

        raise BadRequest(405, "Method not allowed")

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def escape_like(value):
    """Escape LIKE wildcard characters in a user-provided search value."""
    return (
        value.replace(chr(92), chr(92) * 2)
        .replace("%", chr(92) + "%")
        .replace("_", chr(92) + "_")
    )


def search(event, correlation):
    """Search the public doctor directory with filters and pagination."""
    query_params = event.get("queryStringParameters") or {}

    page = positive(query_params.get("page", 1), "page")
    page_size = positive(query_params.get("page_size", 10), "page_size")

    if page_size > 50 or page > 10000:
        raise BadRequest(400, "Pagination limit exceeded")

    clauses = []
    args = []

    # Optional doctor-name search across first name, last name, and full name.
    if query_params.get("name"):
        name = text(query_params["name"], "name", 100)
        pattern = "%" + escape_like(name) + "%"

        clauses.append(
            """
            (
                LOWER(u.first_name) LIKE LOWER(%s) ESCAPE '\\\\'
                OR LOWER(u.last_name) LIKE LOWER(%s) ESCAPE '\\\\'
                OR LOWER(CONCAT(u.first_name, ' ', u.last_name))
                    LIKE LOWER(%s) ESCAPE '\\\\'
                OR LOWER(CONCAT(u.last_name, ' ', u.first_name))
                    LIKE LOWER(%s) ESCAPE '\\\\'
                OR LOWER(u.name) LIKE LOWER(%s) ESCAPE '\\\\'
            )
            """
        )

        args.extend([pattern] * 5)

    # Optional hospital and city filters.
    for field, sql_column in (
        ("hospital", "d.hospital_name"),
        ("city", "d.city"),
    ):
        if query_params.get(field):
            value = text(query_params[field], field, 100)

            clauses.append(sql_column + " LIKE %s")
            args.append("%" + escape_like(value) + "%")

    # Prefer specialization_id when both specialization filters are supplied.
    if query_params.get("specialization_id"):
        specialization_id = positive(
            query_params["specialization_id"],
            "specialization_id",
        )

        clauses.append('d.specialization_id=%s')
        args.append(specialization_id)

    elif query_params.get("specialization"):
        specialization = text(
            query_params["specialization"],
            "specialization",
            100,
        )

        clauses.append("s.specialization_name LIKE %s")
        args.append("%" + escape_like(specialization) + "%")

    # Combine all active filters with AND.
    where_clause = (
        " WHERE " + " AND ".join(clauses)
        if clauses
        else ""
    )

    from_clause = """
        FROM doctors AS d
        JOIN users AS u
            ON u.user_id = d.user_id
        JOIN specializations AS s
            ON s.specialization_id = d.specialization_id
    """

    conn = db_connection()

    try:
        with conn.cursor() as cur:
            # First query: count all doctors matching the selected filters.
            cur.execute(
                "SELECT COUNT(*) AS total"
                + from_clause
                + where_clause,
                tuple(args),
            )

            total = cur.fetchone()["total"]

            # Second query: fetch only the requested page of doctors.
            cur.execute(
                """
                SELECT
                    d.user_id AS doctor_id,
                    u.name,
                    u.first_name,
                    u.last_name,
                    d.hospital_name,
                    d.city,
                    s.specialization_name AS specialization,
                    d.experience_years,
                    d.qualifications
                """
                + from_clause
                + where_clause
                + """
                ORDER BY u.name, u.user_id
                LIMIT %s OFFSET %s
                """,
                (*args, page_size, (page - 1) * page_size),
            )

            items = cur.fetchall()

        total_pages = (total + page_size - 1) // page_size

        return result(
            200,
            {
                "items": items,
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
            correlation,
        )

    finally:
        conn.close()


def public_doctor(doctor_id, correlation):
    """Fetch a doctor's public profile and consultation types."""
    conn = db_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    d.user_id AS doctor_id,
                    u.name,
                    u.first_name,
                    u.last_name,
                    d.hospital_name,
                    d.clinic_address,
                    d.city,
                    d.experience_years,
                    d.qualifications,
                    d.languages,
                    d.timezone,
                    d.photo_key,
                    s.specialization_name AS specialization
                FROM doctors AS d
                JOIN users AS u
                    ON u.user_id = d.user_id
                JOIN specializations AS s
                    ON s.specialization_id = d.specialization_id
                WHERE d.user_id = %s
                """,
                (doctor_id,),
            )

            doctor = cur.fetchone()

            if not doctor:
                raise BadRequest(404, "Doctor not found")

            if isinstance(doctor["languages"], str):
                doctor["languages"] = json.loads(doctor["languages"])

            cur.execute(
                """
                SELECT
                    consultation_type_id,
                    name,
                    duration_minutes,
                    fee,
                    capacity
                FROM consultation_types
                WHERE doctor_id = %s
                ORDER BY consultation_type_id
                """,
                (doctor_id,),
            )

            consultations = cur.fetchall()

            for consultation in consultations:
                consultation["fee"] = str(consultation["fee"])

            doctor["consultation_types"] = consultations

            # Keep the S3 object key private in the database. Return a short-lived
            # URL so the patient browser can display the private profile photo.
            photo_key = doctor.pop("photo_key", None)
            doctor["profile_photo_url"] = None
            if photo_key:
                doctor["profile_photo_url"] = boto3.client("s3").generate_presigned_url(
                    "get_object",
                    Params={
                        "Bucket": os.environ["PHOTO_BUCKET"],
                        "Key": photo_key,
                    },
                    ExpiresIn=300,
                )

            return result(200, doctor, correlation)

    finally:
        conn.close()


def slots(doctor_id, event, correlation):
    """Return future doctor slots with available capacity."""

    # 1. Read query parameters
    query_params = event.get("queryStringParameters") or {}

    # 2. Validate requested dates
    try:
        start_date = dt.date.fromisoformat(query_params["start_date"])
        end_date = dt.date.fromisoformat(query_params["end_date"])

    except (KeyError, ValueError, TypeError):
        raise BadRequest(
            400,
            "start_date and end_date must be YYYY-MM-DD",
        )

    # Allow a maximum date range of 30 days.
    if end_date < start_date or (end_date - start_date).days > 30:
        raise BadRequest(400, "Date range must be at most 30 days")

    # 3. Check dates using Indian timezone
    india_timezone = ZoneInfo("Asia/Kolkata")

    # Reject past dates.
    if start_date < dt.datetime.now(india_timezone).date():
        raise BadRequest(400, "Past dates are not available")

    # 4. Read optional consultation type ID
    consultation_type_id = (
        positive(
            query_params["consultation_type_id"],
            "consultation_type_id",
        )
        if query_params.get("consultation_type_id")
        else None
    )

    # 5. Verify that the doctor exists in MySQL
    conn = db_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM doctors WHERE user_id = %s",
                (doctor_id,),
            )

            if not cur.fetchone():
                raise BadRequest(404, "Doctor not found")

    finally:
        # Always close the database connection.
        conn.close()

    # 6. Convert the requested IST dates into UTC
    # Start from midnight of the first requested date.
    first_utc = (
        dt.datetime.combine(
            start_date,
            dt.time.min,
            tzinfo=india_timezone,
        )
        .astimezone(dt.timezone.utc)
        .isoformat()
    )

    # Use midnight of the next day to include the entire end date.
    last_utc = (
        dt.datetime.combine(
            end_date + dt.timedelta(days=1),
            dt.time.min,
            tzinfo=india_timezone,
        )
        .astimezone(dt.timezone.utc)
        .isoformat()
    )

    # 7. Connect to the DynamoDB slots table
    table = boto3.resource("dynamodb").Table(os.environ["SLOTS_TABLE"])

    # 8. Prepare the DynamoDB query
    query = {
        # Use the index to search by doctor and start time.
        "IndexName": "DoctorStartIndex",

        "KeyConditionExpression": (
            Key("doctorId").eq(str(doctor_id))
            & Key("startTimeUTC").between(first_utc, last_utc)
        ),

        # Retrieve up to 100 items per query page.
        "Limit": 100,
    }

    # Store all matching slots.
    items = []

    # 9. Retrieve slots using pagination
    while True:
        data = table.query(**query)

        # Process each slot returned by DynamoDB.
        for slot in data.get("Items", []):

            # Accept only patient-visible statuses.
            if slot.get("status") not in ("AVAILABLE", "ACTIVE"):
                continue

            # Filter by consultation type if provided.
            if (
                consultation_type_id is not None
                and str(slot.get("consultationTypeId"))
                != str(consultation_type_id)
            ):
                continue

            # Convert the slot's UTC start time into a datetime.
            start_time = dt.datetime.fromisoformat(
                slot["startTimeUTC"].replace("Z", "+00:00")
            )

            # Skip slots that have already started.
            if start_time.astimezone(dt.timezone.utc) <= dt.datetime.now(
                dt.timezone.utc
            ):
                continue

            # Convert the slot time into Indian local date.
            local_date = start_time.astimezone(india_timezone).date()

            # Keep only slots within the requested date range.
            if not start_date <= local_date <= end_date:
                continue

            # 10. Calculate available capacity
            capacity = int(slot.get("capacity", 0))
            booked_count = int(slot.get("bookedCount", 0))
            held_count = int(slot.get("heldCount", 0))

            # Available seats = capacity - booked - held.
            free_seats = max(
                0,
                capacity - booked_count - held_count,
            )

            # Skip slots that are completely full.
            if free_seats <= 0:
                continue

            # 11. Add the valid slot to the result list
            items.append(
                {
                    "slotId": slot["slotId"],
                    "consultationTypeId": slot["consultationTypeId"],
                    "startTimeUTC": slot["startTimeUTC"],
                    "endTimeUTC": slot["endTimeUTC"],
                    "capacity": int(slot["capacity"]),
                    "bookedCount": booked_count,
                    "heldCount": held_count,
                    "freeSeats": free_seats,
                }
            )

        # 12. Check whether another DynamoDB page exists as limit is 100 items per query. If not, exit the loop.
        if "LastEvaluatedKey" not in data:
            break

        # Continue from the last retrieved item.
        query["ExclusiveStartKey"] = data["LastEvaluatedKey"]

    # 13. Sort slots by starting time (earliest first).
    items.sort(key=lambda slot: slot["startTimeUTC"])

    # 14. Return the available slots
    return result(
        200,
        {
            "doctor_id": doctor_id,
            "slots": items,
        },
        correlation,
    )



def lambda_handler(event, context):
    """Route API Gateway requests to the correct function."""

    # Generate a unique request correlation ID.
    correlation = cid(event)

    try:
        # Get the requested API endpoint and HTTP method.
        resource = event.get("resource", "")
        method = event.get("httpMethod", "")

        # Patient profile: GET, PUT or DELETE
        if resource == "/patients/me" and method in ("GET", "PUT", "DELETE"):
            return patient_profile(event, method, correlation)

        # Search for doctors: GET
        if resource == "/doctors" and method == "GET":
            return search(event, correlation)

        # Get a doctor's public profile: GET
        if resource == "/doctors/{doctorId}" and method == "GET":
            doctor_id = positive(
                (event.get("pathParameters") or {}).get("doctorId"),
                "doctorId",
            )

            return public_doctor(doctor_id, correlation)

        # Get a doctor's available slots: GET
        if resource == "/doctors/{doctorId}/slots" and method == "GET":
            doctor_id = positive(
                (event.get("pathParameters") or {}).get("doctorId"),
                "doctorId",
            )

            return slots(doctor_id, event, correlation)

        # Return 404 if no matching endpoint is found.
        raise BadRequest(404, "Route not found")

    # Handle application-specific errors.
    except BadRequest as exc:
        return result(
            exc.status,
            {"error": exc.message},
            correlation,
        )

    # Handle invalid values.
    except ValueError:
        return result(
            400,
            {"error": "Invalid request"},
            correlation,
        )

    # Handle unexpected errors and log their details.
    except Exception:
        logging.exception(
            "Patient domain request failed correlationId=%s",
            correlation,
        )

        return result(
            500,
            {"error": "Internal server error"},
            correlation,
        )
