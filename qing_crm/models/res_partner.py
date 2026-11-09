from odoo import models
from odoo.exceptions import AccessError, UserError


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def action_qing_crm_enroll(self):
        self.ensure_one()
        partner = self.commercial_partner_id
        if partner != self:
            raise UserError(self.env._('Enroll the commercial entity instead of a child contact.'))
        user = self.env.user
        if not user._is_internal() or not user.has_group('sales_team.group_sale_salesman'):
            raise AccessError(self.env._('Only internal sales users can enroll customers.'))
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('Add to Customer Management'),
            'res_model': 'qing.crm.enroll.wizard', 'view_mode': 'form', 'target': 'new',
            'context': {'default_partner_id': partner.id, 'default_company_id': self.env.company.id},
        }
