from odoo import api, fields, models
from odoo.fields import Domain


class ResPartnerBank(models.Model):
    _inherit = 'res.partner.bank'

    def _qing_bank_domain(self):
        user = self.env.user
        if user._is_internal() and not (
            user.has_group('account.group_account_invoice')
            or self.env['res.partner']._qing_is_manager()
        ):
            # Company bank details are required by ordinary business reports.
            return Domain('partner_id', 'in', user.sudo().company_ids.partner_id.ids)
        return Domain('partner_id', 'any!', self.env['res.partner']._qing_access_domain())

    @api.depends_context('uid', 'company', 'allowed_company_ids')
    def _compute_qing_accessible(self):
        domain = self._qing_bank_domain() & Domain('id', 'in', self.ids)
        allowed = set(self.sudo().search(domain).ids)
        for bank in self:
            bank.qing_accessible = bank.id in allowed

    def _search_qing_accessible(self, operator, value):
        return self.env['res.partner']._qing_boolean_domain(self._qing_bank_domain(), operator, value)

    qing_accessible = fields.Boolean(compute='_compute_qing_accessible', search='_search_qing_accessible', compute_sudo=False)
