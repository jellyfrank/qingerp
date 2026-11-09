from collections import defaultdict

from odoo import api, models
from odoo.exceptions import AccessError


ALIAS_JOURNAL_TYPES = frozenset({'sale', 'purchase', 'bank', 'credit', 'cash'})


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    def _qing_clearing_issues(self, payment_type=None):
        """Read-only diagnosis for the dedicated bank clearing workflow."""
        self.ensure_one()
        self.check_access('read')
        issues = []
        if self.type != 'bank':
            return ['请选择银行日记账。']
        for account, label in [(self.default_account_id, '银行存款'), (self.suspense_account_id, '银行暂记')]:
            if not account:
                issues.append('%s科目未配置。' % label)
            elif not account.active or self.company_id not in account.company_ids:
                issues.append('%s科目已停用或不属于本公司。' % label)
        if self.default_account_id and self.default_account_id == self.suspense_account_id:
            issues.append('银行暂记科目不能与银行存款科目相同。')
        for kind, methods, label in [('inbound', self.inbound_payment_method_line_ids, '收款'),
                                     ('outbound', self.outbound_payment_method_line_ids, '付款')]:
            if payment_type and kind != payment_type:
                continue
            if not methods:
                issues.append('%s方式未配置。' % label)
            for method in methods:
                account = method.payment_account_id
                prefix = '%s方式「%s」' % (label, method.name)
                if not account:
                    issues.append('%s未显式设置待清算科目，社区版可能自动补用科目；请明确配置并核查已有付款的实际科目。' % prefix)
                elif account in (self.default_account_id | self.suspense_account_id):
                    issues.append('%s须使用独立待清算科目，不能使用银行存款或银行暂记科目。' % prefix)
                elif (not account.active or self.company_id not in account.company_ids
                      or account.account_type not in ('asset_current', 'liability_current')
                      or not account.reconcile):
                    issues.append('%s的待清算科目须为本公司启用、允许核销的流动资产或流动负债科目。' % prefix)
                elif account.currency_id and account.currency_id != (self.currency_id or self.company_id.currency_id):
                    issues.append('%s的待清算科目固定币种与日记账不一致。' % prefix)
        return issues

    @api.model_create_multi
    def create(self, vals_list):
        """Allocate native codes before aliases for non-ASCII journal names."""
        if 'import_file' in self.env.context:
            return super().create(vals_list)

        prepared = [dict(values) for values in vals_list]
        reserved_codes = defaultdict(list)
        for values in prepared:
            if values.get('code'):
                company_id = values.get('company_id') or self.env.company.id
                reserved_codes[company_id].append(values['code'])

        Alias = self.env['mail.alias']
        Company = self.env['res.company']
        for values in prepared:
            journal_type = values.get('type')
            if journal_type not in ALIAS_JOURNAL_TYPES or values.get('code') or 'alias_name' in values:
                continue
            name = values.get('name') or values.get('bank_account_number') or values.get('name_placeholder')
            if isinstance(name, dict):
                name = name.get(self.env.lang or 'en_US') or name.get('en_US')
            if not name or (Alias._is_encodable(name) and Alias._sanitize_alias_name(name)):
                continue
            company = Company.browse(values.get('company_id') or self.env.company.id)
            code = self._get_next_journal_default_code(
                journal_type, company, cache=reserved_codes[company.id])
            if code:
                values['code'] = code
                reserved_codes[company.id].append(code)
        return super().create(prepared)

    def action_qing_check_clearing(self):
        self.ensure_one()
        if not self.env.user.has_group('account.group_account_manager') or self.company_id != self.env.company:
            raise AccessError('请由当前公司的财务管理员检查配置。')
        issues = self._qing_clearing_issues()
        message = '\n'.join(issues) if issues else '待收、待付及银行暂记配置检查通过。已有付款是否生成分录，仍须逐笔核实。'
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': '银行清算配置检查', 'message': message,
            'type': 'warning' if issues else 'success', 'sticky': True,
        }}
