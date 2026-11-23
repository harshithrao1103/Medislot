import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Lambda modules initialize AWS clients at import; disable metadata and use local test region.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("SLOTS_TABLE", "test-slots")
os.environ.setdefault("IDEMPOTENCY_TABLE", "test-idempotency")
os.environ.setdefault("REFUND_POLICY_PARAM", "/test/refund-policy")
os.environ.setdefault("EVENT_BUS_NAME", "test-events")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "3306")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("DB_SECRET_ARN", "test-secret")
# The local execution image may not have the Lambda-only PyMySQL dependency.
# These unit tests never open a database connection.
import types
pymysql_stub = types.ModuleType("pymysql")
pymysql_stub.cursors = types.SimpleNamespace(DictCursor=object)
pymysql_stub.connect = lambda **kwargs: None
sys.modules.setdefault("pymysql", pymysql_stub)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambdas/_shared"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambdas/appointment_api"))

from appointment_api_lambda import _refund_tier, _identity, ApiError


def test_refund_tier_exact_boundaries():
    now = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
    assert _refund_tier(now + timedelta(hours=48, seconds=1), now, "PATIENT")[1] == 100
    assert _refund_tier(now + timedelta(hours=48), now, "PATIENT")[1] == 75
    assert _refund_tier(now + timedelta(hours=24), now, "PATIENT")[1] == 75
    assert _refund_tier(now + timedelta(hours=24, seconds=-1), now, "PATIENT")[1] == 50
    assert _refund_tier(now + timedelta(hours=6), now, "PATIENT")[1] == 50
    assert _refund_tier(now + timedelta(hours=6, seconds=-1), now, "PATIENT")[1] == 0


def test_no_show_has_zero_refund():
    now = datetime.now(timezone.utc)
    assert _refund_tier(now + timedelta(days=2), now, "NO_SHOW")[1] == 0


def test_doctor_cancellation_always_full_refund():
    now = datetime.now(timezone.utc)
    assert _refund_tier(now + timedelta(minutes=1), now, "DOCTOR")[1] == 100


def test_identity_comes_only_from_authorizer_context():
    event = {"requestContext": {"authorizer": {"role": "patient", "userId": "17"}}, "body": '{"patientId":999}'}
    assert _identity(event, "PATIENT") == (17, "PATIENT")
    try:
        _identity({"requestContext": {"authorizer": {"role": "doctor", "userId": "17"}}}, "PATIENT")
    except ApiError as exc:
        assert exc.status == 403
    else:
        raise AssertionError("patient-only endpoint accepted a doctor")
