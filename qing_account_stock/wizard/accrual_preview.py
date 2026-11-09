from markupsafe import Markup

from odoo import Command, fields, models
from odoo.exceptions import UserError


class AccrualPreview(models.TransientModel):
    _name = 'qing.accrual.preview'
    _description = '暂估分录预览'

    def _rows(self):
        self.ensure_one()
        batch = self.batch_id
        batch._check_manager()
        if batch.state != 'locked':
            raise UserError(self.env._('请先锁定暂估准备批次。'))
        if batch._current_fingerprint() != batch.source_fingerprint:
            raise UserError(self.env._('来源已变化，请取消旧批次并重新生成。'))
        company = batch.company_id
        credit = self.liability_account_id
        credit.check_access('read')
        if not credit or company not in credit.company_ids or not credit.active or credit.account_type != 'liability_current':
            raise UserError(self.env._('请选择本公司启用的流动负债科目作为暂估负债；不要使用应付账款科目。'))
        wizard = self.env['qing.unbilled.purchase'].create({
            'order_ids': [Command.set(batch.source_line_ids.order_id.ids)],
        })
        result = []
        for row in wizard._rows():
            line = row['line']
            if line not in batch.source_line_ids:
                continue
            if line.currency_id != company.currency_id:
                raise UserError(self.env._('本批预览仅支持采购币种与公司本位币一致，外币暂估待汇率口径完成后开放。'))
            valuation = line.product_id.categ_id.property_stock_valuation_account_id or company.account_stock_valuation_id
            debit = valuation.account_stock_variation_id or company.expense_account_id
            (valuation | debit).check_access('read')
            if not valuation or not valuation.active or company not in valuation.company_ids or not debit or company not in debit.company_ids or not debit.active or debit == valuation or debit.account_type not in ('expense', 'expense_direct_cost'):
                raise UserError(self.env._('请检查原生库存估值及库存变动/费用科目配置；预览不会直接借记库存科目。'))
            amount = company.currency_id.round(row['reference'])
            if amount <= 0:
                raise UserError(self.env._('存在零额或负额暂估来源，请先检查采购价格。'))
            result.append({'source': line, 'debit': debit, 'credit': credit, 'amount': amount})
        if not result:
            raise UserError(self.env._('没有可预览的暂估来源。'))
        return result

    batch_id = fields.Many2one('qing.accrual.batch', string='准备批次', required=True, readonly=True)
    company_id = fields.Many2one(related='batch_id.company_id', string='公司')
    entry_date = fields.Date(string='凭证日期', default=fields.Date.context_today)
    journal_id = fields.Many2one('account.journal', string='暂估日记账', domain="[('company_id', '=', company_id), ('type', '=', 'general')]")
    liability_account_id = fields.Many2one('account.account', string='暂估负债科目', required=True,
        domain="[('company_ids', 'in', [company_id]), ('active', '=', True), ('account_type', '=', 'liability_current')]")
    result = fields.Html(string='分录预览', readonly=True)

    def action_preview(self):
        rows = self._rows()
        precision = self.company_id.currency_id.decimal_places
        body = Markup('').join(Markup('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>') % (
            row['source'].order_id.name, row['source'].partner_id.display_name,
            row['debit'].display_name, row['credit'].display_name, f"{row['amount']:.{precision}f}") for row in rows)
        self.result = Markup('<p>预览时间（UTC）：%s；本位币：%s；借贷合计各 %s。</p>'
            '<table class="table table-bordered"><thead><tr><th>采购单</th><th>供应商</th><th>借方</th><th>贷方</th><th>金额</th></tr></thead><tbody>%s</tbody></table>') % (
            fields.Datetime.now(), self.company_id.currency_id.name, f"{sum(row['amount'] for row in rows):.{precision}f}", body)
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'view_mode': 'form', 'target': 'new', 'name': self.env._('暂估分录预览')}

    def write(self, values):
        if {'batch_id', 'liability_account_id'} & values.keys():
            values = dict(values, result=False)
        return super().write(values)

    def action_create_draft(self):
        self.ensure_one()
        batch = self.batch_id
        batch._check_manager()
        self.env.cr.execute('SELECT id FROM qing_accrual_batch WHERE id = %s FOR UPDATE', [batch.id])
        batch.invalidate_recordset(['state', 'draft_move_id'])
        rows = self._rows()
        today = fields.Date.context_today(self)
        captured_date = fields.Datetime.context_timestamp(self, batch.captured_at).date()
        if self.entry_date != today or captured_date != today:
            raise UserError(self.env._('当前仅支持当天快照生成当天草稿；不能把当前余额回填为历史期末余额。'))
        journal = self.journal_id
        journal.check_access('read')
        if not journal or journal.company_id != self.company_id or journal.type != 'general' or not journal.active:
            raise UserError(self.env._('请选择本公司启用的杂项日记账。'))
        if journal.currency_id and journal.currency_id != self.company_id.currency_id:
            raise UserError(self.env._('暂估日记账币种必须与公司本位币一致。'))
        if self.company_id._get_violated_lock_dates(self.entry_date, False, journal):
            raise UserError(self.env._('凭证日期位于原生锁账范围，请检查锁账设置。'))
        lines = []
        snapshot = {'date': str(self.entry_date), 'journal_id': journal.id,
                    'currency_id': self.company_id.currency_id.id, 'rows': []}
        for row in rows:
            source = row['source']
            partner = source.partner_id
            for account, debit, credit in [(row['debit'], row['amount'], 0), (row['credit'], 0, row['amount'])]:
                lines.append(Command.create({'account_id': account.id, 'partner_id': partner.id,
                    'name': '%s / %s' % (batch.name, source.order_id.name), 'debit': debit, 'credit': credit}))
            snapshot['rows'].append({'purchase_line_id': source.id, 'partner_id': partner.id,
                'debit_account_id': row['debit'].id, 'credit_account_id': row['credit'].id, 'amount': row['amount']})
        with self.env.cr.savepoint():
            move = self.env['account.move']._qing_create_accrual(batch, {
                'company_id': self.company_id.id, 'journal_id': journal.id, 'date': self.entry_date,
                'ref': batch.name, 'move_type': 'entry', 'auto_post': 'no', 'line_ids': lines})
            batch._record_draft(move, snapshot)
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move', 'res_id': move.id,
                'view_mode': 'form', 'target': 'current'}
