from odoo import fields, models
from odoo.exceptions import AccessError, UserError


class BankPaymentMatch(models.TransientModel):
    _name = 'qing.bank.payment.match'
    _description = '匹配银行流水与收付款'

    line_id = fields.Many2one('account.bank.statement.line', required=True, readonly=True, string='银行流水')
    payment_id = fields.Many2one('account.payment', required=True, string='收付款')
    payment_amount = fields.Monetary(currency_field='payment_currency_id', related='payment_id.amount', string='付款金额')
    payment_currency_id = fields.Many2one(related='payment_id.currency_id')
    payment_date = fields.Date(related='payment_id.date', string='付款日期')
    payment_partner_id = fields.Many2one(related='payment_id.partner_id', string='往来单位')

    def action_match(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError('仅财务管理员可以匹配银行流水。')
        line, payment = self.line_id, self.payment_id
        line.check_access('write')
        payment.check_access('read')
        if line.journal_id.type != 'bank' or line.move_id.inalterable_hash:
            raise UserError('请选择未锁定的银行流水。')
        if line.company_id != self.env.company or payment.company_id != self.env.company or payment.journal_id != line.journal_id:
            raise UserError('流水和收付款必须属于当前公司、同一个银行日记账。')
        currency = self.env.company.currency_id
        if line.currency_id != currency or payment.currency_id != currency or line.foreign_currency_id:
            raise UserError('当前匹配入口仅支持本位币收付款；外币匹配将单独支持。')
        if payment.move_id.state != 'posted' or line.move_id.state != 'posted':
            raise UserError('流水凭证和收付款凭证必须已经过账。')
        suspense = line.line_ids.filtered(lambda item: item.account_id == line.journal_id.suspense_account_id)
        clearing = payment.move_id.line_ids.filtered(lambda item: item.account_id == payment.outstanding_account_id)
        if line.is_reconciled or len(suspense) != 1 or len(clearing) != 1 or clearing.reconciled:
            raise UserError('流水必须待认领，且收付款必须有一条未核销的银行待清算明细。')
        if clearing.matched_debit_ids or clearing.matched_credit_ids or not clearing.account_id.reconcile:
            raise UserError('当前入口只支持整笔未匹配的收付款。')
        if not currency.is_zero(suspense.balance + clearing.balance):
            raise UserError('银行收支方向或金额与收付款不一致。')
        with self.env.cr.savepoint():
            line.write({'partner_id': payment.partner_id.id})
            suspense = line.line_ids.filtered(lambda item: item.account_id == line.journal_id.suspense_account_id)
            suspense.write({'account_id': clearing.account_id.id, 'partner_id': payment.partner_id.id})
            (suspense | clearing).reconcile()
            line.write({'qing_matched_payment_id': payment.id})
        return {'type': 'ir.actions.act_window_close'}
