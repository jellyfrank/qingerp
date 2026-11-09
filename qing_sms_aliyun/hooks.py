from odoo import SUPERUSER_ID, api


def migrate_legacy(env):
    """Local, repeatable migration only: never query providers or send during upgrade."""
    Account = env['qing.sms.account'].sudo().with_context(active_test=False)
    LegacyTemplate = env['qing.sms.aliyun.template'].sudo()
    companies = env['res.company'].sudo().search([('aliyun_sms_config_id', '!=', False)])
    for company in companies:
        config = company.aliyun_sms_config_id
        domain = [('legacy_config_id', '=', config.id), ('company_id', '=', company.id)]
        account = Account.search(domain, limit=1)
        if not account:
            account = Account.create({'name': config.name, 'provider': 'aliyun', 'company_id': company.id,
                                      'key': config.key, 'secret': config.secret, 'legacy_config_id': config.id})
        if not config.sign:
            continue
        Sign = env['qing.sms.sign'].sudo()
        sign = Sign.search([('account_id', '=', account.id), ('remote_id', '=', config.sign)], limit=1)
        if not sign:
            sign = Sign.create({'account_id': account.id, 'name': config.sign, 'remote_id': config.sign})
        for legacy in LegacyTemplate.search([]):
            Remote = env['qing.sms.remote.template'].sudo()
            remote = Remote.search([('account_id', '=', account.id), ('remote_id', '=', legacy.code)], limit=1)
            if not remote:
                remote = Remote.create({'account_id': account.id, 'remote_id': legacy.code, 'name': legacy.name})
            Binding = env['qing.sms.binding'].sudo()
            if Binding.search_count([('account_id', '=', account.id), ('remote_template_id', '=', remote.id)]):
                continue
            template = env['sms.template'].sudo().create({'name': legacy.name,
                'model_id': env['ir.model']._get('res.partner').id, 'body': '待同步云端模板'})
            Binding.create({'name': legacy.name, 'account_id': account.id, 'sign_id': sign.id,
                            'remote_template_id': remote.id, 'template_id': template.id})
    # Import nonterminal pre-upgrade sends into the durable query-only ledger.
    old_sms = env['sms.sms'].sudo().search([('service_provider', '=', 'aliyun'),
        ('state', 'in', ['pending', 'process', 'error']), ('domestic_message_id', '=', False)])
    from odoo.addons.qing_sms.models.constants import fingerprint
    for sms in old_sms:
        company = sms.aliyun_company_id or env.company
        bindings = env['qing.sms.binding'].sudo().search([
            ('company_id', '=', company.id), ('account_id.legacy_config_id', '=', company.aliyun_sms_config_id.id),
            ('remote_template_id.remote_id', '=', sms.aliyun_template.code)], limit=2)
        if len(bindings) != 1:
            sms.write({'state': 'process', 'to_delete': False, 'aliyun_error_msg': 'LEGACY_REVIEW_REQUIRED'})
            continue
        binding = bindings
        state = 'accepted' if sms.state == 'pending' and sms.aliyun_biz_id else 'unknown'
        message = env['qing.sms.message']._import_legacy({
            'account_id': binding.account_id.id, 'binding_id': binding.id, 'company_id': company.id,
            'event_key': 'legacy:' + sms.uuid, 'payload_hash': fingerprint(['legacy', sms.uuid]),
            'number': sms.number, 'parameters': {}, 'body': '升级前历史短信', 'sms_id': sms.id,
            'sms_uuid': sms.uuid, 'template_hash': binding._fingerprint(),
            'remote_code': sms.aliyun_template.code, 'sign_name': binding.sign_id.name,
            'state': state, 'provider_id': sms.aliyun_biz_id, 'request_id': sms.aliyun_request_id,
            'submitted_at': sms.create_date, 'error_code': 'LEGACY_RECONCILIATION',
        })
        sms.write({'domestic_message_id': message.id, 'to_delete': False,
                   'state': 'pending' if state == 'accepted' else 'process'})


def post_init_hook(env):
    migrate_legacy(env)


def pre_init_hook(env):
    modules = env['ir.module.module'].sudo().search([
        ('name', 'in', ['mommy_sms', 'mommy_aliyun_sms', 'mommy_aliyun_sms_plus']),
        ('state', 'in', ['installed', 'to install', 'to upgrade']),
    ])
    if modules:
        from odoo.exceptions import UserError
        raise UserError('请先执行 QingERP 短信命名空间迁移；不要并行安装或卸载旧短信模块。')
