"""MediSlot Milestone 5 appointment API and transactional outbox dispatcher."""
import hashlib
import json
import logging
import os
import random
import time
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

import pymysql
_sm = boto3.client("secretsmanager")

def body_json(event):
    try:
        value = json.loads(event.get("body") or "{}")
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (TypeError, ValueError):
        raise ApiError(400, "Invalid JSON request body")

def cid(event):
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    candidate = headers.get("x-correlation-id")
    return candidate if isinstance(candidate, str) and 1 <= len(candidate) <= 80 else str(uuid.uuid4())

def response(status, body, correlation=None):
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store", "X-Correlation-ID": correlation or ""}, "body": json.dumps(body, default=_json_safe)}

def db_connection():
    secret = json.loads(_sm.get_secret_value(SecretId=os.environ["DB_SECRET_ARN"])["SecretString"])
    return pymysql.connect(host=os.environ["DB_HOST"], port=int(os.environ["DB_PORT"]), user=secret["username"], password=secret["password"], database=os.environ["DB_NAME"], connect_timeout=8, read_timeout=8, write_timeout=8, autocommit=False, cursorclass=pymysql.cursors.DictCursor)

log = logging.getLogger("medislot.appointments")
log.setLevel(logging.INFO)
_ddb = boto3.resource("dynamodb")
_ssm = boto3.client("ssm")
_events = boto3.client("events")
_slots = _ddb.Table(os.environ["SLOTS_TABLE"])
_idempotency = _ddb.Table(os.environ["IDEMPOTENCY_TABLE"])

ACTIVE_STATUS = "CONFIRMED"
UPCOMING_LIMIT = 3
MAX_CONFLICT_RETRIES = 5
IDEMPOTENCY_TTL_SECONDS = 7 * 24 * 60 * 60


class ApiError(Exception):
    def __init__(self, status, message, extra=None):
        self.status = status
        self.message = message
        self.extra = extra or {}


def _metric(name, value=1, dimensions=None):
    dimensions = dimensions or {}
    log.info(json.dumps({
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": "medislot",
                "Dimensions": [list(dimensions.keys())],
                "Metrics": [{"Name": name, "Unit": "Count"}],
            }],
        },
        name: value,
        **dimensions,
    }, separators=(",", ":")))


def _identity(event, required_role=None):
    ctx = (event.get("requestContext") or {}).get("authorizer") or {}
    role = str(ctx.get("role", "")).upper()
    raw = ctx.get("userId")
    if role not in ("PATIENT", "DOCTOR") or not str(raw or "").isdigit():
        raise ApiError(401, "Authentication required")
    if required_role and role != required_role:
        raise ApiError(403, f"{required_role.title()} role required")
    return int(raw), role


def _json_safe(value):
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _refund_policy():
    name = os.environ["REFUND_POLICY_PARAM"]
    value = _ssm.get_parameter(Name=name)["Parameter"]["Value"]
    policy = json.loads(value)
    tiers = policy.get("tiers", [])
    expected = {"MORE_THAN_24_HOURS", "12_TO_24_HOURS", "3_TO_12_HOURS", "LESS_THAN_3_HOURS", "NO_SHOW"}
    if {x.get("name") for x in tiers} != expected:
        raise RuntimeError("Refund policy parameter has an invalid tier set")
    return policy


def _refund_tier(start_time, now, actor, policy=None):
    if actor == "DOCTOR":
        return "DOCTOR_CANCELLED", Decimal("100.00")
    if actor == "NO_SHOW":
        return "NO_SHOW", Decimal("0.00")
    hours = (start_time - now).total_seconds() / 3600.0
    if policy is None:
        policy = {"tiers": [
            {"name": "MORE_THAN_24_HOURS", "refundPercentage": 100, "minHours": 24, "minInclusive": False},
            {"name": "12_TO_24_HOURS", "refundPercentage": 75, "minHours": 12, "minInclusive": True, "maxHours": 24, "maxInclusive": True},
            {"name": "3_TO_12_HOURS", "refundPercentage": 50, "minHours": 3, "minInclusive": True, "maxHours": 12, "maxInclusive": False},
            {"name": "LESS_THAN_3_HOURS", "refundPercentage": 0, "maxHours": 3, "maxInclusive": False},
        ]}
    for tier in policy.get("tiers", []):
        if tier.get("name") == "NO_SHOW":
            continue
        minimum = tier.get("minHours")
        maximum = tier.get("maxHours")
        above_min = minimum is None or (hours >= float(minimum) if tier.get("minInclusive", True) else hours > float(minimum))
        below_max = maximum is None or (hours <= float(maximum) if tier.get("maxInclusive", False) else hours < float(maximum))
        if above_min and below_max:
            return tier["name"], Decimal(str(tier["refundPercentage"]))
    raise RuntimeError("No refund tier matches the cancellation time")


def _no_show_eligible(start_time, now=None):
    """Return True once five full minutes have elapsed after appointment start."""
    now = now or datetime.now(timezone.utc)
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    else:
        start_time = start_time.astimezone(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    return now >= start_time + timedelta(minutes=5)


def _slot(doctor_id, slot_id):
    # The canonical key is doctorId + (startTimeUTC#slotId). The client only
    # supplies doctorId/slotId, so query the doctor's partition and filter ID.
    kwargs = {
        "KeyConditionExpression": Key("doctorId").eq(str(doctor_id)),
        "FilterExpression": Attr("slotId").eq(str(slot_id)),
        "ConsistentRead": True,
    }
    while True:
        result = _slots.query(**kwargs)
        items = result.get("Items", [])
        if items:
            if len(items) != 1:
                raise ApiError(409, "Slot identifier is ambiguous")
            return items[0]
        key = result.get("LastEvaluatedKey")
        if not key:
            return None
        kwargs["ExclusiveStartKey"] = key

def _reserve_seat(doctor_id, slot):
    slot_key = {"doctorId": str(doctor_id), "slotKey": slot["slotKey"]}

    for attempt in range(MAX_CONFLICT_RETRIES + 1):
        current = _slots.get_item(
            Key=slot_key,
            ConsistentRead=True
        ).get("Item")

        if not current or current.get("status") != "AVAILABLE":
            raise ApiError(409, "Slot is no longer available")

        capacity = int(current.get("capacity", 0))
        booked = int(current.get("bookedCount", 0))
        held = int(current.get("heldCount", 0))
        version = int(current.get("version", 0))

        if capacity < 1 or booked + held >= capacity:
            raise ApiError(409, "Slot is full")

        try:
            _slots.update_item(
                Key=slot_key,
                UpdateExpression=(
                    "SET bookedCount = bookedCount + :one, "
                    "#v = #v + :one"
                ),
                ConditionExpression=(
                    "#st = :available AND #v = :version "
                    "AND bookedCount = :booked "
                    "AND heldCount = :held "
                    "AND #cap = :capacity "
                    "AND bookedCount < :max_booked"
                ),
                ExpressionAttributeNames={
                    "#st": "status",
                    "#v": "version",
                    "#cap": "capacity"
                },
                ExpressionAttributeValues={
                    ":one": 1,
                    ":available": "AVAILABLE",
                    ":version": version,
                    ":booked": booked,
                    ":held": held,
                    ":capacity": capacity,
                    ":max_booked": capacity - held,
                },
            )
            return

        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise

            if attempt >= MAX_CONFLICT_RETRIES:
                raise ApiError(409, "Booking contention; please retry")

            _metric(
                "BookingConflictRetries",
                1,
                {"Environment": os.environ.get("ENVIRONMENT", "unknown")}
            )

            time.sleep(
                random.uniform(0.01, min(0.025 * (2 ** attempt), 0.4))
            )

    raise ApiError(409, "Booking contention; please retry")


def _claim_idempotency(key, patient_id, request_hash):
    now = int(time.time())
    try:
        _idempotency.put_item(
            Item={
                "idempotencyKey": f"{patient_id}#{key}",
                "patientId": str(patient_id),
                "requestHash": request_hash,
                "status": "PENDING",
                "createdAt": now,
                "expiresAt": now + IDEMPOTENCY_TTL_SECONDS,
            },
            ConditionExpression=Attr("idempotencyKey").not_exists(),
        )
        return None
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
    record = _idempotency.get_item(
        Key={"idempotencyKey": f"{patient_id}#{key}"}, ConsistentRead=True
    ).get("Item")
    if not record:
        raise ApiError(409, "Idempotency key is being processed; retry")
    if record.get("requestHash") != request_hash:
        raise ApiError(409, "Idempotency key was already used for a different request")
    if record.get("status") == "COMPLETED" and record.get("response"):
        return json.loads(record["response"])
    recovered = _recover_committed_booking(key, patient_id)
    if recovered is not None:
        return recovered
    if record.get("status") == "FAILED":
        try:
            _idempotency.update_item(
                Key={"idempotencyKey": f"{patient_id}#{key}"},
                UpdateExpression="SET #s=:pending, createdAt=:now, expiresAt=:expires",
                ConditionExpression="#s=:failed AND requestHash=:hash",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":pending": "PENDING", ":failed": "FAILED", ":hash": request_hash,
                    ":now": now, ":expires": now + IDEMPOTENCY_TTL_SECONDS,
                },
            )
            return None
        except ClientError:
            pass
    if record.get("status") == "PENDING" and int(record.get("createdAt", now)) < now - 60:
        try:
            _idempotency.update_item(
                Key={"idempotencyKey": f"{patient_id}#{key}"},
                UpdateExpression="SET createdAt=:now, expiresAt=:expires",
                ConditionExpression="#s=:pending AND createdAt=:old AND requestHash=:hash",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":pending": "PENDING", ":old": int(record["createdAt"]),
                    ":hash": request_hash, ":now": now, ":expires": now + IDEMPOTENCY_TTL_SECONDS,
                },
            )
            return None
        except ClientError:
            pass
    raise ApiError(409, "This booking request is still being processed; retry shortly", {"retryable": True})


def _complete_idempotency(key, patient_id, appointment):
    _idempotency.update_item(
        Key={"idempotencyKey": f"{patient_id}#{key}"},
        UpdateExpression="SET #s = :completed, appointmentId = :aid, #r = :response",
        ConditionExpression="#s = :pending",
        ExpressionAttributeNames={"#s": "status", "#r": "response"},
        ExpressionAttributeValues={
            ":completed": "COMPLETED", ":pending": "PENDING",
            ":aid": str(appointment["appointment_id"]),
            ":response": json.dumps(appointment, default=_json_safe),
        },
    )


def _outbox(cur, aggregate_type, aggregate_id, event_type, payload):
    cur.execute(
        """INSERT INTO event_outbox
           (event_id, aggregate_type, aggregate_id, event_type, payload, status, created_at)
           VALUES (%s, %s, %s, %s, %s, 'PENDING', UTC_TIMESTAMP())""",
        (str(uuid.uuid4()), aggregate_type, str(aggregate_id), event_type,
         json.dumps(payload, default=_json_safe, separators=(",", ":"))),
    )


def book(event, correlation):
    patient_id, _ = _identity(event, "PATIENT")
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    idem_key = headers.get("idempotency-key")
    if not isinstance(idem_key, str) or not 8 <= len(idem_key) <= 128:
        raise ApiError(400, "Idempotency-Key header (8-128 characters) is required")
    data = body_json(event)
    doctor_id = data.get("doctorId")
    slot_id = data.get("slotId")
    if isinstance(doctor_id, bool) or not str(doctor_id or "").isdigit() or int(doctor_id) < 1:
        raise ApiError(400, "Invalid doctorId")
    if not isinstance(slot_id, str) or not slot_id.strip() or len(slot_id) > 255:
        raise ApiError(400, "Invalid slotId")
    doctor_id = int(doctor_id)
    request_hash = hashlib.sha256(json.dumps({"doctorId": doctor_id, "slotId": slot_id}, sort_keys=True).encode()).hexdigest()
    replay = _claim_idempotency(idem_key, patient_id, request_hash)
    if replay is not None:
        return response(200, replay, correlation)
    try:
        slot = _slot(doctor_id, slot_id)
        if not slot:
            raise ApiError(404, "Slot not found")
        start = datetime.fromisoformat(str(slot["startTimeUTC"]).replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        start = start.astimezone(timezone.utc).replace(tzinfo=None)
        if start <= datetime.now(timezone.utc).replace(tzinfo=None):
            raise ApiError(400, "Cannot book a slot in the past")
    except Exception:
        try:
            _idempotency.update_item(
                Key={"idempotencyKey": f"{patient_id}#{idem_key}"},
                UpdateExpression="SET #s=:failed",
                ConditionExpression="#s=:pending",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":failed": "FAILED", ":pending": "PENDING"},
            )
        except Exception:
            pass
        raise

    conn = db_connection()
    reserved = False
    committed = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM patients WHERE user_id=%s", (patient_id,))
            if not cur.fetchone():
                raise ApiError(404, "Patient profile not found")
            cur.execute("SELECT user_id FROM doctors WHERE user_id=%s", (doctor_id,))
            if not cur.fetchone():
                raise ApiError(404, "Doctor not found")
            # Serialize only this patient's booking-limit check, not all bookings.
            cur.execute("SELECT user_id FROM patients WHERE user_id=%s FOR UPDATE", (patient_id,))
            cur.execute(
                """SELECT COUNT(*) AS total FROM appointments
                   WHERE patient_id=%s AND doctor_id=%s AND status='CONFIRMED'
                   AND start_time_utc > UTC_TIMESTAMP()""",
                (patient_id, doctor_id),
            )
            if int(cur.fetchone()["total"]) >= UPCOMING_LIMIT:
                raise ApiError(409, "Maximum of 3 upcoming appointments with this doctor reached")
            cur.execute(
                "SELECT appointment_id FROM appointments WHERE patient_id=%s AND slot_id=%s AND status='CONFIRMED' LIMIT 1",
                (patient_id, slot_id),
            )
            if cur.fetchone():
                raise ApiError(409, "You already have an active booking for this slot")
            cur.execute(
                "SELECT fee FROM consultation_types WHERE consultation_type_id=%s AND doctor_id=%s",
                (int(slot["consultationTypeId"]), doctor_id),
            )
            consult = cur.fetchone()
            if not consult:
                raise ApiError(409, "Slot consultation type is no longer available")
            fee = Decimal(str(consult["fee"]))

        _reserve_seat(doctor_id, slot)
        reserved = True
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO appointments
                   (patient_id, doctor_id, slot_id, start_time_utc, status, fee, idempotency_key)
                   VALUES (%s,%s,%s,%s,'CONFIRMED',%s,%s)""",
                (patient_id, doctor_id, slot_id, start, fee, f"{patient_id}#{idem_key}"),
            )
            appointment_id = cur.lastrowid
            appointment = {
                "appointment_id": appointment_id, "patient_id": patient_id,
                "doctor_id": doctor_id, "slot_id": slot_id,
                "start_time_utc": start.isoformat() + "Z", "status": "CONFIRMED",
                "fee": str(fee),
            }
            _outbox(cur, "APPOINTMENT", appointment_id, "AppointmentBooked", appointment)
        conn.commit()
        committed = True
        _complete_idempotency(idem_key, patient_id, appointment)
        return response(201, appointment, correlation)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        if reserved and not committed:
            # A network error during COMMIT can leave the commit outcome unknown.
            # Check the RDS idempotency key before compensating the DynamoDB seat.
            try:
                committed = _recover_committed_booking(idem_key, patient_id) is not None
            except Exception:
                log.exception("Could not determine booking commit outcome; leaving reservation for recovery")
                committed = True
        if reserved and not committed:
            try:
                _release_seat(doctor_id, slot["slotKey"])
            except Exception:
                log.exception("Booking compensation failed doctorId=%s slotId=%s", doctor_id, slot_id)
        # Mark request failed only if no appointment was committed; if commit succeeded
        # but idempotency completion failed, the RDS unique key allows safe recovery.
        try:
            if not committed:
                _idempotency.update_item(
                    Key={"idempotencyKey": f"{patient_id}#{idem_key}"},
                    UpdateExpression="SET #s = :failed",
                    ConditionExpression="#s = :pending",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":failed": "FAILED", ":pending": "PENDING"},
            )
        except Exception:
            pass
        raise
    finally:
        conn.close()


def list_appointments(event, correlation):
    user_id, role = _identity(event)
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            if role == "PATIENT":
                cur.execute("""SELECT appointment_id,patient_id,doctor_id,slot_id,start_time_utc,status,fee,created_at
                               FROM appointments WHERE patient_id=%s ORDER BY start_time_utc DESC LIMIT 100""", (user_id,))
            else:
                cur.execute("""SELECT appointment_id,patient_id,doctor_id,slot_id,start_time_utc,status,fee,created_at
                               FROM appointments WHERE doctor_id=%s ORDER BY start_time_utc DESC LIMIT 100""", (user_id,))
            return response(200, {"appointments": cur.fetchall()}, correlation)
    finally:
        conn.close()


def appointment_detail(event, correlation):
    user_id, role = _identity(event)
    raw = (event.get("pathParameters") or {}).get("appointmentId")
    if not str(raw or "").isdigit():
        raise ApiError(400, "Invalid appointmentId")
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            owner_column = "patient_id" if role == "PATIENT" else "doctor_id"
            cur.execute(
                f"SELECT appointment_id,patient_id,doctor_id,slot_id,start_time_utc,status,fee,created_at FROM appointments WHERE appointment_id=%s AND {owner_column}=%s",
                (int(raw), user_id),
            )
            row = cur.fetchone()
            if not row:
                raise ApiError(404, "Appointment not found")
            return response(200, row, correlation)
    finally:
        conn.close()


def cancel_appointment(event, correlation):
    patient_id, _ = _identity(event, "PATIENT")
    raw = (event.get("pathParameters") or {}).get("appointmentId")
    if not str(raw or "").isdigit():
        raise ApiError(400, "Invalid appointmentId")
    appointment_id = int(raw)
    request_data = body_json(event)
    cancellation_reason = str(request_data.get("reason") or "Patient requested cancellation").strip()[:200]
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM appointments WHERE appointment_id=%s AND patient_id=%s FOR UPDATE", (appointment_id, patient_id))
            appt = cur.fetchone()
            if not appt:
                raise ApiError(404, "Appointment not found")
            if appt["status"] != "CONFIRMED":
                raise ApiError(409, "Appointment is not cancellable")
            start = appt["start_time_utc"].replace(tzinfo=timezone.utc) if appt["start_time_utc"].tzinfo is None else appt["start_time_utc"].astimezone(timezone.utc)
            now = datetime.now(timezone.utc)
            if start <= now:
                raise ApiError(409, "Past appointments cannot be cancelled")
            # Both the refund windows and percentages are read from SSM.
            policy = _refund_policy()
            tier, percentage = _refund_tier(start, now, "PATIENT", policy)
            fee = Decimal(str(appt["fee"]))
            refund = (fee * percentage / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            deduction = fee - refund
            cur.execute("UPDATE appointments SET status='CANCELLED_BY_PATIENT', cancelled_at=UTC_TIMESTAMP(), cancellation_actor='PATIENT' WHERE appointment_id=%s AND status='CONFIRMED'", (appointment_id,))
            ledger_reason = f"{tier}: {cancellation_reason}"[:255]
            cur.execute("INSERT INTO refund_ledger (appointment_id,refund_percentage,refund_amount,deduction_amount,reason) VALUES (%s,%s,%s,%s,%s)", (appointment_id, percentage, refund, deduction, ledger_reason))
            slot = _slot(appt["doctor_id"], appt["slot_id"])
            if not slot:
                raise ApiError(500, "Associated slot inventory record not found")
            _outbox(cur, "APPOINTMENT", appointment_id, "AppointmentCancelled", {"appointmentId": appointment_id, "actor": "PATIENT", "refundPercentage": str(percentage), "refundAmount": str(refund)})
            _outbox(cur, "SLOT", appt["slot_id"], "SlotSeatFreed", {"doctorId": appt["doctor_id"], "slotId": appt["slot_id"], "slotKey": slot["slotKey"], "appointmentId": appointment_id})
        conn.commit()
        _metric("CancellationsByTier", 1, {"Environment": os.environ.get("ENVIRONMENT", "unknown"), "Tier": tier})
        return response(200, {"appointmentId": appointment_id, "status": "CANCELLED_BY_PATIENT", "refundPercentage": str(percentage), "refundAmount": str(refund), "deductionAmount": str(deduction), "refundTier": tier, "reason": cancellation_reason}, correlation)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cancel_doctor_slot(event, correlation):
    doctor_id, _ = _identity(event, "DOCTOR")
    slot_id = (event.get("pathParameters") or {}).get("slotId")
    if not isinstance(slot_id, str) or not slot_id:
        raise ApiError(400, "Invalid slotId")
    slot = _slot(doctor_id, slot_id)
    if not slot:
        raise ApiError(404, "Slot not found")
    conn = db_connection()
    slot_disabled = False
    try:
        if slot.get("status") == "AVAILABLE":
            _slots.update_item(
                Key={"doctorId": str(doctor_id), "slotKey": slot["slotKey"]},
                UpdateExpression="SET #st=:inactive, #v=#v+:one",
                ConditionExpression="#st=:available",
                ExpressionAttributeNames={"#st": "status", "#v": "version"},
                ExpressionAttributeValues={":inactive": "INACTIVE", ":available": "AVAILABLE", ":one": 1},
            )
            slot_disabled = True
        elif slot.get("status") != "INACTIVE":
            raise ApiError(409, "Slot cannot be cancelled in its current state")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT appointment_id,patient_id,fee,start_time_utc FROM appointments WHERE doctor_id=%s AND slot_id=%s AND status='CONFIRMED' FOR UPDATE",
                (doctor_id, slot_id),
            )
            appointments = cur.fetchall()
            now_utc = datetime.now(timezone.utc)
            for appt in appointments:
                appointment_start = appt["start_time_utc"]
                if appointment_start.tzinfo is None:
                    appointment_start = appointment_start.replace(tzinfo=timezone.utc)
                else:
                    appointment_start = appointment_start.astimezone(timezone.utc)
                if now_utc >= appointment_start + timedelta(minutes=5):
                    raise ApiError(409, "An appointment past the 5-minute no-show threshold must be marked NO_SHOW, not cancelled")
                fee = Decimal(str(appt["fee"]))
                cur.execute(
                    "UPDATE appointments SET status='CANCELLED_BY_DOCTOR',cancelled_at=UTC_TIMESTAMP(),cancellation_actor='DOCTOR' WHERE appointment_id=%s AND status='CONFIRMED'",
                    (appt["appointment_id"],),
                )
                cur.execute(
                    "INSERT INTO refund_ledger (appointment_id,refund_percentage,refund_amount,deduction_amount,reason) VALUES (%s,100.00,%s,0.00,'DOCTOR_CANCELLED')",
                    (appt["appointment_id"], fee),
                )
                _outbox(cur, "APPOINTMENT", appt["appointment_id"], "AppointmentCancelled", {
                    "appointmentId": appt["appointment_id"], "actor": "DOCTOR",
                    "refundPercentage": "100.00", "refundAmount": str(fee),
                })
                _outbox(cur, "SLOT", slot_id, "SlotSeatFreed", {
                    "doctorId": doctor_id, "slotId": slot_id, "slotKey": slot["slotKey"], "appointmentId": appt["appointment_id"],
                })
        conn.commit()
        for _ in appointments:
            _metric("CancellationsByTier", 1, {"Environment": os.environ.get("ENVIRONMENT", "unknown"), "Tier": "DOCTOR_CANCELLED"})
        return response(200, {"slotId": slot_id, "cancelledAppointments": len(appointments), "fullRefunds": len(appointments), "status": "INACTIVE"}, correlation)
    except Exception:
        conn.rollback()
        if slot_disabled:
            try:
                _slots.update_item(
                    Key={"doctorId": str(doctor_id), "slotKey": slot["slotKey"]},
                    UpdateExpression="SET #st=:available, #v=#v+:one",
                    ConditionExpression="#st=:inactive",
                    ExpressionAttributeNames={"#st": "status", "#v": "version"},
                    ExpressionAttributeValues={":available": "AVAILABLE", ":inactive": "INACTIVE", ":one": 1},
                )
            except Exception:
                log.exception("Could not restore slot after failed doctor cancellation")
        raise
    finally:
        conn.close()


def cancel_doctor_appointment(event, correlation):
    """Cancel one appointment for this doctor and record a full refund."""
    doctor_id, _ = _identity(event, "DOCTOR")
    raw = (event.get("pathParameters") or {}).get("appointmentId")
    if not str(raw or "").isdigit():
        raise ApiError(400, "Invalid appointmentId")
    appointment_id = int(raw)
    data = body_json(event)
    reason = str(data.get("reason") or "Doctor cancelled appointment").strip()[:200]
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT appointment_id,patient_id,doctor_id,slot_id,start_time_utc,fee,status FROM appointments WHERE appointment_id=%s AND doctor_id=%s FOR UPDATE", (appointment_id, doctor_id))
            appt = cur.fetchone()
            if not appt:
                raise ApiError(404, "Appointment not found")
            if appt["status"] != "CONFIRMED":
                raise ApiError(409, "Only confirmed appointments can be cancelled")
            appointment_start = appt["start_time_utc"]
            if appointment_start.tzinfo is None:
                appointment_start = appointment_start.replace(tzinfo=timezone.utc)
            else:
                appointment_start = appointment_start.astimezone(timezone.utc)
            if datetime.now(timezone.utc) >= appointment_start + timedelta(minutes=5):
                raise ApiError(409, "This appointment has passed the 5-minute no-show threshold. Mark it NO_SHOW instead.")
            fee = Decimal(str(appt["fee"]))
            cur.execute("UPDATE appointments SET status='CANCELLED_BY_DOCTOR',cancelled_at=UTC_TIMESTAMP(),cancellation_actor='DOCTOR' WHERE appointment_id=%s AND status='CONFIRMED'", (appointment_id,))
            ledger_reason = ("DOCTOR_CANCELLED: " + reason)[:255]
            cur.execute("INSERT INTO refund_ledger (appointment_id,refund_percentage,refund_amount,deduction_amount,reason) VALUES (%s,100.00,%s,0.00,%s)", (appointment_id, fee, ledger_reason))
            slot = _slot(appt["doctor_id"], appt["slot_id"])
            if not slot:
                raise ApiError(500, "Associated slot inventory record not found")
            _outbox(cur, "APPOINTMENT", appointment_id, "AppointmentCancelled", {"appointmentId": appointment_id, "actor": "DOCTOR", "refundPercentage": "100.00", "refundAmount": str(fee), "reason": reason})
            _outbox(cur, "SLOT", appt["slot_id"], "SlotSeatFreed", {"doctorId": appt["doctor_id"], "slotId": appt["slot_id"], "slotKey": slot["slotKey"], "appointmentId": appointment_id})
        conn.commit()
        return response(200, {"appointmentId": appointment_id, "status": "CANCELLED_BY_DOCTOR", "refundPercentage": "100.00", "refundAmount": str(fee)}, correlation)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def doctor_appointments(event, correlation):
    doctor_id, _ = _identity(event, "DOCTOR")
    params = event.get("queryStringParameters") or {}
    allowed = {"CONFIRMED", "COMPLETED", "CANCELLED_BY_PATIENT", "CANCELLED_BY_DOCTOR", "NO_SHOW"}
    status = params.get("status")
    if status and status not in allowed:
        raise ApiError(400, "Invalid appointment status")
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            sql = "SELECT appointment_id,patient_id,doctor_id,slot_id,start_time_utc,status,fee,created_at FROM appointments WHERE doctor_id=%s"
            args = [doctor_id]
            if params.get("from"):
                sql += " AND start_time_utc >= %s"
                args.append(params["from"])
            if params.get("to"):
                sql += " AND start_time_utc <= %s"
                args.append(params["to"])
            if status:
                sql += " AND status=%s"
                args.append(status)
            sql += " ORDER BY start_time_utc LIMIT 100"
            cur.execute(sql, tuple(args))
            appointments = cur.fetchall()
            # Include the canonical slot end time so the dashboard can render
            # the completion action accurately. The outcome endpoint validates
            # the same condition independently on the server.
            for appointment in appointments:
                slot = _slot(appointment["doctor_id"], appointment["slot_id"])
                appointment["end_time_utc"] = (slot or {}).get("endTimeUTC")
            return response(200, {"appointments": appointments}, correlation)
    finally:
        conn.close()


def appointment_outcome(event, correlation):
    doctor_id, _ = _identity(event, "DOCTOR")
    raw = (event.get("pathParameters") or {}).get("appointmentId")
    if not str(raw or "").isdigit():
        raise ApiError(400, "Invalid appointmentId")
    data = body_json(event)
    outcome = data.get("outcome")
    if outcome not in ("COMPLETED", "NO_SHOW"):
        raise ApiError(400, "outcome must be COMPLETED or NO_SHOW")
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT appointment_id,patient_id,doctor_id,slot_id,fee,status,start_time_utc FROM appointments WHERE appointment_id=%s AND doctor_id=%s FOR UPDATE", (int(raw), doctor_id))
            appt = cur.fetchone()
            if not appt:
                raise ApiError(404, "Appointment not found")
            if appt["status"] != "CONFIRMED":
                raise ApiError(409, "Only confirmed appointments can receive an outcome")
            if outcome == "COMPLETED":
                slot = _slot(appt["doctor_id"], appt["slot_id"])
                if not slot or not slot.get("endTimeUTC"):
                    raise ApiError(500, "Unable to verify the scheduled appointment end time")
                try:
                    end_time = datetime.fromisoformat(str(slot["endTimeUTC"]).replace("Z", "+00:00"))
                    if end_time.tzinfo is None:
                        end_time = end_time.replace(tzinfo=timezone.utc)
                    else:
                        end_time = end_time.astimezone(timezone.utc)
                except (TypeError, ValueError):
                    raise ApiError(500, "The scheduled appointment end time is invalid")
                if datetime.now(timezone.utc) < end_time:
                    raise ApiError(409, "Appointment can be marked completed only after its scheduled end time", {"eligibleAt": end_time.isoformat()})
            if outcome == "NO_SHOW":
                start = appt["start_time_utc"]
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                else:
                    start = start.astimezone(timezone.utc)
                eligible_at = start + timedelta(minutes=5)
                now = datetime.now(timezone.utc)
                if not _no_show_eligible(start, now):
                    remaining = max(1, int((eligible_at - now).total_seconds() + 0.999))
                    raise ApiError(409, "No-show can be marked only 5 minutes after appointment start", {"eligibleAt": eligible_at.isoformat(), "secondsRemaining": remaining})
            cur.execute("UPDATE appointments SET status=%s WHERE appointment_id=%s AND status='CONFIRMED'", (outcome, int(raw)))
            if outcome == "NO_SHOW":
                fee = Decimal(str(appt["fee"]))
                cur.execute("INSERT INTO refund_ledger (appointment_id,refund_percentage,refund_amount,deduction_amount,reason) VALUES (%s,0.00,0.00,%s,'NO_SHOW')", (int(raw), fee))
        conn.commit()
        return response(200, {"appointmentId": int(raw), "status": outcome, "refundAmount": "0.00" if outcome == "NO_SHOW" else None}, correlation)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def refund_history(event, correlation):
    patient_id, _ = _identity(event, "PATIENT")
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT r.refund_id,r.appointment_id,r.refund_percentage,r.refund_amount,r.deduction_amount,r.reason,r.created_at
                           FROM refund_ledger r JOIN appointments a ON a.appointment_id=r.appointment_id
                           WHERE a.patient_id=%s ORDER BY r.created_at DESC LIMIT 100""", (patient_id,))
            return response(200, {"refunds": cur.fetchall()}, correlation)
    finally:
        conn.close()


def _release_seat_once(payload):
    appointment_id = str(payload["appointmentId"])
    release_key = f"seat-release#{appointment_id}"
    client = _slots.meta.client
    try:
        client.transact_write_items(TransactItems=[
            {"Put": {
                "TableName": os.environ["IDEMPOTENCY_TABLE"],
                "Item": {
                    "idempotencyKey": {"S": release_key},
                    "status": {"S": "COMPLETED"},
                    "createdAt": {"N": str(int(time.time()))},
                    "expiresAt": {"N": str(int(time.time()) + 30 * 24 * 3600)},
                },
                "ConditionExpression": "attribute_not_exists(idempotencyKey)",
            }},
            {"Update": {
                "TableName": os.environ["SLOTS_TABLE"],
                "Key": {"doctorId": {"S": str(payload["doctorId"])}, "slotKey": {"S": str(payload["slotKey"])}},
                "UpdateExpression": "SET bookedCount = bookedCount - :one, #v = #v + :one",
                "ConditionExpression": "bookedCount > :zero",
                "ExpressionAttributeNames": {"#v": "version"},
                "ExpressionAttributeValues": {":one": {"N": "1"}, ":zero": {"N": "0"}},
            }},
        ])
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code not in ("TransactionCanceledException", "ConditionalCheckFailedException"):
            raise
        prior = _idempotency.get_item(Key={"idempotencyKey": release_key}, ConsistentRead=True).get("Item")
        if prior and prior.get("status") == "COMPLETED":
            return
        raise


def dispatch_outbox(event, correlation):
    if event.get("source") != "medislot.outbox.scheduler" and event.get("operation") != "dispatch_outbox":
        raise ApiError(403, "Internal dispatcher invocation required")
    limit = min(max(int(event.get("limit", 25)), 1), 100)
    conn = db_connection()
    published = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT event_id,event_type,payload FROM event_outbox WHERE status='PENDING' ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED", (limit,))
            rows = cur.fetchall()
        for row in rows:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            if row["event_type"] == "SlotSeatFreed":
                payload["slotKey"] = payload.get("slotKey") or _slot(payload["doctorId"], payload["slotId"])["slotKey"]
                _release_seat_once(payload)
            result = _events.put_events(Entries=[{
                "EventBusName": os.environ["EVENT_BUS_NAME"],
                "Source": "medislot.appointments",
                "DetailType": row["event_type"],
                "Detail": json.dumps(payload, default=_json_safe, separators=(",", ":")),
            }])
            if result.get("FailedEntryCount", 0) == 0:
                with conn.cursor() as cur:
                    cur.execute("UPDATE event_outbox SET status='PUBLISHED',published_at=UTC_TIMESTAMP(),attempt_count=attempt_count+1 WHERE event_id=%s AND status='PENDING'", (row["event_id"],))
                published += 1
            else:
                with conn.cursor() as cur:
                    cur.execute("UPDATE event_outbox SET attempt_count=attempt_count+1,last_error=%s WHERE event_id=%s", (json.dumps(result.get("Entries", []))[:1000], row["event_id"]))
        conn.commit()
        return {"published": published, "examined": len(rows)}
    finally:
        conn.close()


def lambda_handler(event, context):
    correlation = cid(event)
    try:
        if event.get("operation") == "dispatch_outbox" or event.get("source") == "medislot.outbox.scheduler":
            return dispatch_outbox(event, correlation)
        resource = event.get("resource", "")
        method = event.get("httpMethod", "")
        if resource == "/appointments" and method == "POST":
            return book(event, correlation)
        if resource == "/appointments" and method == "GET":
            return list_appointments(event, correlation)
        if resource == "/appointments/{appointmentId}" and method == "GET":
            return appointment_detail(event, correlation)
        if resource == "/appointments/{appointmentId}" and method == "DELETE":
            return cancel_appointment(event, correlation)
        if resource == "/patients/me/refunds" and method == "GET":
            return refund_history(event, correlation)
        if resource == "/doctors/me/slots/{slotId}" and method == "DELETE":
            return cancel_doctor_slot(event, correlation)
        if resource == "/doctors/me/appointments" and method == "GET":
            return doctor_appointments(event, correlation)
        if resource == "/doctors/me/appointments/{appointmentId}" and method == "DELETE":
            return cancel_doctor_appointment(event, correlation)
        if resource == "/doctors/me/appointments/{appointmentId}/outcome" and method == "POST":
            return appointment_outcome(event, correlation)
        raise ApiError(404, "Route not found")
    except ApiError as exc:
        return response(exc.status, {"error": exc.message, **exc.extra}, correlation)
    except ValueError as exc:
        return response(400, {"error": str(exc) or "Invalid request"}, correlation)
    except Exception:
        log.exception("Appointment API failure correlationId=%s", correlation)
        return response(500, {"error": "Internal server error"}, correlation)
