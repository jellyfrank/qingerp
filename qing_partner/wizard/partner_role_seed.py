from odoo import fields, models
from odoo.exceptions import AccessError, UserError


class PartnerRoleSeed(models.TransientModel):
    _name = 'qing.partner.role.seed'
    _description = 'Review Historical Partner Roles'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    partner_ids = fields.Many2many('res.partner', required=True, string='Partners to review')
    use_rank_hints = fields.Boolean(
        string='Include rank hints',
        help='Ranks have no company dimension. Select this only after checking these partners belong to the selected company.',
    )

    def action_seed(self):
        self.ensure_one()
        if not self.env.user.has_group('qing_partner.group_partner_manager'):
            raise AccessError(self.env._('Only a partner manager can seed roles.'))
        if self.company_id not in self.env.companies:
            raise AccessError(self.env._('Select an allowed company.'))
        self.partner_ids.check_access('read')
        partners = self.partner_ids.commercial_partner_id
        if len(partners) > 1000:
            raise UserError(self.env._('Review at most 1000 partners in one batch.'))
        company_id = self.company_id.id
        Sale = self.env['sale.order']
        Purchase = self.env['purchase.order']
        Move = self.env['account.move']
        domain = [('company_id', '=', company_id), ('partner_id.commercial_partner_id', 'in', partners.ids)]
        customers = self.env['res.partner'].union(*(partner.commercial_partner_id for partner, in Sale._read_group(domain, ['partner_id'])))
        suppliers = self.env['res.partner'].union(*(partner.commercial_partner_id for partner, in Purchase._read_group(domain, ['partner_id'])))
        invoice_domain = domain + [('move_type', 'in', ['out_invoice', 'out_refund', 'in_invoice', 'in_refund'])]
        for partner, move_type in Move._read_group(invoice_domain, ['partner_id', 'move_type']):
            if move_type in ('out_invoice', 'out_refund'):
                customers |= partner.commercial_partner_id
            else:
                suppliers |= partner.commercial_partner_id
        if self.use_rank_hints:
            customers |= partners.filtered(lambda partner: partner.customer_rank > 0)
            suppliers |= partners.filtered(lambda partner: partner.supplier_rank > 0)
        Role = self.env['qing.partner.role']
        existing = Role.search([('company_id', '=', company_id), ('partner_id', 'in', partners.ids)])
        keys = {(record.partner_id.id, record.role) for record in existing}
        values = []
        for role, candidates in [('customer', customers), ('supplier', suppliers)]:
            for partner in candidates:
                if (partner.id, role) not in keys:
                    values.append({'partner_id': partner.id, 'company_id': company_id, 'role': role, 'state': 'pending'})
        Role.create(values)
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('Review Business Roles'),
            'res_model': 'qing.partner.role', 'view_mode': 'list,form',
            'domain': [('company_id', '=', company_id), ('partner_id', 'in', partners.ids)],
        }
