from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .cash_flow_template import ROW_SELECTION


class CashFlowAllocation(models.Model):
    _name = 'qing.cash.flow.allocation'
    _description = 'Cash Flow Classification Exception'
    _check_company_auto = True
    _order = 'cash_line_id, id'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    cash_line_id = fields.Many2one('account.move.line', required=True, check_company=True,
                                   ondelete='cascade', string='Cash Journal Item')
    line_code = fields.Selection(ROW_SELECTION, required=True, string='Cash Flow Item')
    amount = fields.Monetary(required=True, currency_field='currency_id',
                             help='Positive portion of the cash journal item in company currency.')
    currency_id = fields.Many2one(related='company_id.currency_id')
    note = fields.Char()

    @api.constrains('cash_line_id', 'company_id', 'amount', 'line_code')
    def _check_allocation(self):
        for item in self:
            line = item.cash_line_id
            if line.company_id != item.company_id or line.parent_state != 'posted':
                raise ValidationError(self.env._('Classify a posted cash item of the same company.'))
            scheme = self.env['qing.financial.report.scheme']._resolve(item.company_id, line.date)
            if not scheme or line.account_id not in scheme.cash_account_ids:
                raise ValidationError(self.env._('The journal item is outside the configured cash scope.'))
            if item.amount <= 0:
                raise ValidationError(self.env._('Cash flow allocation must be positive.'))
            if item.line_code == 'fx' and not item.note:
                raise ValidationError(self.env._('Explain the foreign exchange effect before classifying it.'))
            line.lock_for_update()
            assigned = sum(self.search([('cash_line_id', '=', line.id)]).mapped('amount'))
            if item.currency_id.compare_amounts(assigned, abs(line.balance)) > 0:
                raise ValidationError(self.env._('Classified amount exceeds the cash journal item.'))
