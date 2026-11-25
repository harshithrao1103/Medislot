import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from botocore.exceptions import ClientError

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

import appointment_api_lambda as api


class AtomicFakeSlots:
    """Thread-safe model of the conditional slot update used by the Lambda."""
    def __init__(self, capacity=3):
        self.item = {
            "doctorId": "12", "slotKey": "2026-12-01T10:00:00Z#slot-1",
            "slotId": "slot-1", "capacity": capacity, "bookedCount": 0,
            "version": 0, "status": "AVAILABLE",
        }
        self.lock = threading.Lock()

    def get_item(self, **kwargs):
        with self.lock:
            return {"Item": dict(self.item)}

    def update_item(self, **kwargs):
        values = kwargs["ExpressionAttributeValues"]
        with self.lock:
            valid = (
                self.item["status"] == values[":available"]
                and self.item["version"] == values[":version"]
                and self.item["bookedCount"] == values[":booked"]
                and self.item["capacity"] == values[":capacity"]
                and self.item["bookedCount"] < values[":capacity"]
            )
            if not valid:
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException", "Message": "condition failed"}},
                    "UpdateItem",
                )
            self.item["bookedCount"] += 1
            self.item["version"] += 1
            return {"Attributes": dict(self.item)}


def test_20_concurrent_bookings_capacity_three_exactly_three_succeed(monkeypatch):
    table = AtomicFakeSlots(capacity=3)
    monkeypatch.setattr(api, "_slots", table)
    slot = dict(table.item)

    def attempt(_):
        try:
            api._reserve_seat(12, slot)
            return True
        except api.ApiError as exc:
            assert exc.status == 409
            return False

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(attempt, range(20)))

    assert sum(results) == 3
    assert table.item["bookedCount"] == 3
    assert table.item["bookedCount"] <= table.item["capacity"]
