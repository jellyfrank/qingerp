"""Run with `odoo shell` ONLY in a disposable sms_* database. No provider network calls.

Uses real independent transactions (not TransactionCase cursor substitution) to verify
visibility, crash recovery, and concurrent workers. The disposable DB retains fixtures.
"""
import threading
from datetime import timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

from odoo import api, fields, SUPERUSER_ID

assert env.cr.dbname.startswith('sms_'), 'Disposable SMS test database required'
nonce = uuid4().hex
account = env['qing.sms.account'].create({'name': 'Worker verification ' + nonce, 'enabled': True})
account._apply_snapshot([{'remote_id': 'S', 'name': 'Test', 'audit_state': 'approved'}], [
    {'remote_id': 'T', 'name': 'Test', 'audit_state': 'approved', 'content': 'Order ${order}',
     'normalized_content': 'Order ${order}', 'variables': ['order']}])
template = env['sms.template'].create({'name': 'Worker test', 'body': 'Order',
    'model_id': env['ir.model']._get('res.partner').id})
binding = env['qing.sms.binding'].create({'name': 'Worker test', 'account_id': account.id,
    'remote_template_id': account.template_ids.id, 'sign_id': account.sign_ids.id, 'template_id': template.id})
binding.action_prepare_variables()
binding.action_review()
adapter = Mock()
adapter.validate_parameters.return_value = None
Message = env['qing.sms.message']
registry = env.registry
checks = []


def read_state(message_id):
    with registry.cursor() as cr:
        cr.execute('SELECT state FROM qing_sms_message WHERE id=%s', [message_id])
        result = cr.fetchone()
        return result[0] if result else None


def enqueue(suffix, **kwargs):
    return binding.send_sms('13800138000', {'order': 'SO001'}, event_key=nonce + suffix, **kwargs)


with patch.object(type(account), '_get_adapter', return_value=adapter):
    message = enqueue('timeout')
    assert read_state(message.id) is None
    Message._process_one(message.id)
    adapter.send.assert_not_called()
    env.cr.commit()
    checks.append('uncommitted intent invisible to worker')

    def timeout_after_submission(record):
        assert read_state(record.id) == 'unknown', 'Claim must be committed before external send'
        raise TimeoutError('signed-url-containing-secret')

    adapter.send.side_effect = timeout_after_submission
    Message._process_one(message.id)
    assert read_state(message.id) == 'unknown'
    adapter.query.return_value = {'state': 'delivered', 'code': 'OK'}
    Message._process_one(message.id)
    assert read_state(message.id) == 'delivered'
    assert adapter.send.call_count == 1
    checks.append('timeout reconciled by query; no second send')

    message = enqueue('crash')
    env.cr.commit()
    def killed_process(record):
        assert read_state(record.id) == 'unknown'
        raise KeyboardInterrupt('simulated hard process interruption')
    adapter.send.side_effect = killed_process
    try:
        Message._process_one(message.id)
    except KeyboardInterrupt:
        pass
    assert read_state(message.id) == 'unknown'
    send_count = adapter.send.call_count
    Message._process_one(message.id)
    assert read_state(message.id) == 'delivered'
    assert adapter.send.call_count == send_count
    checks.append('process interruption retains durable unknown intent')

    message = enqueue('concurrent')
    env.cr.commit()
    started, release = threading.Event(), threading.Event()
    errors = []
    def slow_send(record):
        started.set()
        assert release.wait(10)
        return {'state': 'accepted', 'code': 'OK', 'provider_id': 'test-biz'}
    adapter.send.side_effect = slow_send
    def process():
        try:
            Message._process_one(message.id)
        except BaseException as error:
            errors.append(error)
    worker = threading.Thread(target=process)
    worker.start()
    assert started.wait(10)
    count = adapter.send.call_count
    assert Message._process_one(message.id) is False
    release.set()
    worker.join(10)
    assert not errors, errors
    assert not worker.is_alive()
    assert adapter.send.call_count == count
    assert read_state(message.id) == 'accepted'
    checks.append('concurrent worker excluded across commit and network call')

    message = enqueue('scheduled', scheduled_at=fields.Datetime.now() + timedelta(hours=1))
    env.cr.commit()
    count = adapter.send.call_count
    Message._process_one(message.id)
    assert read_state(message.id) == 'queued'
    assert adapter.send.call_count == count
    checks.append('scheduled intent never sent early')

    message = enqueue('disabled')
    account.enabled = False
    env.cr.commit()
    count = adapter.send.call_count
    Message._process_one(message.id)
    assert read_state(message.id) == 'failed'
    assert adapter.send.call_count == count
    checks.append('disabled account rejected before network')

print('WORKER_VERIFICATION_PASS:', len(checks))
for check in checks:
    print('PASS:', check)
env.cr.rollback()
