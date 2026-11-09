"""Run with Odoo shell only in a disposable sms_* database; provider calls are mocked."""
import threading
from datetime import timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

from odoo import fields


assert env.cr.dbname.startswith('sms_'), 'Disposable SMS database required'
nonce = uuid4().hex[:12]
account = env['qing.sms.account'].create({'name': 'Application test ' + nonce})
account._apply_snapshot([{'remote_id': 'S', 'name': 'Test sign', 'audit_state': 'approved'}], [])
Application = env['qing.sms.template.application']


def new_application(suffix):
    application = Application.create({'name': 'Test ' + suffix, 'account_id': account.id,
        'sign_id': account.sign_ids.id, 'content': 'Notification ' + suffix})
    adapter = Mock()
    adapter.validate_template_application.return_value = None
    with patch.object(type(account), '_get_adapter', return_value=adapter):
        application.action_queue()
    env.cr.commit()
    return application


def read_state(application):
    with env.registry.cursor() as cursor:
        cursor.execute('SELECT state FROM qing_sms_template_application WHERE id=%s', [application.id])
        return cursor.fetchone()[0]


first = new_application('timeout')
calls = []


def timeout(record):
    assert read_state(record) == 'unknown', 'Create intent must be durable before network'
    calls.append(record.id)
    raise TimeoutError('provider outcome unknown')


with patch.object(type(account), '_create_template_application', side_effect=timeout):
    Application._process_one(first.id)
    Application._process_one(first.id)
assert read_state(first) == 'unknown' and calls == [first.id]
env.cr.commit()

second = new_application('approved')
with patch.object(type(account), '_create_template_application', side_effect=AssertionError('throttle bypass')):
    assert Application._process_one(second.id) is False
assert read_state(second) == 'queued'
account.template_apply_not_before = fields.Datetime.now() - timedelta(seconds=1)
env.cr.commit()


def created(record):
    assert read_state(record) == 'unknown'
    calls.append(record.id)
    return {'state': 'pending', 'code': 'OK', 'remote_code': 'SMS_TEST_1'}


with patch.object(type(account), '_create_template_application', side_effect=created):
    Application._process_one(second.id)
assert read_state(second) == 'pending' and calls == [first.id, second.id]
env.cr.commit()
second._write_internal({'next_check_at': fields.Datetime.now() - timedelta(seconds=1)})
env.cr.commit()
with patch.object(type(account), '_query_template_application', return_value={'state': 'approved', 'code': 'OK'}):
    Application._process_one(second.id)
assert read_state(second) == 'approved'
env.cr.commit()

third = new_application('concurrent')
account.template_apply_not_before = fields.Datetime.now() - timedelta(seconds=1)
env.cr.commit()
entered, release = threading.Event(), threading.Event()
errors = []


def slow_create(record):
    assert read_state(record) == 'unknown'
    entered.set()
    assert release.wait(10)
    return {'state': 'pending', 'code': 'OK', 'remote_code': 'SMS_TEST_2'}


def worker():
    try:
        Application._process_one(third.id)
    except BaseException as error:
        errors.append(error)


with patch.object(type(account), '_create_template_application', side_effect=slow_create):
    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(10)
    assert Application._process_one(third.id) is False
    release.set()
    thread.join(10)
assert not errors and not thread.is_alive() and read_state(third) == 'pending'
print('APPLICATION_WORKER_VERIFICATION_PASS: unknown, no retry, throttle, query, concurrent lock')
env.cr.rollback()
