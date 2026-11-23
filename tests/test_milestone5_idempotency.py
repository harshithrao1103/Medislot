import json
import os
import sys
import types
from pathlib import Path

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
pymysql_stub = types.ModuleType("pymysql")
pymysql_stub.cursors = types.SimpleNamespace(DictCursor=object)
pymysql_stub.connect = lambda **kwargs: None
sys.modules.setdefault("pymysql", pymysql_stub)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambdas/appointment_api"))
import appointment_api_lambda as api


class FakeIdempotencyTable:
    def __init__(self):
        self.items = {}
    def put_item(self, Item, ConditionExpression=None):
        key = Item["idempotencyKey"]
        if key in self.items:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}}, "PutItem")
        self.items[key] = dict(Item)
    def get_item(self, Key, ConsistentRead=False):
        return {"Item": self.items.get(Key["idempotencyKey"])}
    def update_item(self, Key, UpdateExpression, ConditionExpression=None, ExpressionAttributeNames=None, ExpressionAttributeValues=None):
        item = self.items[Key["idempotencyKey"]]
        item["status"] = "COMPLETED"
        item["response"] = ExpressionAttributeValues[":response"]
        item["appointmentId"] = ExpressionAttributeValues[":aid"]


def test_same_idempotency_key_replays_original_appointment(monkeypatch):
    table = FakeIdempotencyTable()
    monkeypatch.setattr(api, "_idempotency", table)
    key = "request-key-123"
    patient_id = 44
    request_hash = "same-normalized-request-hash"
    assert api._claim_idempotency(key, patient_id, request_hash) is None
    original = {"appointment_id": 9001, "patient_id": 44, "doctor_id": 12, "slot_id": "slot-x", "status": "CONFIRMED"}
    api._complete_idempotency(key, patient_id, original)
    replay = api._claim_idempotency(key, patient_id, request_hash)
    assert replay == original
    assert len(table.items) == 1
    assert table.items[f"{patient_id}#{key}"]["appointmentId"] == "9001"
