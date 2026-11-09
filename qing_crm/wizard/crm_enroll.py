from odoo import fields, models
from odoo.exceptions import AccessError, UserError


class CrmEnrollWizard(models.TransientModel):
    _name = 'qing.crm.enroll.wizard'
    _description = 'Enroll Customer in CRM'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    crm_team_id = fields.Many2one('crm.team', check_company=True)
    destination = fields.Selection([
        ('mine', 'My Private Customers'),
        ('public', 'Public Pool'),
    ], required=True, default='mine')
    partner_id = fields.Many2one('res.partner', required=True)
    reason = fields.Text()

    def action_confirm(self):
        self.ensure_one()
        user = self.env.user
        if not user._is_internal() or not user.has_group('sales_team.group_sale_salesman'):
            raise AccessError(self.env._('Only internal sales users can enroll customers.'))
        if self.company_id not in self.env.companies:
            raise AccessError(self.env._('Activate the target company before enrolling this customer.'))
        partner = self.partner_id.commercial_partner_id
        role = self.env['qing.partner.role'].sudo().search([
            ('partner_id', '=', partner.id), ('company_id', '=', self.company_id.id), ('role', '=', 'customer')
        ], limit=1)
        if role.crm_managed:
            raise UserError(self.env._('This customer is already managed in CRM.'))
        if not role:
            role = self.env['qing.partner.role'].sudo().create({
                'company_id': self.company_id.id,
                'partner_id': partner.id,
                'role': 'customer',
                'state': 'pending',
            })
        role.sudo().with_context(qing_crm_service=True).write({
            'crm_managed': True,
            'crm_team_id': self.crm_team_id.id,
            'lifecycle_stage': 'potential',
        })
        if self.destination == 'public':
            role.with_user(user)._crm_return(False, self.reason or self.env._('Enrolled into public pool'))
        else:
            role.with_user(user)._crm_assign(user, self.reason or self.env._('Enrolled as private customer'), 'enrolled')
        return role.with_user(user).action_open_customer()
