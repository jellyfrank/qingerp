"""Run through `odoo shell` only in a disposable qing_wa_* database.
External network calls are mocked. Unlike TransactionCase, this checks durable commits.
"""
import os
import threading
import uuid
from unittest.mock import patch

from odoo import api, fields, SUPERUSER_ID
from odoo.addons.qing_whatsapp.services.cloud_api import CloudAPI, CloudError

assert env.cr.dbname.startswith('qing_wa_'), 'Disposable test database required'
label = uuid.uuid4().hex[:8]
operator = env['res.users'].with_context(no_reset_password=True).create({
    'name': 'Queue Test ' + label, 'login': 'queue_' + label,
    'group_ids': [(6, 0, [env.ref('qing_whatsapp.group_user').id])],
    'company_id': env.company.id, 'company_ids': [(6, 0, env.company.ids)]})
account = env['qing.whatsapp.account'].create({'name': 'Queue Test ' + label,
    'phone_uid': str(uuid.uuid4().int)[:15], 'waba_uid': '123', 'access_token': os.environ["QING_TEST_ACCESS_TOKEN"],
    'operator_ids': [(6, 0, operator.ids)]})
conversation = env['qing.whatsapp.conversation']._find_or_create(account, '32499000000')
conversation.last_customer_at = fields.Datetime.now()
Message = env['qing.whatsapp.message']


def queue(suffix):
    message = Message.with_user(operator)._enqueue(conversation, 'text', {'body': suffix}, request_key=label + suffix)
    env.cr.commit()
    return message


def refresh(message):
    env.cr.rollback()  # start a fresh PostgreSQL repeatable-read snapshot
    env.invalidate_all()
    return Message.browse(message.id)


success = queue('success')
observed = []


def accepted(client, payload):
    with env.registry.cursor() as cr:
        cr.execute('SELECT state FROM qing_whatsapp_message WHERE id=%s', [success.id])
        observed.append(cr.fetchone()[0])
    return 'wamid.' + label + '.success'


with patch.object(CloudAPI, 'send', accepted):
    Message._cron_send()
assert observed == ['sending'], observed
assert refresh(success).state == 'accepted'
print('PASS: sending marker committed before external IO')

unknown = queue('unknown')
with patch.object(CloudAPI, 'send', side_effect=CloudError('timeout', uncertain=True)) as call:
    Message._cron_send()
    Message._cron_send()
    assert call.call_count == 1
assert refresh(unknown).state == 'unknown'
print('PASS: uncertain result is not automatically resent')

retry = queue('retry')
with patch.object(CloudAPI, 'send', side_effect=CloudError('429', retryable=True)):
    Message._cron_send()
retry = refresh(retry)
assert retry.state == 'retry_wait' and retry.attempt_count == 1
retry.next_attempt_at = fields.Datetime.now()
env.cr.commit()
with patch.object(CloudAPI, 'send', return_value='wamid.' + label + '.retry'):
    Message._cron_send()
assert refresh(retry).state == 'accepted'
assert len(retry.attempt_ids) == 2
print('PASS: definitive throttle retries with attempt history')

race = queue('race')
started, release = threading.Event(), threading.Event()
errors, calls = [], []


def slow_send(client, payload):
    calls.append(payload['biz_opaque_callback_data'])
    started.set()
    assert release.wait(10)
    return 'wamid.' + label + '.race'


def worker():
    try:
        with env.registry.cursor() as cr:
            worker_env = api.Environment(cr, SUPERUSER_ID, {})
            worker_env['qing.whatsapp.message']._cron_send()
            cr.commit()
    except Exception as exc:
        errors.append(type(exc).__name__)


with patch.object(CloudAPI, 'send', slow_send):
    thread = threading.Thread(target=worker)
    thread.start()
    assert started.wait(10)
    worker()
    release.set()
    thread.join(15)
assert not errors, errors
assert len(calls) == 1, calls
assert refresh(race).state == 'accepted'
print('PASS: two workers do not send an in-flight message twice')

# Demonstrate business rollback: no durable outbox row survives.
rolled_key = label + 'rollback'
Message.with_user(operator)._enqueue(conversation, 'text', {'body': 'rollback'}, request_key=rolled_key)
env.cr.rollback()
assert not Message.search([('request_key', '=', rolled_key)])
print('PASS: rollback leaves no executable outbox record')
print('DELIVERY_INTEGRATION_OK')
