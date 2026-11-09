"""Milestone 3 doctor-only API. Authorization identity always comes from Lambda authorizer."""
import json
import os
import re
import uuid
from decimal import Decimal, InvalidOperation
import boto3
from doctor_common import *

# Allowed photo MIME types and corresponding file extensions.
PHOTO_TYPES = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp'}
# Supported consultation presets: (duration minutes, minimum capacity, maximum capacity).
CONSULTS = {'Quick Consult': (15, 1, 1), 'Standard Consult': (30, 1, 1),
            'Detailed Consult': (45, 1, 1), 'Health Camp Session': (60, 2, 10)}


# ============================================================
# CONSULTATION TYPE VALIDATION
# ============================================================
# Validate preset consultation name, fixed duration, capacity bounds, and nonnegative fee.



def validate_consult(data):

    # 1. Validate the consultation name (maximum 100 characters).
    name = require_text(data.get('name'), 'name', 100)

    # 2. Check whether the consultation type is supported.
    if name not in CONSULTS:
        raise ApiError(400, 'Unsupported consultation type')

    # 3. Get the fixed duration and allowed capacity range.
    duration, minimum, maximum = CONSULTS[name]

    # 4. Validate the consultation duration.
    if (
        type(data.get('duration_minutes')) is not int
        or data['duration_minutes'] != duration
    ):
        raise ApiError(
            400,
            f'{name} duration must be {duration} minutes'
        )

    # 5. Validate the consultation capacity.
    capacity = data.get('capacity')

    if (
        type(capacity) is not int
        or not minimum <= capacity <= maximum
    ):
        raise ApiError(
            400,
            f'{name} capacity must be {minimum}-{maximum}'
        )

    # 6. Validate the consultation fee.
    try:
        # Decimal is used for accurate monetary calculations.
        fee = Decimal(str(data.get('fee')))

        # Fee must be:
        # - A finite number
        # - Nonnegative
        # - Not greater than 99999999.99
        # - Limited to two decimal places
        if (
            not fee.is_finite()
            or fee < 0
            or fee > Decimal('99999999.99')
            or fee.as_tuple().exponent < -2
        ):
            raise ValueError()

    # 7. Handle invalid fee values.
    except (InvalidOperation, ValueError, TypeError):
        raise ApiError(
            400,
            'fee must be a nonnegative amount with up to two decimals'
        )

    # 8. Return validated consultation details.
    return name, duration, str(fee), capacity




# ============================================================
# DOCTOR PROFILE LOOKUP
# ============================================================

def fetch_doctor(cur, uid):

    # 1. Retrieve doctor details from the users and doctors tables.
    # JOIN combines both tables using the doctor's user_id.
    cur.execute(
        """
        SELECT
            u.user_id,
            u.name,
            u.email,
            d.licence_number,
            d.specialization_id,
            s.specialization_name AS specialization,
            d.experience_years,
            d.qualifications,
            d.hospital_name,
            d.clinic_address,
            d.city,
            d.languages,
            d.timezone,
            d.photo_key
        FROM users u
        JOIN doctors d
            ON d.user_id = u.user_id
        JOIN specializations s ON s.specialization_id = d.specialization_id
        WHERE u.user_id = %s
        """,
        (uid,)
    )

    # 2. Fetch the first matching doctor record.
    item = cur.fetchone()

    # 3. Return a 404 error if the doctor profile does not exist.
    if not item:
        raise ApiError(404, 'Doctor profile not found')

    # 4. Convert the stored languages JSON string into a Python list.
    # Example: '["English", "Hindi"]' -> ["English", "Hindi"]
    if isinstance(item['languages'], str):
        item['languages'] = json.loads(item['languages'])

    # 5. Return the complete doctor profile.
    return item



# ============================================================
# PROFILE GET / UPDATE
# ============================================================
# Fetch or update editable profile fields, with validation and transaction rollback.
def profile(event, uid, method, correlation):
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            # GET returns the doctor profile without making database changes.
            if method == 'GET':
                return respond(200, fetch_doctor(cur, uid), correlation)
            data = body_json(event)
            # Only explicitly permitted profile fields can be updated.
            allowed = {'name','specialization_id','experience_years','qualifications',
                       'hospital_name','clinic_address','city','languages','timezone'}
            if not data or set(data) - allowed:
                raise ApiError(400, 'Unsupported or empty profile update')
            fields = {}
            for field, limit in [('name',150),('qualifications',65535),
                                 ('hospital_name',200),('clinic_address',500),('city',100)]:
                if field in data:
                    fields[field] = require_text(data[field], field, limit)
            if 'specialization_id' in data:
                sid = data['specialization_id']
                if type(sid) is not int or sid < 1:
                    raise ApiError(400, 'Invalid specialization_id')
                cur.execute('SELECT specialization_id FROM specializations WHERE specialization_id=%s', (sid,))
                if cur.fetchone() is None:
                    raise ApiError(400, 'Unknown specialization_id')
                fields['specialization_id'] = sid
            if 'experience_years' in data:
                years = data['experience_years']
                if type(years) is not int or not 0 <= years <= 80:
                    raise ApiError(400, 'Invalid experience_years')
                fields['experience_years'] = years
            if 'languages' in data:
                languages = data['languages']
                if not isinstance(languages, list) or not languages or len(languages) > 20 or any(
                    not isinstance(x,str) or not x.strip() or len(x)>50 for x in languages):
                    raise ApiError(400, 'languages must be a nonempty list of language names')
                fields['languages'] = json.dumps(languages)
            if 'timezone' in data:
                if data['timezone'] != 'Asia/Kolkata':
                    raise ApiError(400, 'Milestone 3 supports Asia/Kolkata only')
                fields['timezone'] = data['timezone']
            fetch_doctor(cur, uid)
            # Name belongs to users; other professional details belong to doctors.
            if 'name' in fields:
                cur.execute('UPDATE users SET name=%s WHERE user_id=%s', (fields.pop('name'), uid))
            if fields:
                cur.execute('UPDATE doctors SET '+', '.join(f'{key}=%s' for key in fields)+
                            ' WHERE user_id=%s', (*fields.values(), uid))
            conn.commit()
            return respond(200, fetch_doctor(cur, uid), correlation)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()



# ============================================================
# PROFILE PHOTO URL / UPLOAD
# ============================================================
# GET  -> Generate a temporary URL to view the doctor's photo.
# POST -> Generate a temporary URL and fields to upload a photo.
#
# Actual photos are stored in S3.
# Only the photo_key is stored in RDS MySQL.
# ============================================================

def photo(event, uid, correlation, method='POST'):

    # ========================================================
    # 1. GET - VIEW EXISTING PROFILE PHOTO
    # ========================================================

    if method == 'GET':

        # 1.1 Connect to RDS MySQL.
        conn = db_connection()

        try:
            with conn.cursor() as cur:

                # 1.2 Retrieve the doctor's S3 photo key.
                cur.execute(
                    """
                    SELECT photo_key
                    FROM doctors
                    WHERE user_id = %s
                    """,
                    (uid,)
                )

                row = cur.fetchone()

        finally:
            # 1.3 Always close the database connection.
            conn.close()

        # 1.4 Check whether the doctor has a profile photo.
        if not row or not row['photo_key']:
            raise ApiError(
                404,
                'No profile photo uploaded'
            )

        # 1.5 Generate a temporary presigned GET URL.
        # This allows private photo viewing for 5 minutes.
        url = boto3.client('s3').generate_presigned_url(
            'get_object',
            Params={
                'Bucket': os.environ['PHOTO_BUCKET'],
                'Key': row['photo_key']
            },
            ExpiresIn=300
        )

        # 1.6 Return the photo viewing URL.
        return respond(
            200,
            {'url': url},
            correlation
        )

    # ========================================================
    # 2. POST - PREPARE NEW PROFILE PHOTO UPLOAD
    # ========================================================

    # 2.1 Extract the JSON request body.
    data = body_json(event)

    # 2.2 Get the uploaded photo's MIME type.
    mime = data.get('contentType')

    # 2.3 Allow only JPEG, PNG, and WebP images.
    if mime not in PHOTO_TYPES:
        raise ApiError(
            400,
            'Only image/jpeg, image/png, image/webp allowed'
        )

    # ========================================================
    # 3. VALIDATE PHOTO SIZE
    # ========================================================

    # 3.1 Read the photo size in bytes.
    size = data.get('sizeBytes')

    # 3.2 Allow images between 1 byte and 5 MB.
    if type(size) is not int or not 0 < size <= 5 * 1024 * 1024:
        raise ApiError(
            400,
            'sizeBytes must be between 1 and 5242880'
        )

    # ========================================================
    # 4. GENERATE UNIQUE S3 OBJECT KEY
    # ========================================================

    # 4.1 Get the S3 bucket name from environment variables.
    bucket = os.environ['PHOTO_BUCKET']

    # 4.2 Generate a unique filename using UUID.
    # Example: doctors/101/abc123.png
    key = (
        f'doctors/{uid}/'
        f'{uuid.uuid4().hex}.{PHOTO_TYPES[mime]}'
    )

    # 4.3 Create the S3 client.
    s3 = boto3.client('s3')

    # ========================================================
    # 5. GENERATE S3 PRESIGNED POST
    # ========================================================

    # 5.1 Generate temporary permission for direct S3 upload.
    # The browser uploads the image directly to S3.
    # Image bytes do not pass through API Gateway or Lambda.
    upload = s3.generate_presigned_post(
        Bucket=bucket,
        Key=key,
        Fields={
            'Content-Type': mime
        },
        Conditions=[
            {'Content-Type': mime},
            ['content-length-range', 1, 5 * 1024 * 1024]
        ],
        ExpiresIn=300
    )

    # ========================================================
    # 6. UPDATE PHOTO KEY IN RDS MYSQL
    # ========================================================

    # 6.1 Connect to RDS MySQL.
    conn = db_connection()

    try:
        with conn.cursor() as cur:

            # 6.2 Save the generated S3 photo key.
            # The actual photo is stored in S3, not RDS.
            cur.execute(
                """
                UPDATE doctors
                SET photo_key = %s
                WHERE user_id = %s
                """,
                (key, uid)
            )

            # 6.3 Check whether the doctor record exists.
            if not cur.rowcount:
                raise ApiError(
                    404,
                    'Doctor not found'
                )

        # 6.4 Commit the photo key update to RDS.
        conn.commit()

    except Exception:

        # 6.5 Roll back uncommitted changes if an error occurs.
        conn.rollback()
        raise

    finally:

        # 6.6 Always close the database connection.
        conn.close()

    # ========================================================
    # 7. RETURN PRESIGNED UPLOAD DETAILS
    # ========================================================

    # 7.1 Return the upload URL, form fields, and photo key.
    # The client must then upload the image directly to S3.
    return respond(
        200,
        {
            'upload': upload,
            'photoKey': key,
            'expiresIn': 300,
            'note': (
                'Upload using multipart/form-data with '
                'the returned fields; photo is private.'
            )
        },
        correlation
    )




# ============================================================
# CONSULTATION TYPE CRUD
# ============================================================
# GET    -> List consultation types.
# POST   -> Create a new consultation type.
# PUT    -> Update an existing consultation type.
# DELETE -> Delete a consultation type.
#
# All operations are restricted to the authenticated doctor.
# Data is stored in the RDS MySQL consultation_types table.
# ============================================================

def consultation(event, uid, method, item_id, correlation):

    # ========================================================
    # 1. ESTABLISH DATABASE CONNECTION
    # ========================================================

    conn = db_connection()

    try:
        with conn.cursor() as cur:

            # =================================================
            # 2. GET - FETCH CONSULTATION TYPES
            # =================================================

            if method == 'GET':

                # 2.1 Fetch consultation types belonging
                # to the authenticated doctor.
                cur.execute(
                    """
                    SELECT *
                    FROM consultation_types
                    WHERE doctor_id = %s
                    ORDER BY consultation_type_id
                    """,
                    (uid,)
                )

                # 2.2 Return all matching consultation types.
                return respond(
                    200,
                    {'items': cur.fetchall()},
                    correlation
                )

            # =================================================
            # 3. POST / PUT - CREATE OR UPDATE CONSULTATION
            # =================================================

            if method in ('POST', 'PUT'):

                # 3.1 Extract the JSON request body.
                data = body_json(event)

                # 3.2 Validate consultation name, duration,
                # fee, and capacity.
                name, duration, fee, capacity = validate_consult(
                    data
                )

                # =============================================
                # 4. POST - CREATE CONSULTATION TYPE
                # =============================================

                if method == 'POST':

                    # 4.1 Insert a new consultation type into RDS.
                    cur.execute(
                        """
                        INSERT INTO consultation_types (
                            doctor_id,
                            name,
                            duration_minutes,
                            fee,
                            capacity
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (uid, name, duration, fee, capacity)
                    )

                    # 4.2 Retrieve the newly generated ID.
                    item_id = cur.lastrowid

                # =============================================
                # 5. PUT - UPDATE CONSULTATION TYPE
                # =============================================

                else:

                    # 5.1 Update the consultation type
                    # belonging to the authenticated doctor.
                    cur.execute(
                        """
                        UPDATE consultation_types
                        SET
                            name = %s,
                            duration_minutes = %s,
                            fee = %s,
                            capacity = %s
                        WHERE
                            doctor_id = %s
                            AND consultation_type_id = %s
                        """,
                        (
                            name,
                            duration,
                            fee,
                            capacity,
                            uid,
                            item_id
                        )
                    )

                    # 5.2 Return 404 if no matching record
                    # was updated.
                    if not cur.rowcount:
                        raise ApiError(
                            404,
                            'Consultation type not found'
                        )

                # 5.3 Commit the INSERT or UPDATE to RDS.
                conn.commit()

                # 5.4 Return the consultation type ID.
                # POST -> 201 Created
                # PUT  -> 200 OK
                return respond(
                    200 if method == 'PUT' else 201,
                    {'consultationTypeId': item_id},
                    correlation
                )

            # =================================================
            # 6. DELETE - REMOVE CONSULTATION TYPE
            # =================================================

            if method == 'DELETE':

                # 6.1 Delete only the consultation type
                # belonging to the authenticated doctor.
                cur.execute(
                    """
                    DELETE FROM consultation_types
                    WHERE doctor_id = %s
                      AND consultation_type_id = %s
                    """,
                    (uid, item_id)
                )

                # 6.2 Return 404 if the record was not found.
                if not cur.rowcount:
                    raise ApiError(
                        404,
                        'Consultation type not found'
                    )

                # 6.3 Commit the deletion to RDS.
                conn.commit()

                # 6.4 Return successful deletion response.
                return respond(
                    200,
                    {'deleted': True},
                    correlation
                )

    # ========================================================
    # 7. ERROR HANDLING AND CONNECTION CLEANUP
    # ========================================================

    except Exception:

        # 7.1 Roll back uncommitted database changes.
        conn.rollback()
        raise

    finally:

        # 7.2 Close the RDS MySQL connection.
        conn.close()



# ============================================================
# MYSQL TIME NORMALIZATION
# ============================================================
# Convert MySQL TIME values into the HH:MM representation used by validation.
def parse_db_time(value):
    """Normalize MySQL TIME (often timedelta) before applying HH:MM validation."""
    # PyMySQL can return TIME columns as timedelta values.
    if hasattr(value, 'total_seconds'):
        total = int(value.total_seconds())
        if not 0 <= total < 24 * 3600 or total % 60:
            raise ApiError(400, 'Invalid saved availability time')
        return parse_time(f'{total // 3600:02d}:{(total // 60) % 60:02d}')
    parts = str(value).split(':')
    if len(parts) >= 2:
        return parse_time(f'{int(parts[0]):02d}:{parts[1]}')
    raise ApiError(400, 'Invalid saved availability time')


def validate_availability_duration(start, end, duration_minutes):
    """
    Ensure an availability window contains one or more complete
    consultation slots, with no unused minutes at the end.
    """
    if type(duration_minutes) is not int or duration_minutes <= 0:
        raise ApiError(500, 'Invalid consultation duration configuration')

    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    total_minutes = end_minutes - start_minutes

    if total_minutes < duration_minutes:
        raise ApiError(
            400,
            f'Availability must be at least {duration_minutes} minutes long'
        )

    if total_minutes % duration_minutes != 0:
        raise ApiError(
            400,
            f'Availability duration must be an exact multiple of '
            f'the {duration_minutes}-minute consultation duration'
        )



# ============================================================
# WEEKLY AVAILABILITY CRUD
# ============================================================
# GET    -> Fetch the doctor's weekly availability.
# POST   -> Create a new availability entry.
# PUT    -> Update an entry or replace the entire weekly schedule.
# DELETE -> Remove an availability entry.
#
# Validates consultation ownership and overlapping time windows.
# All availability patterns are stored in RDS MySQL.
# ============================================================

def availability(event, uid, method, correlation, item_id=None):

    # ========================================================
    # 1. ESTABLISH DATABASE CONNECTION
    # ========================================================

    conn = db_connection()

    try:
        with conn.cursor() as cur:

            # =================================================
            # 2. GET - FETCH WEEKLY AVAILABILITY
            # =================================================

            if method == 'GET':

                # 2.1 Fetch availability entries belonging
                # to the authenticated doctor.
                cur.execute(
                    """
                    SELECT
                        availability_id,
                        consultation_type_id,
                        day_of_week,
                        start_time,
                        end_time
                    FROM doctor_availability
                    WHERE doctor_id = %s
                    ORDER BY day_of_week, start_time
                    """,
                    (uid,)
                )

                # 2.2 Return all matching availability entries.
                return respond(
                    200,
                    {'items': cur.fetchall()},
                    correlation
                )

            # =================================================
            # 3. DELETE - REMOVE AN AVAILABILITY ENTRY
            # =================================================

            if method == 'DELETE':

                # 3.1 Delete the specified availability entry.
                cur.execute(
                    """
                    DELETE FROM doctor_availability
                    WHERE doctor_id = %s
                      AND availability_id = %s
                    """,
                    (uid, item_id)
                )

                # 3.2 Check whether the entry existed.
                if not cur.rowcount:
                    raise ApiError(
                        404,
                        'Availability entry not found'
                    )

                # 3.3 Commit the deletion to RDS.
                conn.commit()

                # 3.4 Return successful deletion response.
                return respond(
                    200,
                    {'deleted': True},
                    correlation
                )

            # =================================================
            # 4. VALIDATE HTTP METHOD AND REQUEST BODY
            # =================================================

            # 4.1 Allow only POST or PUT after GET/DELETE handling.
            if method not in ('POST', 'PUT'):
                raise ApiError(
                    405,
                    'Method not allowed'
                )

            # 4.2 Extract JSON data from the request.
            data = body_json(event)

            # =================================================
            # 5. BULK PUT - REPLACE ENTIRE WEEKLY SCHEDULE
            # =================================================

            # 5.1 Handle bulk PUT when no availability ID
            # is provided. Retained for older clients.
            if method == 'PUT' and item_id is None:

                patterns = data.get('patterns')

                # 5.2 Validate the patterns list.
                # Maximum 100 entries are allowed.
                if (
                    not isinstance(patterns, list)
                    or len(patterns) > 100
                ):
                    raise ApiError(
                        400,
                        'patterns must be a list of at most 100 entries'
                    )

                normalized = []

                # =============================================
                # 6. VALIDATE EACH AVAILABILITY PATTERN
                # =============================================

                for item in patterns:

                    # 6.1 Each pattern must be a dictionary.
                    if not isinstance(item, dict):
                        raise ApiError(
                            400,
                            'Invalid pattern'
                        )

                    # 6.2 Extract weekday and consultation type.
                    day = item.get('day_of_week')
                    typ = item.get('consultation_type_id')

                    # 6.3 Validate weekday and consultation ID.
                    if (
                        type(day) is not int
                        or not 0 <= day <= 6
                        or type(typ) is not int
                    ):
                        raise ApiError(
                            400,
                            'Invalid weekday or consultation type'
                        )

                    # 6.4 Validate start and end times.
                    start = parse_time(item.get('start_time'))
                    end = parse_time(item.get('end_time'))

                    # 6.5 Start time must be earlier than end time.
                    if start >= end:
                        raise ApiError(
                            400,
                            'Start time must preced end time'
                        )

                    # 6.6 Add the validated pattern to the list.
                    normalized.append(
                        (uid, typ, day, start, end)
                    )

                # =============================================
                # 7. CHECK OVERLAPPING BULK PATTERNS
                # =============================================

                # 7.1 Compare each pattern with subsequent ones.
                for i, a in enumerate(normalized):
                    for b in normalized[i + 1:]:

                        # Same weekday and overlapping times.
                        if (
                            a[2] == b[2]
                            and a[3] < b[4]
                            and b[3] < a[4]
                        ):
                            raise ApiError(
                                409,
                                'Overlapping availability patterns'
                            )

                # =============================================
                # 8. VERIFY CONSULTATION TYPE OWNERSHIP
                # =============================================

                # 8.1 Retrieve consultation types owned by doctor.
                cur.execute(
                    """
                    SELECT consultation_type_id, duration_minutes
                    FROM consultation_types
                    WHERE doctor_id = %s
                    """,
                    (uid,)
                )

                # 8.2 Map each owned consultation type to its duration.
                owned_durations = {
                    r['consultation_type_id']: r['duration_minutes']
                    for r in cur.fetchall()
                }

                # 8.3 Reject consultation types belonging to another doctor.
                if any(x[1] not in owned_durations for x in normalized):
                    raise ApiError(
                        403,
                        'Consultation type does not belong to doctor'
                    )

                # 8.4 Validate every window before deleting existing records.
                for pattern in normalized:
                    validate_availability_duration(
                        pattern[3],
                        pattern[4],
                        owned_durations[pattern[1]]
                    )

                # =============================================
                # 9. REPLACE WEEKLY AVAILABILITY IN RDS
                # =============================================

                # 9.1 Delete the doctor's existing weekly patterns.
                cur.execute(
                    """
                    DELETE FROM doctor_availability
                    WHERE doctor_id = %s
                    """,
                    (uid,)
                )

                # 9.2 Insert the newly validated patterns.
                if normalized:
                    cur.executemany(
                        """
                        INSERT INTO doctor_availability (
                            doctor_id,
                            consultation_type_id,
                            day_of_week,
                            start_time,
                            end_time
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        normalized
                    )

                # 9.3 Commit all changes to RDS.
                conn.commit()

                # 9.4 Return the number of saved patterns.
                return respond(
                    200,
                    {'updated': len(normalized)},
                    correlation
                )

            # =================================================
            # 10. INDIVIDUAL POST / PUT - VALIDATE INPUT
            # =================================================

            # 10.1 Extract weekday and consultation type.
            day = data.get('day_of_week')
            typ = data.get('consultation_type_id')

            # 10.2 Validate weekday and consultation type.
            if (
                type(day) is not int
                or not 0 <= day <= 6
                or type(typ) is not int
            ):
                raise ApiError(
                    400,
                    'Invalid weekday or consultation type'
                )

            # 10.3 Validate start and end times.
            start = parse_time(data.get('start_time'))
            end = parse_time(data.get('end_time'))

            # 10.4 Ensure start time precedes end time.
            if start >= end:
                raise ApiError(
                    400,
                    'Start time must precede end time'
                )

            # =================================================
            # 11. VERIFY CONSULTATION TYPE OWNERSHIP
            # =================================================

            # 11.1 Check whether the consultation type
            # belongs to the authenticated doctor.
            cur.execute(
                """
                SELECT duration_minutes
                FROM consultation_types
                WHERE doctor_id = %s
                  AND consultation_type_id = %s
                """,
                (uid, typ)
            )

            # 11.2 Retrieve the duration and verify consultation ownership.
            consultation = cur.fetchone()
            if not consultation:
                raise ApiError(
                    403,
                    'Consultation type does not belong to doctor'
                )

            # 11.3 Require the complete window to fit exact consultation slots.
            validate_availability_duration(
                start,
                end,
                consultation['duration_minutes']
            )

            # =================================================
            # 12. CHECK EXISTING AVAILABILITY OVERLAPS
            # =================================================

            # 12.1 Retrieve existing availability for the weekday.
            cur.execute(
                """
                SELECT
                    availability_id,
                    start_time,
                    end_time
                FROM doctor_availability
                WHERE doctor_id = %s
                  AND day_of_week = %s
                """,
                (uid, day)
            )

            # 12.2 Compare the new time window with existing ones.
            for row in cur.fetchall():

                # Skip the same record during an update.
                if row['availability_id'] == item_id:
                    continue

                # Normalize saved MySQL TIME values.
                existing_start = parse_db_time(row['start_time'])
                existing_end = parse_db_time(row['end_time'])

                # Reject overlapping time windows.
                if (
                    start < existing_end
                    and existing_start < end
                ):
                    raise ApiError(
                        409,
                        'Availability overlaps an existing time window'
                    )

            # =================================================
            # 13. POST - CREATE NEW AVAILABILITY ENTRY
            # =================================================

            if method == 'POST':

                # 13.1 Insert a new weekly availability entry.
                cur.execute(
                    """
                    INSERT INTO doctor_availability (
                        doctor_id,
                        consultation_type_id,
                        day_of_week,
                        start_time,
                        end_time
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (uid, typ, day, start, end)
                )

                # 13.2 Retrieve the generated availability ID.
                item_id = cur.lastrowid

            # =================================================
            # 14. PUT - UPDATE EXISTING AVAILABILITY ENTRY
            # =================================================

            else:

                # 14.1 Update the specified availability entry.
                cur.execute(
                    """
                    UPDATE doctor_availability
                    SET
                        consultation_type_id = %s,
                        day_of_week = %s,
                        start_time = %s,
                        end_time = %s
                    WHERE doctor_id = %s
                      AND availability_id = %s
                    """,
                    (typ, day, start, end, uid, item_id)
                )

                # 14.2 Check whether the entry existed.
                if not cur.rowcount:
                    raise ApiError(
                        404,
                        'Availability entry not found'
                    )

            # =================================================
            # 15. COMMIT CHANGES AND RETURN RESPONSE
            # =================================================

            # 15.1 Save the INSERT or UPDATE in RDS MySQL.
            conn.commit()

            # 15.2 Return the availability ID.
            # Slot materialisation happens separately.
            return respond(
                201 if method == 'POST' else 200,
                {
                    'availabilityId': item_id,
                    'materialisation': (
                        'Next scheduled run or manual invocation'
                    )
                },
                correlation
            )

    # ========================================================
    # 16. ERROR HANDLING AND CONNECTION CLEANUP
    # ========================================================

    except Exception:

        # 16.1 Roll back uncommitted database changes.
        conn.rollback()
        raise

    finally:

        # 16.2 Always close the RDS MySQL connection.
        conn.close()



def trigger_slot_reconciliation(day):
    """
    Ask the existing slot materialiser to reconcile the rolling window
    immediately after a blackout is added or removed. The invocation is
    asynchronous so the doctor API does not wait for the full materialiser.
    """
    function_name = os.environ.get('SLOT_MATERIALISER_FUNCTION')
    if not function_name:
        raise ApiError(500, 'Slot materialiser function is not configured')

    boto3.client('lambda').invoke(
        FunctionName=function_name,
        InvocationType='Event',
        Payload=json.dumps({'date': day.isoformat()}).encode('utf-8')
    )


# ============================================================
# DOCTOR BLACKOUT DATES
# ============================================================
# GET    -> Fetch the doctor's blackout dates.
# POST   -> Add a new blackout date.
# DELETE -> Remove an existing blackout date.
#
# Blackout dates represent days when a doctor is unavailable.
# All records are stored in the RDS MySQL doctor_blackouts table.
# ============================================================

def blackouts(event, uid, method, item_id, correlation):

    # ========================================================
    # 1. ESTABLISH DATABASE CONNECTION
    # ========================================================

    # 1.1 Connect to RDS MySQL.
    conn = db_connection()

    try:
        # 1.2 Create a database cursor for executing SQL.
        with conn.cursor() as cur:

            # =================================================
            # 2. GET - FETCH DOCTOR BLACKOUT DATES
            # =================================================

            if method == 'GET':

                # 2.1 Retrieve all blackout dates belonging
                # to the authenticated doctor.
                cur.execute(
                    """
                    SELECT
                        blackout_id,
                        blackout_date,
                        reason
                    FROM doctor_blackouts
                    WHERE doctor_id = %s
                    ORDER BY blackout_date
                    """,
                    (uid,)
                )

                # 2.2 Return the blackout records.
                return respond(
                    200,
                    {'items': cur.fetchall()},
                    correlation
                )

            # =================================================
            # 3. POST - CREATE A NEW BLACKOUT DATE
            # =================================================

            if method == 'POST':

                # 3.1 Extract JSON data from the request body.
                data = body_json(event)

                # 3.2 Extract and validate the blackout date.
                day = parse_date(data.get('blackout_date'))

                # 3.3 Extract the optional blackout reason.
                reason = data.get('reason')

                # =================================================
                # 4. VALIDATE BLACKOUT REASON
                # =================================================

                # 4.1 Reason must be a string of at most
                # 255 characters if provided.
                if reason is not None and (
                    not isinstance(reason, str)
                    or len(reason) > 255
                ):
                    raise ApiError(
                        400,
                        'Invalid reason'
                    )

                # =================================================
                # 5. CHECK FOR EXISTING BLACKOUT DATE
                # =================================================

                # 5.1 Check whether the authenticated doctor
                # already has a blackout on the requested date.
                cur.execute(
                    """
                    SELECT blackout_id
                    FROM doctor_blackouts
                    WHERE doctor_id = %s
                      AND blackout_date = %s
                    """,
                    (uid, day)
                )

                # 5.2 Reject duplicate blackout dates.
                if cur.fetchone():
                    raise ApiError(
                        409,
                        'Blackout already exists'
                    )

                # =================================================
                # 6. INSERT NEW BLACKOUT DATE INTO RDS
                # =================================================

                # 6.1 Insert the validated blackout record.
                cur.execute(
                    """
                    INSERT INTO doctor_blackouts (
                        doctor_id,
                        blackout_date,
                        reason
                    )
                    VALUES (%s, %s, %s)
                    """,
                    (uid, day, reason)
                )

                # 6.2 Commit the new blackout record to RDS.
                conn.commit()

                # 6.3 Trigger immediate asynchronous slot reconciliation.
                # The Schedule API also checks the blackout table directly,
                # so the date disappears from the dashboard immediately.
                trigger_slot_reconciliation(day)

                # 6.4 Return the newly generated blackout ID.
                return respond(
                    201,
                    {'blackoutId': cur.lastrowid},
                    correlation
                )

            # =================================================
            # 7. DELETE - REMOVE AN EXISTING BLACKOUT DATE
            # =================================================

            if method == 'DELETE':

                # 7.1 Delete only the blackout record belonging
                # to the authenticated doctor.
                cur.execute(
                    """
                    DELETE FROM doctor_blackouts
                    WHERE doctor_id = %s
                      AND blackout_id = %s
                    """,
                    (uid, item_id)
                )

                # 7.2 Return 404 if the blackout was not found.
                if not cur.rowcount:
                    raise ApiError(
                        404,
                        'Blackout not found'
                    )

                # 7.3 Commit the deletion to RDS.
                conn.commit()

                # 7.4 Regenerate eligible slots for the rolling window.
                # Existing inactive slot IDs are reactivated by the materialiser.
                from datetime import datetime
                from zoneinfo import ZoneInfo
                trigger_slot_reconciliation(
                    # Reconcile from today so the removed blackout date is
                    # included in the rolling 30-day window.
                    datetime.now(ZoneInfo('Asia/Kolkata')).date()
                )

                # 7.5 Return successful deletion response.
                return respond(
                    200,
                    {'deleted': True},
                    correlation
                )

    # ========================================================
    # 8. ERROR HANDLING AND CONNECTION CLEANUP
    # ========================================================

    except Exception:

        # 8.1 Roll back any uncommitted database changes.
        conn.rollback()
        raise

    finally:

        # 8.2 Always close the RDS MySQL connection.
        conn.close()



# ============================================================
# DAILY APPOINTMENT SCHEDULE
# ============================================================
# Read materialised DynamoDB slots and attach appointment/patient data from MySQL.

# ============================================================
# DAILY APPOINTMENT SCHEDULE
# ============================================================
# GET -> Retrieve the doctor's daily appointment schedule.
#
# DynamoDB:
#   - Fetch materialised appointment slots.
#   - Filter slots by doctor, date, and availability status.
#
# RDS MySQL:
#   - Fetch appointment details.
#   - Retrieve patient information using a SQL JOIN.
#
# Combine DynamoDB slots and MySQL appointments using slot ID.
# ============================================================

def schedule(event, uid, correlation):

    # ========================================================
    # 1. EXTRACT AND VALIDATE REQUESTED DATE
    # ========================================================

    # 1.1 Retrieve the selected date from query parameters.
    day = parse_date(
        (event.get('queryStringParameters') or {}).get('date')
    )

    # ========================================================
    # 2. CHECK WHETHER THE REQUESTED DATE IS A DOCTOR BLACKOUT
    # ========================================================

    # Return an empty schedule immediately for blackout dates.
    # This makes the dashboard reflect the change without waiting for
    # the asynchronous DynamoDB reconciliation to finish.
    blackout_conn = db_connection()
    try:
        with blackout_conn.cursor() as blackout_cur:
            blackout_cur.execute(
                """
                SELECT blackout_id
                FROM doctor_blackouts
                WHERE doctor_id = %s
                  AND blackout_date = %s
                LIMIT 1
                """,
                (uid, day)
            )
            if blackout_cur.fetchone():
                return respond(
                    200,
                    {
                        'date': day.isoformat(),
                        'timezone': 'Asia/Kolkata',
                        'slots': []
                    },
                    correlation
                )
    finally:
        blackout_conn.close()

    # ========================================================
    # 3. IMPORT REQUIRED LIBRARIES
    # ========================================================

    import datetime as dt
    from zoneinfo import ZoneInfo
    from boto3.dynamodb.conditions import Key

    # ========================================================
    # 3. CONVERT IST DATE RANGE INTO UTC
    # ========================================================

    # 3.1 Convert the start of the selected day
    # from Asia/Kolkata timezone into UTC.
    start = dt.datetime.combine(
        day,
        dt.time.min,
        tzinfo=ZoneInfo('Asia/Kolkata')
    ).astimezone(dt.timezone.utc)

    # 3.2 Convert the start of the next day
    # from Asia/Kolkata timezone into UTC.
    end = (
        dt.datetime.combine(
            day + dt.timedelta(days=1),
            dt.time.min,
            tzinfo=ZoneInfo('Asia/Kolkata')
        )
    ).astimezone(dt.timezone.utc)

    # Exclude slots whose appointment time has already elapsed.
    # This keeps the schedule accurate even before the asynchronous
    # materialiser has updated their DynamoDB status.
    now_utc = dt.datetime.now(dt.timezone.utc)

    # ========================================================
    # 4. CONNECT TO DYNAMODB SLOTS TABLE
    # ========================================================

    # 4.1 Access the DynamoDB slots table using
    # the SLOTS_TABLE environment variable.
    table = boto3.resource('dynamodb').Table(
        os.environ['SLOTS_TABLE']
    )

    # ========================================================
    # 5. PREPARE DYNAMODB QUERY
    # ========================================================

    # 5.1 Query the DoctorStartIndex to retrieve slots
    # belonging to the authenticated doctor within
    # the requested UTC date range.
    params = {
        'IndexName': 'DoctorStartIndex',

        'KeyConditionExpression': (
            Key('doctorId').eq(str(uid))
            & Key('startTimeUTC').between(
                start.isoformat(),
                end.isoformat()
            )
        ),

        # Maximum number of items evaluated per query call.
        'Limit': 100
    }

    # ========================================================
    # 6. FETCH SLOTS FROM DYNAMODB
    # ========================================================

    # 6.1 Initialize the list for storing matching slots.
    items = []

    while True:

        # 6.2 Execute the DynamoDB query.
        res = table.query(**params)

        # 6.3 Add slots that belong to the requested day
        # and have AVAILABLE status.
        items.extend(
            x for x in res['Items']
            if (
                x['startTimeUTC'] < end.isoformat()
                and x.get('status') == 'AVAILABLE'
                and dt.datetime.fromisoformat(x['endTimeUTC']) > now_utc
            )
        )

        # 6.4 Stop if DynamoDB has no additional pages.
        if 'LastEvaluatedKey' not in res:
            break

        # 6.5 Continue querying the next page.
        params['ExclusiveStartKey'] = res['LastEvaluatedKey']

    # ========================================================
    # 7. CONNECT TO RDS MYSQL
    # ========================================================

    # 7.1 Establish a database connection.
    conn = db_connection()

    try:
        with conn.cursor() as cur:

            # =================================================
            # 8. FETCH APPOINTMENTS AND PATIENT DETAILS
            # =================================================

            # 8.1 Retrieve appointments belonging to the doctor
            # within the requested UTC date range.
            #
            # JOIN the users table to obtain patient names.
            cur.execute(
                """
                SELECT
                    a.appointment_id,
                    a.slot_id,
                    a.status,
                    a.start_time_utc,
                    u.user_id AS patient_id,
                    u.name AS patient_name
                FROM appointments a
                JOIN users u
                    ON u.user_id = a.patient_id
                WHERE a.doctor_id = %s
                  AND a.start_time_utc >= %s
                  AND a.start_time_utc < %s
                ORDER BY a.start_time_utc
                """,
                (
                    uid,
                    start.replace(tzinfo=None),
                    end.replace(tzinfo=None)
                )
            )

            # 8.2 Fetch all matching appointment records.
            appointments = cur.fetchall()

    # ========================================================
    # 9. CLOSE RDS MYSQL CONNECTION
    # ========================================================

    finally:

        # 9.1 Close the database connection.
        conn.close()

    # ========================================================
    # 10. GROUP APPOINTMENTS BY SLOT ID
    # ========================================================

    # 10.1 Create a dictionary to group appointments
    # under their corresponding slot IDs.
    by_slot = {}

    for a in appointments:

        # 10.2 Add each appointment to its matching slot group.
        by_slot.setdefault(
            a['slot_id'],
            []
        ).append(a)

    # ========================================================
    # 11. ATTACH APPOINTMENTS TO DYNAMODB SLOTS
    # ========================================================

    # 11.1 Match DynamoDB slotId with MySQL slot_id.
    for slot in items:

        # 11.2 Attach the appointment records to each slot.
        # Use an empty list if no appointments exist.
        slot['appointments'] = by_slot.get(
            slot['slotId'],
            []
        )

    # ========================================================
    # 12. RETURN DAILY SCHEDULE RESPONSE
    # ========================================================

    # 12.1 Return the requested date, timezone,
    # and combined slot/appointment information.
    return respond(
        200,
        {
            'date': day.isoformat(),
            'timezone': 'Asia/Kolkata',
            'slots': items
        },
        correlation
    )


# ============================================================
# LAMBDA ENTRY POINT AND ROUTING
# ============================================================
# Resolve authenticated doctor identity, validate route IDs, and dispatch requests.
def lambda_handler(event,context):
    correlation=cid(event)
    try:
        uid=doctor_id(event)
        method=event.get('httpMethod','')
        resource=event.get('resource','')
        raw_id=(event.get('pathParameters') or {}).get('id')
        item_id=None
        if raw_id is not None:
            if not str(raw_id).isdigit() or int(raw_id)<1:
                raise ApiError(400,'Invalid record ID')
            item_id=int(raw_id)
        if resource=='/doctors/me':
            return profile(event,uid,method,correlation)
        if resource=='/doctors/me/photo' and method in ('POST','GET'):
            return photo(event,uid,correlation,method)
        if resource.startswith('/doctors/me/consultation-types'):
            return consultation(event,uid,method,item_id,correlation)
        if resource.startswith('/doctors/me/availability'):
            return availability(event,uid,method,correlation,item_id)
        if resource.startswith('/doctors/me/blackouts'):
            return blackouts(event,uid,method,item_id,correlation)
        if resource=='/doctors/me/schedule' and method=='GET':
            return schedule(event,uid,correlation)
        raise ApiError(404,'Route not found')
    except Exception as exc:
        return dispatch_error(exc,correlation)
