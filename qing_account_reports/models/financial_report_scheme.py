import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .balance_sheet_template import TEMPLATE_VERSION as BALANCE_TEMPLATE_VERSION
from .profit_statement_template import TEMPLATE_VERSION as PROFIT_TEMPLATE_VERSION


_logger = logging.getLogger(__name__)


class FinancialReportScheme(models.Model):
    _name = 'qing.financial.report.scheme'
    _description = 'Company Financial Report Scheme'
    _check_company_auto = True
    _order = 'company_id, date_from desc, id desc'

    def _check_mapping_pair(self):
        for scheme in self:
            balance = scheme.balance_mapping_id
            profit = scheme.profit_mapping_id
            if not balance or not profit:
                raise ValidationError(self.env._('Select both statement mappings.'))
            if balance.company_id != scheme.company_id or profit.company_id != scheme.company_id:
                raise ValidationError(self.env._('Both mappings must belong to this company.'))
            if balance.state != 'confirmed' or profit.state != 'confirmed':
                raise ValidationError(self.env._('Both statement mappings must be confirmed.'))
            if balance.template_version != BALANCE_TEMPLATE_VERSION or profit.template_version != PROFIT_TEMPLATE_VERSION:
                raise ValidationError(self.env._('The report scheme uses an unsupported template version.'))
            balance_rows = {line.account_id.id: line.line_code for line in balance.line_ids}
            required_accounts = profit.line_ids.account_id | profit.closing_account_ids
            if any(balance_rows.get(account.id) != '51' for account in required_accounts):
                raise ValidationError(self.env._(
                    'Every profit and closing account must be mapped to balance sheet row 51.'
                ))
            if any(scheme.company_id not in account.company_ids or balance_rows.get(account.id) != '1'
                   for account in scheme.cash_account_ids):
                raise ValidationError(self.env._(
                    'Cash and cash-equivalent accounts must be mapped to balance sheet cash row 1.'
                ))

    def _check_active_range(self):
        for scheme in self.filtered(lambda item: item.state == 'active'):
            if scheme.date_to and scheme.date_to < scheme.date_from:
                raise ValidationError(self.env._('The end date cannot precede the start date.'))
            scheme.company_id.lock_for_update()
            domain = [
                ('id', '!=', scheme.id), ('company_id', '=', scheme.company_id.id),
                ('state', '=', 'active'), ('date_from', '<=', scheme.date_to or '9999-12-31'),
                '|', ('date_to', '=', False), ('date_to', '>=', scheme.date_from),
            ]
            existing = self.search(domain, limit=1)
            if existing:
                raise ValidationError(self.env._('Active report schemes for a company cannot overlap.'))

    @api.model
    def _resolve(self, company, report_date):
        if company != self.env.company:
            raise UserError(self.env._('Switch to the company selected on this report.'))
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(self.env._('Only accounting administrators can view financial statements.'))
        self.check_access('read')
        domain = [
            ('company_id', '=', company.id), ('state', '=', 'active'),
            ('date_from', '<=', report_date), '|', ('date_to', '=', False), ('date_to', '>=', report_date),
        ]
        schemes = self.search(domain, limit=2)
        if len(schemes) > 1:
            raise UserError(self.env._('Multiple report schemes apply to this date. Ask an administrator to fix the configuration.'))
        return schemes

    @api.model
    def _bootstrap_company(self, company):
        """Adopt a unique existing pair, or create a native CN pair once."""
        company.lock_for_update()
        Scheme = self.with_company(company)
        if Scheme.search([('company_id', '=', company.id)], limit=1):
            return
        Balance = self.env['qing.balance.sheet.mapping'].with_company(company)
        Profit = self.env['qing.profit.statement.mapping'].with_company(company)
        balance = Balance.search([('company_id', '=', company.id), ('state', '=', 'confirmed')], limit=2)
        profit = Profit.search([('company_id', '=', company.id), ('state', '=', 'confirmed')], limit=2)
        if len(balance) == len(profit) == 1:
            try:
                with self.env.cr.savepoint():
                    scheme = Scheme.create({
                        'balance_mapping_id': balance.id, 'company_id': company.id,
                        'date_from': '1900-01-01', 'name': self.env._('Company reporting scheme'),
                        'profit_mapping_id': profit.id, 'source': 'existing',
                        'cash_account_ids': [(6, 0, balance.line_ids.filtered(
                            lambda line: line.line_code == '1' and
                            line.account_id.account_type == 'asset_cash'
                        ).account_id.ids)],
                    })
                    scheme.action_activate()
            except (UserError, ValidationError) as error:
                _logger.warning('Cannot adopt existing report mappings for company %s: %s', company.id, error)
            return
        if balance or profit or company.chart_template != 'cn':
            return
        try:
            with self.env.cr.savepoint():
                balance = Balance.create({'company_id': company.id, 'name': self.env._('Built-in CN small-business balance sheet')})
                balance.action_suggest_cn()
                balance.action_confirm()
                profit = Profit.create({'company_id': company.id, 'name': self.env._('Built-in CN small-business profit statement')})
                profit.action_suggest_cn()
                profit.action_confirm()
                scheme = Scheme.create({
                    'balance_mapping_id': balance.id, 'company_id': company.id,
                    'date_from': '1900-01-01', 'name': self.env._('Built-in CN small-business statements'),
                    'profit_mapping_id': profit.id, 'source': 'system',
                    'cash_account_ids': [(6, 0, balance.line_ids.filtered(
                        lambda line: line.line_code == '1' and
                        line.account_id.account_type == 'asset_cash'
                    ).account_id.ids)],
                })
                scheme.action_activate()
        except (UserError, ValidationError) as error:
            _logger.warning('Cannot bootstrap native CN report mappings for company %s: %s', company.id, error)

    @api.model
    def _bootstrap_existing_companies(self):
        companies = self.env['res.company'].search([])
        for company in companies:
            self.with_company(company)._bootstrap_company(company)

    @api.constrains('balance_mapping_id', 'cash_account_ids', 'company_id', 'profit_mapping_id', 'state')
    def _check_pair(self):
        self.filtered(lambda item: item.state == 'active')._check_mapping_pair()

    @api.constrains('company_id', 'date_from', 'date_to', 'state')
    def _check_range(self):
        self._check_active_range()

    balance_mapping_id = fields.Many2one(
        'qing.balance.sheet.mapping', check_company=True, ondelete='restrict', string='Balance Sheet Rules',
    )
    cash_account_ids = fields.Many2many(
        'account.account', 'qing_report_scheme_cash_account_rel', 'scheme_id', 'account_id',
        string='Cash and Cash Equivalents',
        help='Review the actual cash scope. Suspense and transit accounts must not be included automatically.',
    )
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    date_from = fields.Date(required=True, default='1900-01-01', string='Effective From')
    date_to = fields.Date(string='Effective Until')
    name = fields.Char(required=True)
    profit_mapping_id = fields.Many2one(
        'qing.profit.statement.mapping', check_company=True, ondelete='restrict', string='Profit Statement Rules',
    )
    source = fields.Selection(
        [('system', 'Built-in'), ('existing', 'Existing Mappings'), ('manual', 'Manual')],
        default='manual', required=True, readonly=True,
    )
    state = fields.Selection([('draft', 'Draft'), ('active', 'Active')], default='draft', required=True)

    @api.model_create_multi
    def create(self, values_list):
        if any(values.get('state', 'draft') != 'draft' for values in values_list):
            raise UserError(self.env._('Create a draft scheme and activate it after review.'))
        return super().create(values_list)

    def write(self, values):
        if any(item.state == 'active' for item in self):
            if set(values) - {'date_to'}:
                raise UserError(self.env._('Active report schemes are immutable except for their end date.'))
        if 'state' in values:
            raise UserError(self.env._('Use Activate to enable a report scheme.'))
        return super().write(values)

    def unlink(self):
        if any(item.state == 'active' for item in self):
            raise UserError(self.env._('Active report schemes cannot be deleted.'))
        return super().unlink()

    def action_activate(self):
        self.check_access('write')
        self.lock_for_update()
        self.invalidate_recordset(['state'])
        if any(item.state != 'draft' for item in self):
            raise UserError(self.env._('Only draft report schemes can be activated.'))
        self._check_mapping_pair()
        return super().write({'state': 'active'})
