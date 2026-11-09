from odoo import fields, models, _
from odoo.exceptions import UserError


class SmsApplicationLink(models.TransientModel):
    _name = 'qing.sms.application.link'
    _description = 'Reconcile a cloud SMS template'

    account_id = fields.Many2one('qing.sms.account', related='application_id.account_id')
    application_id = fields.Many2one('qing.sms.template.application', required=True)
    remote_template_id = fields.Many2one('qing.sms.remote.template', required=True)

    def action_link(self):
        self.ensure_one()
        if self.remote_template_id.account_id != self.account_id:
            raise UserError(_('The cloud template belongs to another account.'))
        self.application_id.action_link_remote(self.remote_template_id)
        return {'type': 'ir.actions.act_window', 'res_model': 'qing.sms.template.application',
                'res_id': self.application_id.id, 'view_mode': 'form', 'target': 'current'}
