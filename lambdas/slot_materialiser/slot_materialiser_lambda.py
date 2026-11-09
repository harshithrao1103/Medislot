
"""
MediSlot - Slot Materialiser Lambda

Purpose:
    Generate appointment slots for a rolling 30-day booking window.

Responsibilities:
    1. Read doctor availability patterns from RDS MySQL.
    2. Read doctor blackout dates.
    3. Generate appointment slots in IST.
    4. Convert slot times to UTC.
    5. Store slots in DynamoDB.
    6. Prevent duplicate slot creation.
    7. Reactivate eligible inactive slots.
    8. Disable obsolete unbooked slots.

The materialiser is designed to run repeatedly without
resetting existing booking or hold counters.
"""

# ============================================================
# 1. IMPORTS
# ============================================================

import datetime as dt
import hashlib
import json
import logging
import os

from zoneinfo import ZoneInfo

import boto3
from botocore.exceptions import ClientError

from auth_common import db_connection


# ============================================================
# 2. TIMEZONE CONFIGURATION
# ============================================================

# Doctor availability is configured in Indian Standard Time.
IST = ZoneInfo('Asia/Kolkata')

# Appointment slots are stored in DynamoDB using UTC.
UTC = dt.timezone.utc


# ============================================================
# 3. TIME CONVERSION HELPER
# ============================================================

def time_of(value):
    """
    Convert a database time value into a Python time object.

    MySQL drivers may return TIME values as:
        - datetime.timedelta
        - datetime.time
        - String representation

    Returns:
        datetime.time object.
    """

    # Handle MySQL TIME values returned as timedelta.
    if isinstance(value, dt.timedelta):

        seconds = int(value.total_seconds())

        return dt.time(
            seconds // 3600,
            (seconds % 3600) // 60
        )

    # Return directly if the value is already a time object.
    if isinstance(value, dt.time):
        return value

    # Convert a string representation into a time object.
    return dt.time.fromisoformat(str(value))


# ============================================================
# 4. GENERATE SLOTS FOR A DOCTOR
# ============================================================

def make_slots(doctor, pattern, day):
    """
    Generate appointment slots for a doctor on a specific date.

    Parameters:
        doctor:
            Doctor's unique user ID.

        pattern:
            Doctor availability configuration containing:
                - day_of_week
                - start_time
                - end_time
                - consultation_type_id
                - duration_minutes
                - capacity

        day:
            Date for which slots should be generated.

    Returns:
        List of DynamoDB slot items.
    """

    # Generate slots only when the date matches
    # the configured weekday.
    if day.weekday() != int(pattern['day_of_week']):
        return []

    # Convert availability start and end times.
    start = time_of(pattern['start_time'])
    end = time_of(pattern['end_time'])

    # Combine the selected date with the availability times.
    # These datetime objects use the IST timezone.
    cursor = dt.datetime.combine(
        day,
        start,
        tzinfo=IST
    )

    finish = dt.datetime.combine(
        day,
        end,
        tzinfo=IST
    )

    # Duration of one consultation appointment.
    duration = dt.timedelta(
        minutes=int(pattern['duration_minutes'])
    )

    slots = []

    # Generate consecutive slots while each complete
    # consultation fits inside the availability window.
    while cursor + duration <= finish:

        end_at = cursor + duration

        # Convert IST slot times into UTC.
        utc_start = cursor.astimezone(UTC).isoformat()
        utc_end = end_at.astimezone(UTC).isoformat()

        # Build a deterministic identifier using:
        # doctor ID + consultation type ID + UTC start time.
        identifier = (
            f"{doctor}:"
            f"{pattern['consultation_type_id']}:"
            f"{utc_start}"
        )

        # SHA-256 ensures that the same logical slot
        # generates the same slotId on repeated runs.
        slot_id = hashlib.sha256(
            identifier.encode()
        ).hexdigest()

        # Construct the DynamoDB slot item.
        slots.append({
            'slotId': slot_id,
            'doctorId': str(doctor),
            'consultationTypeId': str(
                pattern['consultation_type_id']
            ),
            'startTimeUTC': utc_start,
            'endTimeUTC': utc_end,
            'timezone': 'Asia/Kolkata',
            'capacity': int(pattern['capacity']),
            'bookedCount': 0,
            'heldCount': 0,
            'version': 0,
            'status': 'AVAILABLE'
        })

        # Move to the next consultation interval.
        cursor = end_at

    return slots


# ============================================================
# 5. MATERIALISE THE ROLLING BOOKING WINDOW
# ============================================================

def materialise(
    start_day,
    days=30,
    table=None,
    connection_factory=db_connection
):
    """
    Synchronise DynamoDB appointment slots with RDS availability.

    Main operations:
        1. Read availability patterns from MySQL.
        2. Read doctor blackout dates.
        3. Identify doctors using the IST timezone.
        4. Generate the desired appointment slots.
        5. Insert missing slots into DynamoDB.
        6. Reactivate eligible inactive slots.
        7. Disable obsolete unbooked slots.

    Parameters:
        start_day:
            First date of the materialisation window.

        days:
            Number of days to process (default: 30).

        table:
            Optional DynamoDB table object, useful for testing.

        connection_factory:
            Function used to establish the MySQL connection.

    Returns:
        Dictionary containing materialisation statistics.
    """

    # --------------------------------------------------------
    # 5.1 INITIALISE DYNAMODB AND MYSQL CONNECTIONS
    # --------------------------------------------------------

    # Use the supplied table or load the configured DynamoDB table.
    table = table or boto3.resource('dynamodb').Table(
        os.environ['SLOTS_TABLE']
    )

    # Open the RDS MySQL connection.
    conn = connection_factory()

    try:
        with conn.cursor() as cur:

            # ------------------------------------------------
            # 5.2 FETCH DOCTOR AVAILABILITY PATTERNS
            # ------------------------------------------------

            # Join availability with consultation types to
            # retrieve duration and capacity for each pattern.
            #
            # Only doctors configured with Asia/Kolkata
            # timezone are included.

            cur.execute(
                """
                SELECT
                    a.doctor_id,
                    a.consultation_type_id,
                    a.day_of_week,
                    a.start_time,
                    a.end_time,
                    c.duration_minutes,
                    c.capacity
                FROM doctor_availability a
                JOIN consultation_types c
                    ON c.consultation_type_id = a.consultation_type_id
                    AND c.doctor_id = a.doctor_id
                JOIN doctors d
                    ON d.user_id = a.doctor_id
                WHERE d.timezone = %s
                """,
                ('Asia/Kolkata',)
            )

            patterns = cur.fetchall()

            # ------------------------------------------------
            # 5.3 FETCH DOCTOR BLACKOUT DATES
            # ------------------------------------------------

            # Blackout dates represent days when doctors
            # are unavailable for appointments.
            #
            # Fetch blackouts only within the booking window.

            cur.execute(
                """
                SELECT
                    doctor_id,
                    blackout_date
                FROM doctor_blackouts
                WHERE blackout_date >= %s
                  AND blackout_date < %s
                """,
                (
                    start_day,
                    start_day + dt.timedelta(days=days)
                )
            )

            # Store doctor/date pairs for efficient lookup.
            #store doctor id and date as pair
            blackouts = {
                (
                    str(x['doctor_id']),
                    str(x['blackout_date'])
                )
                for x in cur.fetchall()
            }

            # ------------------------------------------------
            # 5.4 FETCH DOCTORS
            # ------------------------------------------------

            # Collect doctor IDs for the later cleanup step.
            # This includes doctors without availability patterns.

            cur.execute(
                """
                SELECT user_id
                FROM doctors
                WHERE timezone = %s
                """,
                ('Asia/Kolkata',)
            )

            doctors = {
                str(x['user_id'])
                for x in cur.fetchall()
            }

    finally:

        # Always close the MySQL connection.
        conn.close()

    # --------------------------------------------------------
    # 5.5 GENERATE THE DESIRED SLOTS
    # --------------------------------------------------------

    # Never create or alter elapsed slots on the current IST date.
    # Historical explicit test windows retain their deterministic behavior.
    now_ist = dt.datetime.now(IST)
    live_window = start_day <= now_ist.date() < start_day + dt.timedelta(days=days)
    wanted = {}

    # Iterate over every date in the booking window.
    for offset in range(days):

        day = start_day + dt.timedelta(days=offset)

        # Process every availability pattern.
        for p in patterns:

            doctor = str(p['doctor_id'])

            # Skip this doctor/date when a blackout exists.
            if (doctor, day.isoformat()) in blackouts:
                continue

            # Generate slots matching the weekday.
            for slot in make_slots(doctor, p, day):
                if live_window and dt.datetime.fromisoformat(slot['endTimeUTC']) <= now_ist:
                    continue
                wanted[slot['slotId']] = slot

    # --------------------------------------------------------
    # 5.6 INITIALISE MATERIALISATION COUNTERS
    # --------------------------------------------------------

    created = 0
    existing = 0
    disabled = 0

    # --------------------------------------------------------
    # 5.7 CREATE MISSING SLOTS IN DYNAMODB
    # --------------------------------------------------------

    for slot in wanted.values():

        try:

            # Insert only when slotId does not already exist.
            #
            # This conditional write prevents duplicate
            # creation during repeated or concurrent runs.

            table.put_item(
                Item=slot,
                ConditionExpression='attribute_not_exists(slotId)'
            )

            created += 1

        except ClientError as exc:

            # Only handle the expected duplicate-slot condition.
            # Propagate unexpected DynamoDB errors.
            if (
                exc.response['Error']['Code']
                != 'ConditionalCheckFailedException'
            ):
                raise

            # ------------------------------------------------
            # 5.8 REACTIVATE ELIGIBLE INACTIVE SLOTS
            # ------------------------------------------------

            # An existing inactive slot can be reactivated
            # only when no appointments or holds exist.
            #
            # Existing counters and version values are preserved.

            try:

                table.update_item(  
                    Key={
                        'slotId': slot['slotId']
                    },
                    UpdateExpression='SET #st=:active ADD #ver :one',
                    ConditionExpression=(
                        '#st=:inactive '
                        'AND bookedCount=:zero '
                        'AND heldCount=:zero'
                    ),
                    ExpressionAttributeNames={
                        '#st': 'status',
                        '#ver': 'version'
                    },
                    ExpressionAttributeValues={
                        ':active': 'AVAILABLE',
                        ':inactive': 'INACTIVE',
                        ':zero': 0,
                        ':one': 1
                    }
                )

            except ClientError as update_exc:

                # A failed condition means the slot was not
                # eligible for reactivation.
                if (
                    update_exc.response['Error']['Code']
                    != 'ConditionalCheckFailedException'
                ):
                    raise

            # Count slots that already existed,
            # regardless of whether reactivation succeeded.
            existing += 1

    # --------------------------------------------------------
    # 5.9 DISABLE OBSOLETE UNBOOKED SLOTS
    # --------------------------------------------------------

    # When a doctor changes availability or adds a blackout,
    # previously generated slots may become obsolete.
    #
    # These slots are marked INACTIVE rather than deleted.
    # Existing bookings remain attached to their slot, but the slot becomes
    # INACTIVE for new bookings when no hold is in progress. Active holds are
    # left untouched until their hold-expiry workflow completes.

    for doctor in doctors:

        from boto3.dynamodb.conditions import Key

        # Query the DoctorStartIndex for the doctor's slots
        # within the materialisation date window.
        #
        # Convert the IST window boundaries into UTC.

        query = {
            'IndexName': 'DoctorStartIndex',

            'KeyConditionExpression': (
                Key('doctorId').eq(doctor)
                &
                Key('startTimeUTC').between(
                    # Query from the beginning of the first day, not from
                    # the current time. This includes elapsed slots so they
                    # can be marked INACTIVE during reconciliation.
                    dt.datetime.combine(
                        start_day, dt.time.min, tzinfo=IST
                    ).astimezone(UTC).isoformat(),

                    dt.datetime.combine(
                        start_day + dt.timedelta(days=days),
                        dt.time.min,
                        tzinfo=IST
                    ).astimezone(UTC).isoformat()
                )
            ),

            'ProjectionExpression': 'slotId,#st',

            'ExpressionAttributeNames': {
                '#st': 'status'
            }
        }

         # ----------------------------------------------------
        # 5.10 PROCESS PAGINATED DYNAMODB QUERY RESULTS
        # ----------------------------------------------------

        # DynamoDB queries may return multiple pages.
        # Continue until all matching slots are processed.

        while True:

            result = table.query(**query)

            for old in result['Items']:

                # Disable only when:
                # 1. The slot is no longer in the wanted set.
                # 2. Its current status is AVAILABLE.

                if (
                    old['slotId'] not in wanted
                    and old.get('status') == 'AVAILABLE'
                ):

                    try:

                        # Conditional update ensures the slot
                        # is still available and has no active
                        # holds at update time. Booked appointments stay stored.

                        table.update_item(
                            Key={
                                'slotId': old['slotId']
                            },

                            UpdateExpression=(
                                'SET #st=:disabled ADD #ver :one'
                            ),

                            ConditionExpression=(
                                '#st=:active '
                                'AND heldCount=:zero'
                            ),

                            ExpressionAttributeNames={
                                '#st': 'status',
                                '#ver': 'version'
                            },

                            ExpressionAttributeValues={
                                ':disabled': 'INACTIVE',
                                ':active': 'AVAILABLE',
                                ':zero': 0,
                                ':one': 1
                            }
                        )

                        disabled += 1

                    except ClientError as exc:

                        # Ignore only conditional failures.
                        # Other DynamoDB errors must propagate.
                        if (
                            exc.response['Error']['Code']
                            != 'ConditionalCheckFailedException'
                        ):
                            raise

            # Stop when DynamoDB has no additional pages.
            if 'LastEvaluatedKey' not in result:
                break

            # Continue querying from the last evaluated item.
            query['ExclusiveStartKey'] = result['LastEvaluatedKey']

    # --------------------------------------------------------
    # 5.11 RETURN MATERIALISATION STATISTICS
    # --------------------------------------------------------

    return {
        'created': created,
        'alreadyPresent': existing,
        'disabled': disabled,
        'windowStart': start_day.isoformat(),
        'days': days
    }


# ============================================================
# 6. AWS LAMBDA ENTRY POINT
# ============================================================

def lambda_handler(event, context):
    """
    AWS Lambda entry point for slot materialisation.

    Steps:
        1. Determine the materialisation start date.
        2. Read the booking window from SSM Parameter Store.
        3. Validate that the booking window is 30 days.
        4. Run the materialisation process.
        5. Log the results to CloudWatch.
        6. Return materialisation statistics.
    """

    # --------------------------------------------------------
    # 6.1 DETERMINE THE START DATE
    # --------------------------------------------------------

    # Use the event date when provided.
    # Otherwise use today's date in IST.
    start = (event or {}).get('date')

    day = (
        dt.date.fromisoformat(start)
        if start
        else dt.datetime.now(IST).date()
    )

    # --------------------------------------------------------
    # 6.2 READ BOOKING WINDOW FROM SSM
    # --------------------------------------------------------

    # The SSM parameter name is supplied through
    # the BOOKING_WINDOW_PARAM environment variable.

    days = int(
        boto3.client('ssm').get_parameter(
            Name=os.environ['BOOKING_WINDOW_PARAM']
        )['Parameter']['Value']
    )

    # --------------------------------------------------------
    # 6.3 VALIDATE THE REQUIRED 30-DAY WINDOW
    # --------------------------------------------------------

    if days != 30:
        raise ValueError(
            'Milestone 3 requires a 30-day window'
        )

    # --------------------------------------------------------
    # 6.4 EXECUTE SLOT MATERIALISATION
    # --------------------------------------------------------

    result = materialise(day, days)

    # --------------------------------------------------------
    # 6.5 LOG MATERIALISATION RESULTS
    # --------------------------------------------------------

    # Write a structured JSON log entry.
    # Lambda logs are sent to CloudWatch Logs when configured.

    logging.info(
        json.dumps({
            'event': 'SlotMaterialisationComplete',
            **result
        })
    )

    # --------------------------------------------------------
    # 6.6 RETURN EXECUTION RESULTS
    # --------------------------------------------------------

    return result
