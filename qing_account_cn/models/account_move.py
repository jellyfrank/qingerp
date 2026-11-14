from decimal import Decimal, ROUND_HALF_UP

from odoo import Command, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.misc import format_amount


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _qing_cn_voucher_digits(self, amount):
        """Eleven places: hundred million through cents; never truncate overflow."""
        value = Decimal(str(amount)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if abs(value) >= Decimal('1000000000'):
            raise UserError(self.env._('金额超出传统凭证分位格范围，请选择标准凭证样式。'))
        digits = str(int(abs(value) * 100)).rjust(11) if value else ' ' * 11
        return {'sign': '-' if value < 0 else '', 'digits': digits}

    def _qing_cn_voucher_pages(self):
        self.ensure_one()
        lines = self.line_ids.filtered(lambda line: line.account_id)
        return [lines[index:index + 6] for index in range(0, len(lines), 6)] or [lines]

    def _qing_cn_opening_summary(self):
        """Inspect selected entries only; residuals describe now, not a past cutoff."""
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(self.env._('仅财务管理员可以执行期初分录检查。'))
        if not self:
            raise UserError(self.env._('请先选择需要核对的期初分录。'))
        self.check_access('read')
        companies = self.company_id
        if len(companies) != 1 or companies != self.env.company:
            raise UserError(self.env._('请切换到目标公司，并且只选择该公司的期初分录。'))
        lines = self.line_ids
        lines.check_access('read')
        currency = companies.currency_id
        issues = []
        if any(move.move_type != 'entry' for move in self):
            issues.append(self.env._('选中记录包含发票/账单，请仅选择用于初始化的普通日记账分录。'))
        if len(set(self.mapped('date'))) != 1:
            issues.append(self.env._('选中凭证日期不一致，请核对期初截止日期。'))
        if any(move.state == 'cancel' for move in self):
            issues.append(self.env._('选中记录包含已取消凭证。'))
        if any(move.state == 'draft' for move in self):
            issues.append(self.env._('包含草稿：以下合计包含草稿金额，不能与仅已过账报表直接比较。'))
        if any(not move.line_ids for move in self):
            issues.append(self.env._('包含没有明细的凭证。'))
        if any(not currency.is_zero(sum(move.line_ids.mapped('balance'))) for move in self):
            issues.append(self.env._('存在单张凭证借贷不平衡，请逐张核对。'))
        if any(line.tax_line_id or line.tax_ids for line in lines):
            issues.append(self.env._('包含税项，请确认是否重复计入历史税额。'))
        trade_lines = lines.filtered(lambda line: line.account_id.account_type in (
            'asset_receivable', 'liability_payable',
        ))
        if any(not line.partner_id for line in trade_lines):
            issues.append(self.env._('应收/应付明细缺少往来单位。'))
        if any(not line.date_maturity for line in trade_lines):
            issues.append(self.env._('应收/应付明细缺少到期日，请核对账龄依据。'))
        if any(line.matched_debit_ids or line.matched_credit_ids for line in trade_lines):
            issues.append(self.env._('往来明细已经发生核销；当前未结额不是历史期初余额。'))
        return {
            'currency': currency, 'entry_count': len(self), 'line_count': len(lines),
            'debit': sum(lines.mapped('debit')), 'credit': sum(lines.mapped('credit')),
            'trade': {
                kind: {
                    'balance': sum(trade_lines.filtered(lambda line: line.account_id.account_type == kind).mapped('balance')),
                    'residual': sum(trade_lines.filtered(lambda line: line.account_id.account_type == kind).mapped('amount_residual')),
                } for kind in ('asset_receivable', 'liability_payable')
            },
            'issues': issues,
        }

    def action_qing_cn_check_opening_entries(self):
        summary = self._qing_cn_opening_summary()
        currency = summary['currency']
        def money(value):
            return format_amount(self.env, value, currency)

        message = self.env._(
            '本次选择：%(entries)s张凭证，%(lines)s条明细；本位币%(currency)s。',
            entries=summary['entry_count'], lines=summary['line_count'], currency=currency.name,
        )
        message += '\n' + self.env._('借方：%s；贷方：%s；差额：%s。',
                                    money(summary['debit']), money(summary['credit']),
                                    money(summary['debit'] - summary['credit']))
        for kind, label in (('asset_receivable', self.env._('应收')), ('liability_payable', self.env._('应付'))):
            values = summary['trade'][kind]
            message += '\n' + self.env._('%s原分录余额：%s；当前未结：%s。',
                                        label, money(values['balance']), money(values['residual']))
        message += '\n' + self.env._('余额采用借正贷负。本检查仅覆盖选中记录；未与来源试算表核对，不代表完整期初验收。')
        if summary['issues']:
            message += '\n' + '\n'.join('• ' + issue for issue in summary['issues'])
        return self._qing_notify(message, title=self.env._('选中期初分录检查'),
                                 notification_type='warning' if summary['issues'] else 'info', sticky=True)

    def action_qing_cn_compare_opening_balances(self):
        self._qing_cn_opening_summary()
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('期初来源余额对照'),
            'res_model': 'qing.cn.opening.balance.compare', 'view_mode': 'form', 'target': 'new',
            'context': {
                'default_move_ids': [Command.set(self.ids)],
                'default_source_date': self[0].date,
                'default_company_id': self.env.company.id,
            },
        }
