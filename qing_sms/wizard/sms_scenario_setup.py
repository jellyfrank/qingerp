from odoo import fields, models, _
from odoo.exceptions import AccessError, UserError


class SmsScenarioSetup(models.TransientModel):
    _name = 'qing.sms.scenario.setup'
    _description = 'Domestic SMS scenario setup'

    account_id = fields.Many2one('qing.sms.account', required=True)
    blueprint_id = fields.Many2one('qing.sms.blueprint', required=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    remote_template_id = fields.Many2one('qing.sms.remote.template', required=True)
    sender_user_id = fields.Many2one('res.users', required=True)
    sign_id = fields.Many2one('qing.sms.sign', required=True)

    def action_setup(self):
        """Bind a reviewed cloud template to a catalog blueprint without enabling sends."""
        self.ensure_one()
        if not self.env.user.has_group('qing_sms.group_sms_manager'):
            raise AccessError(_('Only SMS managers can configure scenarios.'))
        blueprint = self.blueprint_id
        remote = self.remote_template_id
        if not blueprint.event_code or self.account_id.company_id != self.company_id or remote.account_id != self.account_id or self.sign_id.account_id != self.account_id:
            raise UserError(_('Choose a matching company, account, sign and scenario blueprint.'))
        if remote.audit_state != 'approved' or remote.category != blueprint.category or remote.content != blueprint.content:
            raise UserError(_('The approved cloud template must exactly match blueprint content and category.'))
        keys = [item['key'] for item in blueprint.variable_schema or []]
        if set(remote.variables or []) != set(keys):
            raise UserError(_('Cloud template variables do not match the blueprint schema.'))
        Scenario = self.env['qing.sms.scenario']
        domain = [('company_id', '=', self.company_id.id), ('event_code', '=', blueprint.event_code)]
        scenario = Scenario.search(domain, limit=1)
        if not scenario:
            scenario = Scenario.create({'name': blueprint.name, 'company_id': self.company_id.id,
                'event_code': blueprint.event_code, 'model_id': blueprint.model_id.id,
                'sender_user_id': self.sender_user_id.id})
        else:
            scenario.write({'sender_user_id': self.sender_user_id.id})
        template = self.env['sms.template'].create({'name': blueprint.name,
            'model_id': blueprint.model_id.id, 'body': blueprint.content})
        binding = self.env['qing.sms.binding'].create({'name': blueprint.name,
            'account_id': self.account_id.id, 'sign_id': self.sign_id.id,
            'remote_template_id': remote.id, 'template_id': template.id,
            'blueprint_id': blueprint.id, 'variant_code': blueprint.variant_code})
        binding.action_prepare_variables()
        binding.action_review()
        scenario.action_set_binding(binding)
        return {'type': 'ir.actions.act_window', 'res_model': 'qing.sms.scenario',
                'res_id': scenario.id, 'view_mode': 'form', 'target': 'current'}
