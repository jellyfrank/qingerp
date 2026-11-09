import csv
import io
from collections import defaultdict
from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.binary import BinaryBytes

from ..models.balance_sheet_template import ROW_NAMES, SPLIT_ROWS, TEMPLATE_VERSION, TOTALS


class BalanceSheet(models.TransientModel):
    _name = 'qing.balance.sheet'
    _description = 'Chinese Small Business Balance Sheet'
    _check_company_auto = True

    @api.depends('date_to')
    def _compute_display_name(self):
        for record in self:
            record.display_name = f"{self.env._('Chinese Small Business Balance Sheet')} · {record.date_to or ''}"

    def _base_domain(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(self.env._('Only accounting administrators can generate this report.'))
        if self.company_id != self.env.company:
            raise UserError(self.env._('Switch to the company selected on this report.'))
        if not self.date_to:
            raise UserError(self.env._('Select a report date.'))
        return [('company_id', '=', self.company_id.id), ('parent_state', '=', 'posted'),
                ('account_id.account_type', '!=', 'off_balance'), ('date', '<=', self.date_to)]

    def _report_data(self):
        """Use historical posted balances, never today's residuals or account types as mappings."""
        domain = self._base_domain()
        mapping = self.mapping_id
        mapping.check_access('read')
        if not mapping or mapping.company_id != self.company_id or mapping.state != 'confirmed':
            raise UserError(self.env._('Select a confirmed mapping for this company.'))
        if mapping.template_version != TEMPLATE_VERSION:
            raise UserError(self.env._('This mapping uses an unsupported template version.'))
        opening_date = self.date_to.replace(month=1, day=1) - timedelta(days=1)
        Line = self.env['account.move.line'].with_company(self.company_id)
        pairs = defaultdict(lambda: [0.0, 0.0])
        for index, scope in enumerate((domain + [('date', '<=', opening_date)], domain)):
            for account, partner, balance in Line._read_group(scope, ['account_id', 'partner_id'], ['balance:sum']):
                pairs[account, partner][index] = balance
        account_mapping = {line.account_id.id: int(line.line_code) for line in mapping.line_ids}
        amounts = {code: [0.0, 0.0] for code in ROW_NAMES}
        details = defaultdict(lambda: [0.0, 0.0])
        issues = set()
        ledger = [0.0, 0.0]
        currency = self.currency_id
        for (account, partner), balances in pairs.items():
            code = account_mapping.get(account.id)
            for index, balance in enumerate(balances):
                ledger[index] += balance
                if currency.is_zero(balance):
                    continue
                if not code:
                    issues.add(self.env._('Unmapped account: %s', account.display_name))
                    details[account.id, partner.id, 0][index] += balance
                    continue
                row = code
                if code in SPLIT_ROWS:
                    if not partner:
                        issues.add(self.env._('Partner missing on a non-zero trade balance: %s', account.display_name))
                    row = SPLIT_ROWS[code][balance < 0]
                signed = -balance if row >= 31 or row == 19 else balance
                amounts[row][index] += signed
                details[account.id, partner.id if code in SPLIT_ROWS else False, row][index] += signed
        for code, parts in TOTALS.items():
            for index in (0, 1):
                amounts[code][index] += sum(amounts[abs(part)][index] * (1 if part > 0 else -1) for part in parts)
        amounts = {code: [currency.round(value) for value in values] for code, values in amounts.items()}
        differences = [currency.round(amounts[30][i] - amounts[53][i]) for i in (0, 1)]
        if any(not currency.is_zero(value) for value in ledger):
            issues.add(self.env._('The accessible ledger is not balanced. Check permissions and source entries.'))
        if any(not currency.is_zero(value) for value in differences):
            issues.add(self.env._('Assets do not equal liabilities plus equity.'))
        if any(currency.compare_amounts(amounts[9][i], sum(amounts[row][i] for row in (10, 11, 12, 13))) < 0 for i in (0, 1)):
            issues.add(self.env._('Inventory is smaller than its disclosed detail rows. Review inventory mappings.'))
        return {'amounts': amounts, 'details': details, 'issues': sorted(issues),
                'differences': differences, 'opening_date': opening_date}

    def _amount(self, value):
        return f'{self.currency_id.round(value):.{self.currency_id.decimal_places}f}'

    def _render_table(self, headers, rows):
        head = Markup('').join(Markup('<th>%s</th>') % label for label in headers)
        body = Markup('').join(Markup('<tr>%s</tr>') % Markup('').join(
            Markup('<td>%s</td>') % value for value in row) for row in rows)
        return Markup('<div class="table-responsive"><table class="table table-bordered"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>') % (head, body)

    def _result_values(self, data):
        generated = fields.Datetime.now()
        headers = ['资产', '行次', '期末余额', '年初余额', '负债和所有者权益', '行次', '期末余额', '年初余额']
        rows = []
        for asset in range(1, 31):
            liability = asset + 30
            row = [ROW_NAMES[asset], str(asset), self._amount(data['amounts'][asset][1]), self._amount(data['amounts'][asset][0])]
            row += ([ROW_NAMES[liability], str(liability), self._amount(data['amounts'][liability][1]),
                     self._amount(data['amounts'][liability][0])] if liability <= 53 else ['', '', '', ''])
            rows.append(row)
        issues = data['issues']
        status = self.env._('Checks failed; resolve the listed issues before downloading.') if issues else self.env._('Arithmetic checks passed for accessible records; financial review is still required.')
        metadata = [self.company_id.display_name, str(self.date_to), str(data['opening_date']), self.currency_id.name,
                    self.mapping_id.name, str(self.mapping_id.id), TEMPLATE_VERSION, str(generated)]
        meta_headers = [self.env._('Company'), self.env._('Report Date'), self.env._('Opening Balance Date'),
                        self.env._('Currency'), self.env._('Mapping Version'), self.env._('Mapping ID'),
                        self.env._('Template Version'), self.env._('Generated at (UTC)')]
        result = Markup('<h3>资产负债表 · 会小企01表</h3>') + self._render_table(meta_headers, [metadata])
        result += Markup('<p><strong>%s</strong></p>') % status
        for issue in issues:
            result += Markup('<p>%s</p>') % issue
        result += Markup('<p>%s: %s / %s</p>') % (self.env._('Opening / closing equation difference'),
                                                 *[self._amount(value) for value in data['differences']])
        result += self._render_table(headers, rows)
        detail_headers = [self.env._('Account Code'), self.env._('Account Name'), self.env._('Partner'),
                          self.env._('Statement Row'), self.env._('Closing Balance'), self.env._('Opening Balance')]
        details = []
        for (account_id, partner_id, code), values in sorted(data['details'].items()):
            account = self.env['account.account'].browse(account_id)
            partner = self.env['res.partner'].browse(partner_id)
            details.append([account.code, account.name, partner.display_name if partner else '',
                            f'{code} · {ROW_NAMES[code]}' if code else self.env._('Unmapped'),
                            self._amount(values[1]), self._amount(values[0])])
        result += Markup('<h4>%s</h4>') % self.env._('Account Contributions') + self._render_table(detail_headers, details)
        payload = False
        if not issues:
            stream = io.StringIO()
            writer = csv.writer(stream)
            safe = self.env['qing.trial.balance']._csv_text
            writer.writerow(meta_headers)
            writer.writerow([safe(value) for value in metadata])
            writer.writerow([status])
            writer.writerow(headers)
            writer.writerows(rows)
            writer.writerow([])
            writer.writerow(detail_headers)
            for row in details:
                writer.writerow([safe(value) for value in row[:4]] + row[4:])
            payload = BinaryBytes(stream.getvalue().encode('utf-8-sig'))
        return {'result': result, 'result_file': payload,
                'result_filename': f'balance_sheet_{self.date_to}.csv' if payload else False}

    @api.onchange('company_id', 'date_to', 'mapping_id')
    def _onchange_scope(self):
        self.result = self.result_file = self.result_filename = False

    company_id = fields.Many2one('res.company', required=True, readonly=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    date_to = fields.Date(string='Report Date', required=True, default=fields.Date.context_today)
    mapping_id = fields.Many2one('qing.balance.sheet.mapping', required=True, check_company=True,
                                  domain="[('company_id', '=', company_id), ('state', '=', 'confirmed')]",
                                  string='Mapping Version')
    result = fields.Html(readonly=True)
    result_file = fields.Binary(string='CSV Download', readonly=True, attachment=False)
    result_filename = fields.Char(readonly=True)
    source_account_id = fields.Many2one('account.account', string='Source Account', check_company=True,
                                         domain="[('company_ids', 'in', company_id)]")

    def action_generate(self):
        self.write(self._result_values(self._report_data()))
        return {'type': 'ir.actions.act_window', 'name': self.env._('Chinese Small Business Balance Sheet'),
                'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'current'}

    def action_open_lines(self):
        domain = self._base_domain()
        if self.source_account_id:
            domain += [('account_id', '=', self.source_account_id.id)]
        action = self.env['ir.actions.actions']._for_xml_id('account.action_account_moves_all')
        action.update(domain=domain, context={'group_by': 'account_id'})
        return action

    def write(self, values):
        if {'company_id', 'date_to', 'mapping_id'} & values.keys():
            values = dict(values, result=False, result_file=False, result_filename=False)
        return super().write(values)
