"""Four required integration tests against a deployed MediSlot AWS environment.

Set M6_RUN_AWS_INTEGRATION=1 and the M6_TEST_* environment variables described in
`docs/TEST_REPORT_MILESTONE6.md` / the deployment workflow before running this module.
"""
import base64
import concurrent.futures
import datetime as dt
import json
import os
import time
import uuid

import pytest
boto3 = pytest.importorskip('boto3')
requests = pytest.importorskip('requests')
from boto3.dynamodb.conditions import Key, Attr

RUN = os.getenv('M6_RUN_AWS_INTEGRATION') == '1'
pytestmark = pytest.mark.skipif(not RUN, reason='requires deployed AWS integration fixtures and credentials')
API = os.getenv('MEDISLOT_API_BASE_URL', '').rstrip('/')
REGION = os.getenv('AWS_REGION', os.getenv('AWS_DEFAULT_REGION', 'us-east-1'))
ENVIRONMENT = os.getenv('M6_TEST_ENVIRONMENT', 'dev')
SLOTS_TABLE = os.getenv('M6_TEST_SLOTS_TABLE', 'medislot-dev-slots')
WAITLIST_TABLE = os.getenv('M6_TEST_WAITLIST_TABLE', 'medislot-dev-waitlist')
EVENT_BUS = os.getenv('M6_TEST_EVENT_BUS', 'medislot-dev-appointments')
M6_FUNCTION = os.getenv('M6_TEST_LAMBDA_NAME', 'medislot-dev-milestone6')
STATE_MACHINE_ARN = os.getenv('M6_TEST_STATE_MACHINE_ARN', '')


def token_list(name):
    return json.loads(os.environ[name])


def patient_id(token):
    payload = token.split('.')[1]
    payload += '=' * (-len(payload) % 4)
    return str(json.loads(base64.urlsafe_b64decode(payload))['sub'])


def call(method, path, token, **kwargs):
    extra_headers = kwargs.pop('headers', {})
    headers = {'Authorization': f'Bearer {token}', **extra_headers}
    return requests.request(method, API + path, headers=headers, timeout=35, **kwargs)


def slot_item(slot_id):
    table = boto3.resource('dynamodb', region_name=REGION).Table(SLOTS_TABLE)
    result = table.scan(FilterExpression=Attr('slotId').eq(str(slot_id)))
    rows = result.get('Items', [])
    while result.get('LastEvaluatedKey'):
        result = table.scan(FilterExpression=Attr('slotId').eq(str(slot_id)), ExclusiveStartKey=result['LastEvaluatedKey'])
        rows.extend(result.get('Items', []))
    assert len(rows) == 1, f'expected exactly one slot row for {slot_id}, found {len(rows)}'
    return table, rows[0]


def wait_for(predicate, timeout=45, interval=1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError('timed out waiting for deployed AWS state transition')


def cancel_if_created(token, appointment_id):
    if appointment_id:
        response = call('DELETE', f'/appointments/{appointment_id}', token, json={'reason': 'Milestone 6 integration test cleanup'})
        assert response.status_code in (200, 202, 409), response.text


def test_01_concurrency_capacity_three_exactly_three_successes():
    """20+ concurrent patient requests against a capacity-3 Health Camp slot."""
    assert API
    tokens = token_list('M6_TEST_PATIENT_TOKENS_JSON')
    assert len(tokens) >= 20 and len({patient_id(t) for t in tokens}) == len(tokens)
    slot_id = os.environ['M6_TEST_HEALTH_CAMP_SLOT_ID']
    doctor_id = int(os.environ['M6_TEST_DOCTOR_ID'])
    table, slot = slot_item(slot_id)
    assert int(slot['capacity']) == 3 and int(slot.get('bookedCount', 0)) == 0
    created = []

    def attempt(token):
        key = f"m6-concurrency-{uuid.uuid4().hex}"
        response = call('POST', '/appointments', token, json={'doctorId': doctor_id, 'slotId': slot_id}, headers={'Idempotency-Key': key})
        payload = response.json()
        return response.status_code, payload, token

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(tokens)) as pool:
            results = list(pool.map(attempt, tokens))
        successes = [r for r in results if r[0] in (200, 201)]
        failures = [r for r in results if r[0] == 409]
        assert len(successes) == 3, f'expected 3 bookings, got {len(successes)}; responses={[r[:2] for r in results]}'
        assert len(failures) == len(results) - 3
        created = [(r[2], r[1].get('appointment_id') or r[1].get('appointmentId')) for r in successes]
        fresh = table.get_item(Key={'doctorId': str(slot['doctorId']), 'slotKey': slot['slotKey']}, ConsistentRead=True)['Item']
        assert int(fresh['bookedCount']) == 3
    finally:
        for token, appointment_id in created:
            cancel_if_created(token, appointment_id)


def test_02_idempotency_replay_creates_one_appointment():
    token = os.environ['M6_TEST_IDEMPOTENCY_PATIENT_TOKEN']
    slot_id = os.environ['M6_TEST_IDEMPOTENCY_SLOT_ID']
    doctor_id = int(os.environ['M6_TEST_DOCTOR_ID'])
    key = f"m6-idempotency-{uuid.uuid4().hex}"
    first = call('POST', '/appointments', token, json={'doctorId': doctor_id, 'slotId': slot_id}, headers={'Idempotency-Key': key})
    assert first.status_code == 201, first.text
    first_body = first.json()
    second = call('POST', '/appointments', token, json={'doctorId': doctor_id, 'slotId': slot_id}, headers={'Idempotency-Key': key})
    assert second.status_code == 200, second.text
    second_body = second.json()
    first_id = first_body.get('appointment_id') or first_body.get('appointmentId')
    second_id = second_body.get('appointment_id') or second_body.get('appointmentId')
    assert first_id == second_id
    cancel_if_created(token, first_id)


def test_03_reschedule_release_failure_compensates_target_seat():
    """Directly run the deployed Saga with an invalid original slot to force release failure."""
    assert STATE_MACHINE_ARN
    token = os.environ['M6_TEST_RESCHEDULE_PATIENT_TOKEN']
    appointment_id = int(os.environ['M6_TEST_RESCHEDULE_APPOINTMENT_ID'])
    patient = int(os.environ['M6_TEST_RESCHEDULE_PATIENT_ID'])
    doctor = int(os.environ['M6_TEST_DOCTOR_ID'])
    target_id = os.environ['M6_TEST_RESCHEDULE_TARGET_SLOT_ID']
    before_appointment = call('GET', f'/appointments/{appointment_id}', token)
    assert before_appointment.status_code == 200, before_appointment.text
    original = before_appointment.json()['slot_id']
    table, target = slot_item(target_id)
    before_count = int(target.get('bookedCount', 0))
    assert target.get('status') == 'AVAILABLE' and before_count < int(target['capacity'])
    saga_id = uuid.uuid4().hex
    sf = boto3.client('stepfunctions', region_name=REGION)
    execution = sf.start_execution(stateMachineArn=STATE_MACHINE_ARN, name=f"m6-test-{saga_id[:20]}", input=json.dumps({
        'sagaId': saga_id, 'appointmentId': appointment_id, 'patientId': patient, 'doctorId': doctor,
        'originalSlotId': f'__forced_release_failure_{saga_id}__', 'originalSlotKey': '__invalid_original_slot_key__',
        'targetSlotId': target_id, 'targetSlotKey': target['slotKey'], 'correlationId': f'm6-test-{saga_id}'
    }))
    wait_for(lambda: (lambda x: x if x in ('FAILED', 'SUCCEEDED', 'TIMED_OUT', 'ABORTED') else None)(sf.describe_execution(executionArn=execution['executionArn'])['status']), timeout=90)
    final_status = sf.describe_execution(executionArn=execution['executionArn'])['status']
    assert final_status == 'FAILED', f'forced release failure should fail after compensation, got {final_status}'
    history = sf.get_execution_history(executionArn=execution['executionArn'], reverseOrder=False, maxResults=100)['events']
    entered = [event.get('stateEnteredEventDetails', {}).get('name') for event in history if event['type'] == 'TaskStateEntered']
    exited = [event.get('stateExitedEventDetails', {}).get('name') for event in history if event['type'] == 'TaskStateExited']
    assert 'ClaimTarget' in exited and 'ReleaseOriginal' in entered and 'Compensate' in exited, f'expected target claim, forced release failure and completed compensation; entered={entered}, exited={exited}'
    fresh = table.get_item(Key={'doctorId': str(target['doctorId']), 'slotKey': target['slotKey']}, ConsistentRead=True)['Item']
    assert int(fresh['bookedCount']) == before_count, 'target seat was not compensated'
    after_appointment = call('GET', f'/appointments/{appointment_id}', token)
    assert after_appointment.status_code == 200
    assert after_appointment.json()['slot_id'] == original
    assert after_appointment.json()['status'] == 'CONFIRMED'


def test_04_one_freed_seat_has_only_one_live_waitlist_offer():
    tokens = token_list('M6_TEST_WAITLIST_PATIENT_TOKENS_JSON')
    assert len(tokens) == 3
    slot_id = os.environ['M6_TEST_WAITLIST_SLOT_ID']
    table, slot = slot_item(slot_id)
    initial_count = int(slot.get('bookedCount', 0))
    assert initial_count >= int(slot['capacity']), 'test fixture slot must be full'
    patient_ids = [patient_id(t) for t in tokens]
    wl = boto3.resource('dynamodb', region_name=REGION).Table(WAITLIST_TABLE)
    seat_freed = False
    try:
        for token in tokens:
            joined = call('POST', f'/slots/{slot_id}/waitlist', token)
            assert joined.status_code == 201, joined.text

        # Simulate the atomic seat decrement that cancellation commits before publishing SlotSeatFreed.
        table.update_item(Key={'doctorId': str(slot['doctorId']), 'slotKey': slot['slotKey']},
                          UpdateExpression='SET bookedCount=bookedCount-:one, version=version+:one',
                          ConditionExpression='bookedCount>=:one', ExpressionAttributeValues={':one': 1})
        seat_freed = True
        eb = boto3.client('events', region_name=REGION)
        result = eb.put_events(Entries=[{'EventBusName': EVENT_BUS, 'Source': 'medislot.appointments', 'DetailType': 'SlotSeatFreed', 'Detail': json.dumps({'doctorId': str(slot['doctorId']), 'slotId': str(slot_id), 'slotKey': slot['slotKey'], 'correlationId': 'm6-waitlist-test'})}])
        assert result.get('FailedEntryCount', 0) == 0

        def offered_entries():
            rows = wl.query(KeyConditionExpression=Key('PK').eq(f'SLOT#{slot_id}'), ConsistentRead=True)['Items']
            return [r for r in rows if r.get('status') == 'OFFERED']

        first_offer = wait_for(offered_entries)
        assert len(first_offer) == 1
        first = first_offer[0]
        assert first['patientId'] == patient_ids[0], 'offer was not assigned FIFO'
        expiry = dt.datetime.fromisoformat(first['offerExpiresAt'])
        offered = dt.datetime.fromisoformat(first['offeredAt'])
        hold_seconds = int(boto3.client('ssm', region_name=REGION).get_parameter(Name=f'/medislot/{ENVIRONMENT}/milestone6/hold-duration-seconds')['Parameter']['Value'])
        assert hold_seconds - 10 <= (expiry - offered).total_seconds() <= hold_seconds + 10

        # Expire the first offer deterministically without waiting 15 real minutes.
        past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=5)).isoformat()
        wl.update_item(Key={'PK': first['PK'], 'SK': first['SK']}, UpdateExpression='SET offerExpiresAt=:past', ExpressionAttributeValues={':past': past})
        table.update_item(Key={'doctorId': str(slot['doctorId']), 'slotKey': slot['slotKey']}, UpdateExpression='SET holdExpiresAt=:past', ExpressionAttributeValues={':past': past})
        lam = boto3.client('lambda', region_name=REGION)
        invoked = lam.invoke(FunctionName=M6_FUNCTION, InvocationType='RequestResponse', Payload=json.dumps({'operation': 'expire_offers'}).encode())
        assert 'FunctionError' not in invoked, invoked.get('Payload').read().decode()
        second_offer = wait_for(offered_entries)
        assert len(second_offer) == 1
        assert second_offer[0]['patientId'] == patient_ids[1], 'expired offer did not pass to the next FIFO patient'
    finally:
        # Leaving active entries releases any live offer and allows the next queued entry to progress.
        for token in tokens:
            try:
                call('DELETE', f'/slots/{slot_id}/waitlist', token)
            except Exception:
                pass
        if seat_freed:
            table.update_item(Key={'doctorId': str(slot['doctorId']), 'slotKey': slot['slotKey']},
                              UpdateExpression='SET bookedCount=bookedCount+:one, version=version+:one',
                              ExpressionAttributeValues={':one': 1})
