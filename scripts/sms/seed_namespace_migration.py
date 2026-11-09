"""Run with OLD code in disposable sms_qing_migrate database only."""
import json
from unittest.mock import patch
from odoo import fields
from odoo.addons.mommy_aliyun_sms.hooks import migrate_legacy

assert env.cr.dbname == 'sms_qing_migrate'
config = env['mommy.aliyun.sms.config'].create({'name': 'Namespace fixture', 'key': 'dummy', 'secret': 'dummy', 'sign': '迁移签名'})
env.company.aliyun_sms_config_id = config
legacy = env['mommy.aliyun.sms.template'].create({'name': '迁移模板', 'code': 'SMS_MIGRATE'})
env.company.aliyun_delivery_template_id = legacy
migrate_legacy(env)
binding = legacy._get_binding(env.company)
account = binding.account_id
account.enabled = True
account._apply_snapshot([{'remote_id': '迁移签名', 'name': '迁移签名', 'audit_state': 'approved'}], [
    {'remote_id': 'SMS_MIGRATE', 'name': '订单', 'content': '订单 ${order}', 'normalized_content': '订单 ${order}',
     'variables': ['order'], 'audit_state': 'approved'}])
binding.action_prepare_variables()
binding.action_review()
messages = env['mommy.sms.message']
for state in ['queued', 'unknown', 'accepted', 'delivered', 'failed']:
    message = binding.send_sms('13800138000', {'order': 'SO001'}, event_key='namespace-' + state)
    if state != 'queued':
        message._write_internal({'state': state, 'submitted_at': fields.Datetime.now(), 'provider_id': 'biz-' + state})
    messages |= message
user = env['res.users'].create({'name': 'SMS migration sender', 'login': 'sms-migration-sender',
    'group_ids': [(4, env.ref('mommy_sms.group_sms_user').id)]})
action = env['ir.actions.server'].create({'name': 'SMS custom reference', 'model_id': env['ir.model']._get('mommy.sms.account').id,
    'state': 'code', 'code': "records = env['mommy.sms.account']; group = env.ref('mommy_sms.group_sms_user')"})
snapshot = {'action': action.id, 'account': account.id, 'binding': binding.id, 'messages': messages.ids,
    'uuids': messages.mapped('sms_uuid'), 'states': messages.mapped('state'), 'user': user.id,
    'revision': binding.remote_template_id.revision_ids.ids,
    'group': env.ref('mommy_sms.group_sms_user').id,
    'config': config.id, 'template': legacy.id}
env['ir.config_parameter'].set_str('sms.namespace.fixture', json.dumps(snapshot))
env['ir.cron'].search([]).write({'active': False})
env.cr.commit()
print('MIGRATION_FIXTURE_READY: 5 states, group membership, legacy references, template history')
