def post_init_hook(env):
    if 'aliyun_delivery_template_id' not in env['res.company']._fields:
        return
    for company in env['res.company'].sudo().search([('aliyun_delivery_template_id', '!=', False)]):
        if company.qing_sms_delivery_binding_id:
            continue
        template = company.aliyun_delivery_template_id
        bindings = env['qing.sms.binding'].sudo().search([
            ('company_id', '=', company.id),
            ('account_id.legacy_config_id', '=', company.aliyun_sms_config_id.id),
            ('remote_template_id.remote_id', '=', template.code),
        ], limit=2)
        if len(bindings) == 1:
            company.qing_sms_delivery_binding_id = bindings
