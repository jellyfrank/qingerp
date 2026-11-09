"""Run after upgrading the isolated old-version fixture to the new SMS modules."""
import json
import os


assert env.cr.dbname.startswith('sms_'), 'Disposable SMS database required'
with open(os.environ['QING_SMS_MIGRATION_SNAPSHOT'], encoding='utf-8') as source:
    before = json.load(source)
binding = env['qing.sms.binding'].browse(before['binding_id'])
assert binding.exists() and binding.accept_new and binding.active
other = env['qing.sms.account'].browse(before['other_account_id'])
assert other.exists() and other.company_id.id == before['other_company_id'] and not other.enabled
assert env['qing.sms.remote.template'].browse(before['other_template_id']).audit_state == 'rejected'
assert not env['qing.sms.scenario'].search([('enabled', '=', True)])
for key, expected in before['messages'].items():
    message = env['qing.sms.message'].browse(expected['id'])
    assert message.exists() and message.event_key == key
    for field in ('state', 'sms_uuid', 'payload_hash'):
        assert message[field] == expected[field], (key, field)
    assert message.sms_id.id == expected['sms_id']
    assert not message.event_id
print('MIGRATION_VERIFICATION_PASS:', len(before['messages']))
