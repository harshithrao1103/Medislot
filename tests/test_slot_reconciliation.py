"""Offline slot-reconciliation tests; fake AWS/MySQL services, no live resources."""
import datetime as dt
import importlib.util
import sys
import types
from pathlib import Path

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
# The materialiser imports db_connection lazily; tests inject their own factory.
# Avoid importing authentication dependencies when testing pure slot reconciliation.
if 'auth_common' not in sys.modules:
    stub = types.ModuleType('auth_common')
    stub.db_connection = lambda: None
    sys.modules['auth_common'] = stub
spec = importlib.util.spec_from_file_location(
    'slot_reconciliation_under_test',
    ROOT / 'lambdas/slot_materialiser/slot_materialiser_lambda.py',
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def fail():
    raise ClientError({'Error': {'Code': 'ConditionalCheckFailedException', 'Message': 'condition'}}, 'UpdateItem')


class Table:
    def __init__(self):
        self.rows = {}
    def put_item(self, Item, ConditionExpression):
        if Item['slotId'] in self.rows:
            fail()
        self.rows[Item['slotId']] = dict(Item)
    def update_item(self, Key, UpdateExpression, ConditionExpression, ExpressionAttributeNames, ExpressionAttributeValues):
        item = self.rows[Key['slotId']]
        if ':disabled' in UpdateExpression:
            if item['status'] != 'AVAILABLE' or item['heldCount'] != 0:
                fail()
            item['status'] = 'INACTIVE'
            item['version'] += 1
        else:
            if item['status'] != 'INACTIVE' or item['bookedCount'] or item['heldCount']:
                fail()
            item['status'] = 'AVAILABLE'
            item['version'] += 1
    def query(self, **kwargs):
        return {'Items': [{'slotId': k, 'status': v['status']} for k, v in self.rows.items()]}


class Cursor:
    def __init__(self, patterns):
        self.patterns = patterns
        self.count = 0
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def execute(self, *args):
        self.count += 1
    def fetchall(self):
        return self.patterns if self.count == 1 else ([] if self.count == 2 else [{'user_id': 5}])


class Conn:
    def __init__(self, patterns):
        self.patterns = patterns
    def cursor(self):
        return Cursor(self.patterns)
    def close(self):
        pass


def test_edit_reuses_matching_slot_and_disables_old_one():
    day = dt.date(2026, 11, 9)  # Future Monday
    pattern = {'doctor_id': 5, 'consultation_type_id': 3, 'day_of_week': 0,
               'start_time': dt.time(9), 'end_time': dt.time(10),
               'duration_minutes': 30, 'capacity': 2}
    table = Table()
    run = lambda: mod.materialise(day, 1, table=table, connection_factory=lambda: Conn([pattern]))
    assert run()['created'] == 2
    original = mod.make_slots('5', pattern, day)
    first_id, reused_id = [s['slotId'] for s in original]
    table.rows[first_id]['bookedCount'] = 1
    pattern.update(start_time=dt.time(9, 30), end_time=dt.time(10, 30))
    result = run()
    assert result['created'] == 1
    assert table.rows[first_id]['status'] == 'INACTIVE'
    assert table.rows[first_id]['bookedCount'] == 1  # Appointment retained
    assert table.rows[first_id]['version'] == 1
    assert table.rows[reused_id]['status'] == 'AVAILABLE'
    assert table.rows[reused_id]['slotId'] == reused_id
    new_id = mod.make_slots('5', pattern, day)[1]['slotId']
    assert table.rows[new_id]['status'] == 'AVAILABLE'
    assert len(table.rows) == 3


def test_active_hold_prevents_immediate_deactivation():
    day = dt.date(2026, 11, 9)
    pattern = {'doctor_id': 5, 'consultation_type_id': 3, 'day_of_week': 0,
               'start_time': dt.time(9), 'end_time': dt.time(9, 30),
               'duration_minutes': 30, 'capacity': 1}
    table = Table()
    mod.materialise(day, 1, table=table, connection_factory=lambda: Conn([pattern]))
    old_id = next(iter(table.rows))
    table.rows[old_id]['heldCount'] = 1
    mod.materialise(day, 1, table=table, connection_factory=lambda: Conn([]))
    assert table.rows[old_id]['status'] == 'AVAILABLE'
    table.rows[old_id]['heldCount'] = 0
    mod.materialise(day, 1, table=table, connection_factory=lambda: Conn([]))
    assert table.rows[old_id]['status'] == 'INACTIVE'
