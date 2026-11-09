"""Verify the disposable seed after pre-registry migration and module upgrades."""
import json
from unittest.mock import patch
from odoo.exceptions import UserError

assert env.cr.dbname == 'sms_qing_migrate'
snapshot = json.loads(env['ir.config_parameter'].get_str('sms.namespace.fixture'))
assert "env['qing.sms.account']" in env['ir.actions.server'].browse(snapshot['action']).code
assert "qing_sms.group_sms_user" in env['ir.actions.server'].browse(snapshot['action']).code
account = env['qing.sms.account'].browse(snapshot['account']).exists()
binding = env['qing.sms.binding'].browse(snapshot['binding']).exists()
messages = env['qing.sms.message'].browse(snapshot['messages']).exists()
assert account.key == 'dummy' and account.secret == 'dummy'
assert not account.enabled and not account.auto_sync and not binding.review_hash
assert messages.ids == snapshot['messages']
assert messages.mapped('sms_uuid') == snapshot['uuids']
assert messages.mapped('state') == snapshot['states']
assert binding.remote_template_id.revision_ids.ids == snapshot['revision']
assert account.legacy_config_id.id == snapshot['config']
assert env.company.aliyun_sms_config_id.id == snapshot['config']
assert env.company.aliyun_delivery_template_id.id == snapshot['template']
assert env.company.qing_sms_delivery_binding_id == binding
assert env.ref('qing_sms.group_sms_user').id == snapshot['group']
user = env['res.users'].browse(snapshot['user'])
assert user.has_group('qing_sms.group_sms_user')
assert messages.with_user(user).read(['state'])
assert all(s.domestic_message_id in messages for s in messages.mapped('sms_id'))
assert not env.ref('qing_sms_aliyun.mommy_aliyun_sms_root').active
assert not env.ref('qing_sms_aliyun.mommy_aliyun_sms').active
assert not env.ref('qing_sms_aliyun.menu_sms_aliyun_template').active
assert env.ref('qing_sms.menu_sms_root').parent_id == env.ref('phone_validation.phone_menu_main')
assert not env.ref('qing_sms.ir_cron_dispatch').active
assert not env.ref('qing_sms.ir_cron_sync').active
assert not env['ir.module.module'].search([('name', 'in', ['mommy_sms', 'mommy_aliyun_sms', 'mommy_aliyun_sms_plus']), ('state', '=', 'installed')])
assert not any(name.startswith('mommy.sms.') for name in env.registry.models)
# Stored PostgreSQL uniqueness must survive the model/table rename.
env.cr.execute("SELECT count(*) FROM pg_constraint WHERE conrelid='qing_sms_message'::regclass AND contype='u'")
assert env.cr.fetchone()[0] >= 1
# After deliberate review, an existing business event must still resolve to the SAME original task.
account.enabled = True
binding.action_review()
with patch('odoo.addons.qing_sms_aliyun.services.aliyun_client.AliyunSmsClient.sendSms') as send:
    existing = binding.send_sms('13800138000', {'order': 'SO001'}, event_key='namespace-queued')
    assert existing.id == snapshot['messages'][0]
    send.assert_not_called()
# Roll back enable/review and temporary changes; keep migrated fixtures paused.
env.cr.rollback()
print('NAMESPACE_MIGRATION_PASS: IDs, UUIDs, states, history, ACLs, company FKs, menu, cron, unique constraint and event dedup preserved')
