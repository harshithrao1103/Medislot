"""Local infrastructure/API contract tests; these are not deployed-AWS integration tests."""
import pathlib
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]


class CfnLoader(yaml.SafeLoader):
    pass


def _intrinsic(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


CfnLoader.add_multi_constructor('!', _intrinsic)


def template(name):
    return yaml.load((ROOT / 'Cloudformation' / name).read_text(), Loader=CfnLoader)


def test_waitlist_table_has_final_key_gsi_and_ttl():
    resource = template('Data_Stack.yaml')['Resources']['WaitlistTable']['Properties']
    assert resource['TableName'] == "medislot-${Environment}-waitlist"
    assert resource['KeySchema'] == [
        {'AttributeName': 'PK', 'KeyType': 'HASH'},
        {'AttributeName': 'SK', 'KeyType': 'RANGE'},
    ]
    gsi = resource['GlobalSecondaryIndexes'][0]
    assert gsi['IndexName'] == 'PatientWaitlistIndex'
    assert gsi['KeySchema'] == [
        {'AttributeName': 'GSI1PK', 'KeyType': 'HASH'},
        {'AttributeName': 'GSI1SK', 'KeyType': 'RANGE'},
    ]
    assert resource['TimeToLiveSpecification']['AttributeName'] == 'ttlExpiresAt'


def test_application_contains_saga_api_schedules_and_notification_consumer():
    resources = template('Application_Stack.yaml')['Resources']
    assert 'RescheduleStateMachine' in resources
    assert 'Milestone6Lambda' in resources
    assert 'WaitlistExpirySchedule' in resources
    assert 'ReminderSchedule' in resources
    assert 'Milestone6NotificationRule' in resources
    assert 'AppointmentRescheduleMethod' in resources
    assert 'WaitlistJoinMethod' in resources
    assert 'WaitlistLeaveMethod' in resources
    assert 'PatientWaitlistsMethod' in resources
    assert 'WaitlistOfferAcceptMethod' in resources


def test_reschedule_state_machine_has_explicit_compensation():
    definition = template('Application_Stack.yaml')['Resources']['RescheduleStateMachine']['Properties']['DefinitionString']
    assert '"ClaimTarget"' in definition
    assert '"ReleaseOriginal"' in definition
    assert '"CommitAppointment"' in definition
    assert '"Compensate"' in definition
    assert '"Next":"Compensate"' in definition
    code = (ROOT / 'lambdas' / 'milestone6' / 'milestone6_lambda.py').read_text()
    assert 'RESCHEDULE_SLOT_FREED#' in code and "'SlotSeatFreed'" in code


def test_no_nat_or_sns_sqs_notification_pipeline_was_added():
    network = template('Network_Stack.yaml')['Resources']
    assert not any('NatGateway' in name or 'NATGateway' in name for name in network)
    app = template('Application_Stack.yaml')['Resources']
    assert 'Milestone6NotificationRule' in app
    assert 'Milestone6NotificationInvokePermission' in app
    # The pre-existing appointment API DLQ remains, but no new SQS/SNS notification resource exists.
    assert not any('NotificationQueue' in name or 'NotificationTopic' in name for name in app)


def test_rds_reschedule_lock_and_existing_counter_are_preserved():
    schema = (ROOT / 'database' / 'schema.sql').read_text()
    assert 'CREATE TABLE IF NOT EXISTS reschedule_locks' in schema
    assert 'reschedule_count TINYINT UNSIGNED NOT NULL DEFAULT 0' in schema
    assert 'MIGRATE_MILESTONE6_RESCHEDULE_LOCK.sql' in (ROOT / 'README.md').read_text()


def test_patient_dashboard_has_waitlist_and_reschedule_flows():
    app = (ROOT / 'doctor_dashboard' / 'app.py').read_text()
    assert "@app.route('/patient/waitlists'" in app
    assert "@app.route('/patient/appointments/<int:appointment_id>/reschedule'" in app
    assert "@app.route('/patient/reschedule-status'" in app
    assert (ROOT / 'doctor_dashboard' / 'templates' / 'patient_waitlists.html').exists()
    assert (ROOT / 'doctor_dashboard' / 'templates' / 'patient_reschedule.html').exists()
