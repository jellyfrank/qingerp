from odoo import api, Command, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .common import finite_nonnegative, internal, is_internal


DEPOSIT_KINDS = [('receipt', 'Receipt'), ('refund', 'Refund')]
DEPOSIT_STATES = [('draft', 'Draft'), ('posted', 'Posted')]


class RentalDeposit(models.Model):
    _name = 'qing.rental.deposit'
    _description = 'Rental deposit'
    _order = 'date desc, id desc'
    _check_company_auto = True

    _reference_unique = models.UniqueIndex(
        "(company_id, journal_id, reference, kind) WHERE state = 'posted'",
        'This bank or cash reference has already been recorded.',
    )

    @api.constrains('amount', 'order_id', 'kind', 'reference')
    def _check_amount(self):
        for deposit in self:
            finite_nonnegative(self.env, deposit.amount)
            if deposit.amount <= 0 or not deposit.order_id.is_qing_rental:
                raise ValidationError(self.env._('A deposit requires a rental order and a positive amount.'))
            if not deposit.reference or not deposit.reference.strip() or deposit.reference != deposit.reference.strip():
                raise ValidationError(self.env._('Enter a receipt reference without leading or trailing spaces.'))
            if deposit.currency_id.compare_amounts(deposit.amount, deposit.currency_id.round(deposit.amount)) or abs(deposit.amount - deposit.currency_id.round(deposit.amount)) > 1e-8:
                raise ValidationError(self.env._('The deposit amount must match the currency rounding.'))

    amount = fields.Monetary(required=True)
    company_id = fields.Many2one(related='order_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='order_id.currency_id')
    date = fields.Date(required=True, default=fields.Date.context_today)
    journal_id = fields.Many2one('account.journal', required=True, check_company=True, domain=[('type', 'in', ['bank', 'cash'])])
    kind = fields.Selection(DEPOSIT_KINDS, required=True, default='receipt')
    liability_account_id = fields.Many2one('account.account', required=True, check_company=True, domain=[('account_type', '=', 'liability_current'), ('reconcile', '=', True)])
    move_id = fields.Many2one('account.move', readonly=True, copy=False, ondelete='restrict', check_company=True)
    note = fields.Text()
    order_id = fields.Many2one('sale.order', required=True, ondelete='restrict', check_company=True)
    reference = fields.Char(required=True, help='Bank or cash receipt reference for audit and duplicate checks.')
    state = fields.Selection(DEPOSIT_STATES, default='draft', required=True, readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        if not is_internal(self.env):
            for vals in vals_list:
                if vals.get('state', self.env.context.get('default_state', 'draft')) != 'draft' or vals.get('move_id') or self.env.context.get('default_move_id'):
                    raise AccessError(self.env._('Post deposits using the deposit workflow.'))
        return super().create(vals_list)

    def write(self, vals):
        if not is_internal(self.env):
            if {'state', 'move_id', 'order_id'}.intersection(vals) or any(item.state == 'posted' for item in self):
                raise UserError(self.env._('Posted deposits and their accounting links cannot be changed.'))
        return super().write(vals)

    def unlink(self):
        if any(item.state == 'posted' for item in self):
            raise UserError(self.env._('Posted deposits cannot be deleted. Record a refund instead.'))
        return super().unlink()

    def action_post(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('qing_rental.group_rental_manager') or not self.env.user.has_group('account.group_account_invoice'):
            raise AccessError(self.env._('Rental manager and accounting permissions are required to post deposits.'))
        self.order_id._qing_rental_lock()
        self.invalidate_recordset()
        if self.state == 'posted':
            return True
        if self.order_id.state != 'sale':
            raise UserError(self.env._('Confirm the rental before recording its deposit.'))
        if self.currency_id != self.company_id.currency_id:
            raise UserError(self.env._('First-version deposits must use the company currency.'))
        journal, account = self.journal_id, self.liability_account_id
        if journal.type not in ('bank', 'cash') or not journal.default_account_id or (journal.currency_id and journal.currency_id != self.currency_id):
            raise UserError(self.env._('Select a bank or cash journal with a liquidity account in the deposit currency.'))
        if account.account_type != 'liability_current' or not account.reconcile:
            raise UserError(self.env._('Use a reconcilable current liability account for refundable deposits.'))
        self._check_company()
        self._check_amount()
        previous = self.order_id.qing_rental_deposit_ids.filtered(lambda item: item.state == 'posted')
        if previous and previous.liability_account_id != account:
            raise UserError(self.env._('Use the same liability account for this rental deposit and its refunds.'))
        domain = [('company_id', '=', self.company_id.id), ('journal_id', '=', journal.id),
                  ('reference', '=', self.reference), ('kind', '=', self.kind), ('state', '=', 'posted'), ('id', '!=', self.id)]
        if self.sudo().search_count(domain, limit=1):
            raise UserError(self.env._('This bank or cash reference has already been recorded.'))
        if self.kind == 'refund' and self.currency_id.compare_amounts(self.amount, self.order_id.qing_rental_deposit_balance) > 0:
            raise UserError(self.env._('The refund exceeds the outstanding deposit.'))
        partner = self.order_id.partner_id.commercial_partner_id
        amount = self.amount if self.kind == 'receipt' else -self.amount
        label = self.env._('Rental deposit: %(order)s / %(reference)s', order=self.order_id.name, reference=self.reference)
        lines = [Command.create({'name': label, 'account_id': journal.default_account_id.id,
                                 'partner_id': partner.id, 'debit': max(amount, 0), 'credit': max(-amount, 0)}),
                 Command.create({'name': label, 'account_id': account.id,
                                 'partner_id': partner.id, 'credit': max(amount, 0), 'debit': max(-amount, 0)})]
        move = internal(self.env['account.move']).create({'move_type': 'entry', 'journal_id': journal.id,
            'company_id': self.company_id.id, 'date': self.date, 'ref': label,
            'qing_rental_deposit_id': self.id, 'line_ids': lines})
        move.action_post()
        internal(self).write({'state': 'posted', 'move_id': move.id})
        return True
