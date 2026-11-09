import hashlib
import json

from odoo import api, fields, models
from odoo.fields import Domain
from odoo.exceptions import AccessError, UserError


class AccountBankStatementLine(models.Model):
    _inherit = 'account.bank.statement.line'

    @api.depends('is_reconciled', 'amount', 'currency_id', 'qing_matched_payment_id',
                 'line_ids.matched_debit_ids', 'line_ids.matched_credit_ids')
    def _compute_qing_edit_locked(self):
        for line in self:
            line.qing_edit_locked = bool((line.is_reconciled and not line.currency_id.is_zero(line.amount))
                                         or line.qing_matched_payment_id
                                         or line.line_ids.matched_debit_ids or line.line_ids.matched_credit_ids)

    @api.constrains('amount', 'currency_id', 'journal_id')
    def _check_qing_nonzero_amount(self):
        for line in self:
            if line.journal_id.type == 'bank' and line.currency_id.is_zero(line.amount):
                raise UserError('银行流水金额不能为 0，请填写实际金额：收入为正数，支出为负数。')

    def _qing_check_editable(self):
        if any(line.qing_edit_locked for line in self):
            raise UserError('已匹配的银行流水必须先 Reset（撤销匹配），才能编辑或删除。')

    def _qing_workbench_access(self):
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError('仅财务管理员可以使用银行对账工作台。')
        self.check_access('read')
        if any(line.company_id != self.env.company or line.journal_id.type != 'bank' for line in self):
            raise AccessError('请选择当前公司的银行流水。')

    def _qing_workbench_line(self):
        self.ensure_one()
        return {
            'id': self.id, 'date': str(self.date), 'amount': self.amount,
            'currency': self.currency_id.name, 'memo': self.payment_ref or '',
            'partner': self.partner_id.display_name or self.partner_name or '',
            'account': self.account_number or '', 'journal': self.journal_id.display_name,
            'reference': self.qing_bank_reference or '', 'move_id': self.move_id.id,
            'write_date': str(self.write_date),
        }

    def _qing_workbench_payment(self, payment):
        clearing = payment.move_id.line_ids.filtered(lambda item: item.account_id == payment.outstanding_account_id)
        if (payment.company_id != self.company_id or payment.journal_id != self.journal_id
                or payment.currency_id != self.company_id.currency_id or payment.move_id.state != 'posted'
                or len(clearing) != 1 or not clearing.account_id.reconcile
                or clearing.reconciled or clearing.matched_debit_ids or clearing.matched_credit_ids
                or not self.currency_id.is_zero(clearing.balance - self.amount)):
            return None
        account = payment.partner_bank_id.account_number or ''
        reasons = ['金额及收支方向一致']
        score = 0
        if self.partner_id and self.partner_id == payment.partner_id:
            reasons.append('往来单位一致')
            score += 60
        elif self.partner_name and self.partner_name.strip().casefold() == (payment.partner_id.name or '').strip().casefold():
            reasons.append('户名一致')
            score += 30
        if self.account_number and self.account_number.replace(' ', '') == account.replace(' ', ''):
            reasons.append('对方账号一致')
            score += 100
        days = abs((self.date - payment.date).days)
        reasons.append('同日' if not days else '日期相差 %s 天' % days)
        return {
            'id': payment.id, 'name': payment.display_name, 'date': str(payment.date),
            'partner': payment.partner_id.display_name, 'amount': payment.amount,
            'signed_amount': clearing.balance, 'currency': payment.currency_id.name,
            'account': account, 'reasons': reasons, 'score': score, 'days': days,
            'write_date': str(payment.write_date), 'clearing_id': clearing.id,
        }

    def _qing_workbench_token(self, payment_data):
        values = [self._qing_workbench_line(), payment_data]
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    @api.model
    def qing_workbench_queue(self, journal_id=False, date_from=False, date_to=False, offset=0):
        self._qing_workbench_access()
        offset = max(0, min(int(offset), 100000))
        Journal = self.env['account.journal']
        journal_domain = [('company_id', '=', self.env.company.id), ('type', '=', 'bank')]
        journals = Journal.search(journal_domain)
        if journal_id and journal_id not in journals.ids:
            raise AccessError('银行日记账不属于当前公司。')
        domain = Domain([('company_id', '=', self.env.company.id), ('journal_id.type', '=', 'bank'),
                         ('is_reconciled', '=', False)])
        if journal_id:
            domain &= Domain('journal_id', '=', journal_id)
        if date_from:
            domain &= Domain('date', '>=', fields.Date.to_date(date_from))
        if date_to:
            domain &= Domain('date', '<=', fields.Date.to_date(date_to))
        if date_from and date_to and date_from > date_to:
            raise UserError('开始日期不能晚于结束日期。')
        lines = self.search(domain, order='date, id', offset=offset, limit=31)
        return {'lines': [line._qing_workbench_line() for line in lines[:30]], 'more': len(lines) > 30,
                'journals': [{'id': j.id, 'name': j.display_name} for j in journals],
                'company': self.env.company.display_name}

    def qing_workbench_candidates(self, query=''):
        self.ensure_one()
        self._qing_workbench_access()
        reason = False
        if self.is_reconciled:
            reason = '此流水已处理，请刷新待处理列表。'
        elif self.currency_id != self.company_id.currency_id or self.foreign_currency_id:
            reason = '本批工作台支持本位币整笔收付款匹配，外币流水请暂时跳过。'
        elif self.move_id.state != 'posted' or self.move_id.inalterable_hash:
            reason = '流水凭证尚未过账或已锁定，当前不能匹配。'
        if reason:
            return {'line': self._qing_workbench_line(), 'candidates': [], 'notice': reason}
        domain = Domain([('company_id', '=', self.company_id.id), ('journal_id', '=', self.journal_id.id),
                         ('currency_id', '=', self.company_id.currency_id.id), ('move_id.state', '=', 'posted'),
                         ('payment_type', '=', 'inbound' if self.amount > 0 else 'outbound'),
                         ('amount', '=', abs(self.amount))])
        if query:
            query = str(query).strip()[:100]
            domain &= Domain.OR([Domain('name', 'ilike', query), Domain('partner_id.name', 'ilike', query)])
        Payment = self.env['account.payment']
        payments = Payment.search(domain, order='date desc, id desc', limit=1001)
        if len(payments) > 1000:
            return {'line': self._qing_workbench_line(), 'candidates': [], 'notice': '同额收付款超过 1000 条，请输入往来单位或单号缩小搜索范围。'}
        candidates = []
        for payment in payments:
            item = self._qing_workbench_payment(payment)
            if item:
                item['token'] = self._qing_workbench_token(item)
                candidates.append(item)
        candidates.sort(key=lambda item: (-item['score'], item['days'], item['id']))
        notice = '候选超过 50 条，仅显示优先项；可输入往来单位或单号缩小范围。' if len(candidates) > 50 else ''
        if not candidates:
            issues = self.journal_id._qing_clearing_issues('inbound' if self.amount > 0 else 'outbound')
            if issues:
                notice = '请先到“银行清算配置”检查：' + '；'.join(issues)
            elif not query:
                notice = '没有可匹配的已过账待清算付款。请检查是否已登记同银行、同币种、同金额的收付款，以及付款是否生成会计分录；仅显示“完成”不代表存在可核销分录。'
        return {'line': self._qing_workbench_line(), 'candidates': candidates[:50], 'notice': notice}

    def qing_workbench_match(self, payment_id, token):
        self.ensure_one()
        self._qing_workbench_access()
        self.check_access('write')
        payment = self.env['account.payment'].browse(payment_id).exists()
        payment.check_access('read')
        if not payment:
            raise UserError('收付款已不存在，请刷新。')
        candidate = self._qing_workbench_payment(payment)
        if not candidate or token != self._qing_workbench_token(candidate):
            raise UserError('流水或收付款已变化，请刷新候选后重新核实。')
        wizard = self.env['qing.bank.payment.match'].create({'line_id': self.id, 'payment_id': payment.id})
        wizard.action_match()
        return True

    _qing_bank_reference_unique = models.Constraint(
        'UNIQUE(journal_id, qing_bank_reference)', '同一银行日记账的银行流水号不能重复。')

    qing_bank_reference = fields.Char(string='银行流水号', copy=False, index=True)
    qing_edit_locked = fields.Boolean(compute='_compute_qing_edit_locked', string='匹配后锁定')
    qing_import_filename = fields.Char(string='导入文件', copy=False, readonly=True)

    qing_matched_payment_id = fields.Many2one('account.payment', string='匹配的收付款', copy=False, readonly=True)

    def write(self, vals):
        # Only guard transaction inputs: native reconciliation/reset must still update journal items.
        inputs = {'date', 'journal_id', 'payment_ref', 'partner_name', 'account_number',
                  'partner_id', 'amount', 'currency_id', 'foreign_currency_id', 'amount_currency',
                  'qing_bank_reference', 'statement_id', 'company_id', 'move_id'}
        if inputs.intersection(vals):
            self._qing_check_editable()
        return super().write(vals)

    def unlink(self):
        self._qing_check_editable()
        return super().unlink()

    def action_undo_reconciliation(self):
        result = super().action_undo_reconciliation()
        self.qing_matched_payment_id = False
        return result

    def action_qing_match_payment(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('account.group_account_manager') or self.company_id != self.env.company:
            raise AccessError('请由当前公司的财务管理员操作。')
        return {'type': 'ir.actions.act_window', 'res_model': 'qing.bank.payment.match',
                'view_mode': 'form', 'target': 'new', 'context': {'default_line_id': self.id}}

    def action_qing_undo_match(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('account.group_account_manager') or self.company_id != self.env.company:
            raise AccessError('请由当前公司的财务管理员操作。')
        if self.move_id.inalterable_hash:
            raise UserError('已锁定的银行凭证不能撤销匹配。')
        if not self.qing_edit_locked:
            raise UserError('此流水尚未匹配，无需 Reset。')
        if self.payment_ids:
            raise UserError('流水已有其他自动生成付款，请使用对应原生流程处理。')
        self.action_undo_reconciliation()
        self.qing_matched_payment_id = False
