from odoo import fields, models
from odoo.exceptions import AccessError


TAXPAYER_TYPES = [
    ('unconfirmed', 'Unconfirmed'),
    ('general', 'General VAT Taxpayer'),
    ('small', 'Small-scale VAT Taxpayer'),
]
VOUCHER_STYLES = [('standard', 'Standard Voucher'),
                  ('traditional', 'Traditional Voucher (Amount Columns)')]
CHINA_CHARTS = ('cn', 'cn_large_bis')


class ResCompany(models.Model):
    _inherit = 'res.company'

    def _qing_cn_check_configuration_access(self):
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(self.env._('仅财务管理员可以维护或检查核算配置。'))
        self.check_access('read')
        if self - self.env.companies:
            raise AccessError(self.env._('请先切换到已授权的目标公司。'))

    def _qing_cn_configuration_issues(self):
        """Inspect saved setup; never install a chart or change accounting data."""
        self.ensure_one()
        company = self.with_company(self)
        issues = []
        if company.country_id.code != 'CN':
            issues.append(self.env._('公司国家未设置为中国，请确认适用范围。'))
        if company.account_fiscal_country_id.code != 'CN':
            issues.append(self.env._('税务所属国家未设置为中国。'))
        if company.chart_template not in CHINA_CHARTS:
            issues.append(self.env._('尚未采用原生中国科目模板，请在财务本地化设置中核实。'))
        if company.qing_cn_taxpayer_type == 'unconfirmed':
            issues.append(self.env._('纳税人身份尚未确认。'))
        if not company.vat:
            issues.append(self.env._('尚未填写公司税号。'))
        if company.currency_id.name != 'CNY':
            issues.append(self.env._('本位币不是人民币，请由会计确认，不要直接修改已有账套币种。'))
        for account, expected, label in (
            (company.receivable_account_id, 'asset_receivable', self.env._('应收账款')),
            (company.payable_account_id, 'liability_payable', self.env._('应付账款')),
        ):
            if not account or not account.active or account.account_type != expected or not account.reconcile:
                issues.append(self.env._('%s默认科目缺失、停用、类型不符或未启用核销。', label))
        Journal = company.env['account.journal']
        domain = [('company_id', '=', company.id), ('type', 'in', ('sale', 'purchase', 'bank'))]
        journal_types = set(Journal.search(domain).mapped('type'))
        for kind, label in (('sale', self.env._('销售')), ('purchase', self.env._('采购')), ('bank', self.env._('银行'))):
            if kind not in journal_types:
                issues.append(self.env._('缺少可用的%s日记账。', label))
        for tax, label in ((company.account_sale_tax_id, self.env._('销售')),
                           (company.account_purchase_tax_id, self.env._('采购'))):
            if not tax or not tax.active or tax.company_id != company:
                issues.append(self.env._('%s默认税项缺失、停用或不属于当前公司。', label))
        return issues

    qing_cn_taxpayer_type = fields.Selection(
        selection=TAXPAYER_TYPES, string='Taxpayer Type', default='unconfirmed', required=True,
        groups='account.group_account_manager',
        help='由财务确认，独立于会计科目模板；该字段不会自动改变税率、科目或历史单据。',
    )

    qing_cn_voucher_style = fields.Selection(
        VOUCHER_STYLES,
        string='Voucher Print Style', default='standard', required=True,
    )

    def action_qing_cn_check_configuration(self):
        self.ensure_one()
        self._qing_cn_check_configuration_access()
        issues = self._qing_cn_configuration_issues()
        message = '\n'.join('• ' + issue for issue in issues)
        if not issues:
            message = self.env._('基础配置项已齐备；税率、准则适用、期初余额及财税口径仍需会计复核。')
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': self.env._('%s：核算配置检查', self.display_name),
                       'message': message, 'type': 'warning' if issues else 'info', 'sticky': True},
        }

    def write(self, vals):
        if {'qing_cn_taxpayer_type', 'qing_cn_voucher_style'} & vals.keys():
            self._qing_cn_check_configuration_access()
        return super().write(vals)
