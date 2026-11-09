"""Run in an isolated sms_* database with the pre-upgrade 20.0 addons path."""
import json
import os
from unittest.mock import Mock, patch


assert env.cr.dbname.startswith('sms_'), 'Disposable SMS database required'
path = os.environ['QING_SMS_MIGRATION_SNAPSHOT']
account = env['qing.sms.account'].create({'name': 'Migration test', 'enabled': True})
account._apply_snapshot([{'remote_id': 'S', 'name': 'Migration sign', 'audit_state': 'approved'}], [
    {'remote_id': 'T', 'name': 'Migration template', 'audit_state': 'approved',
     'content': 'Order ${order}', 'normalized_content': 'Order ${order}', 'variables': ['order']}])
template = env['sms.template'].create({'name': 'Migration business template', 'body': 'Order',
    'model_id': env['ir.model']._get('res.partner').id})
binding = env['qing.sms.binding'].create({'name': 'Migration binding', 'account_id': account.id,
    'remote_template_id': account.template_ids.id, 'sign_id': account.sign_ids.id,
    'template_id': template.id})
binding.action_prepare_variables()
binding.action_review()
adapter = Mock()
adapter.validate_parameters.return_value = None
with patch.object(type(account), '_get_adapter', return_value=adapter):
    for state in ('queued', 'unknown', 'accepted', 'delivered', 'failed', 'canceled'):
        message = binding.send_sms('13800138000', {'order': 'SO-OLD'}, event_key='migration:' + state)
        if state == 'canceled':
            message.action_cancel()
        elif state != 'queued':
            message._apply_result({'state': state, 'code': 'MIGRATION_FIXTURE'}, 'submit')
other_company = env['res.company'].create({'name': 'SMS migration other company'})
other_account = env['qing.sms.account'].with_company(other_company).create({
    'name': 'Other company account', 'company_id': other_company.id, 'enabled': False})
other_account._apply_snapshot([{'remote_id': 'S2', 'name': 'Pending sign',
    'audit_state': 'pending'}], [{'remote_id': 'T2', 'name': 'Rejected template',
    'audit_state': 'rejected', 'content': 'Other ${order}',
    'normalized_content': 'Other ${order}', 'variables': ['order']}])
env.cr.commit()
rows = env['qing.sms.message'].search([('event_key', '=like', 'migration:%')], order='event_key')
snapshot = {'binding_id': binding.id, 'account_id': account.id,
    'other_account_id': other_account.id, 'other_company_id': other_company.id,
    'other_template_id': other_account.template_ids.id,
    'messages': {record.event_key: {'id': record.id, 'state': record.state,
        'sms_uuid': record.sms_uuid, 'payload_hash': record.payload_hash,
        'sms_id': record.sms_id.id} for record in rows}}
with open(path, 'w', encoding='utf-8') as output:
    json.dump(snapshot, output, sort_keys=True)
print('MIGRATION_FIXTURE_PASS:', len(rows), 'messages across two companies')
