import csv
import io
from collections import defaultdict
from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.binary import BinaryBytes

from ..models.balance_sheet_template import TEMPLATE_VERSION as BALANCE_TEMPLATE_VERSION
from ..models.profit_statement_template import INCOME_ROWS, ROW_NAMES, TEMPLATE_VERSION


REPORT_MODES = [('monthly', 'Monthly Report'), ('annual', 'Annual Report')]
SOURCE_SCOPES = [('primary', 'Year to Date / Current Year'), ('secondary', 'Current Month / Prior Year')]
PROFIT_ACCOUNT_TYPES = (
    'income', 'income_other', 'expense', 'expense_depreciation', 'expense_direct_cost',
)


class ProfitStatement(models.TransientModel):
    _name = 'qing.profit.statement'
    _description = 'Chinese Small Business Profit Statement'
    _check_company_auto = True

    @api.depends('date_to')
    def _compute_display_name(self):
        for record in self:
            record.display_name = f"{self.env._('Chinese Small Business Profit Statement')} · {record.date_to or ''}"

    def _base_domain(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(self.env._('Only accounting administrators can generate this report.'))
        if self.company_id != self.env.company:
            raise UserError(self.env._('Switch to the company selected on this report.'))
        if not self.date_to:
            raise UserError(self.env._('Select a report date.'))
        if self.report_mode == 'annual' and (self.date_to.month, self.date_to.day) != (12, 31):
            raise UserError(self.env._('Annual reports must use December 31 as the report date.'))
        return [
            ('company_id', '=', self.company_id.id),
            ('parent_state', '=', 'posted'),
            ('account_id.account_type', '!=', 'off_balance'),
        ]

    def _check_mappings(self):
        profit_mapping = self.mapping_id
        balance_mapping = self.balance_mapping_id
        profit_mapping.check_access('read')
        balance_mapping.check_access('read')
        if (
            not profit_mapping
            or profit_mapping.company_id != self.company_id
            or profit_mapping.state != 'confirmed'
        ):
            raise UserError(self.env._('Select a confirmed profit statement mapping for this company.'))
        if profit_mapping.template_version != TEMPLATE_VERSION:
            raise UserError(self.env._('This profit statement mapping uses an unsupported template version.'))
        if (
            not balance_mapping
            or balance_mapping.company_id != self.company_id
            or balance_mapping.state != 'confirmed'
        ):
            raise UserError(self.env._('Select a confirmed balance sheet mapping for this company.'))
        if balance_mapping.template_version != BALANCE_TEMPLATE_VERSION:
            raise UserError(self.env._('This balance sheet mapping uses an unsupported template version.'))

    def _scopes(self):
        self.ensure_one()
        base_domain = self._base_domain()
        current_start = self.date_to.replace(month=1, day=1)
        if self.report_mode == 'annual':
            prior_start = current_start.replace(year=current_start.year - 1)
            prior_end = current_start - timedelta(days=1)
            definitions = [
                (self.env._('Current Year'), current_start, self.date_to),
                (self.env._('Prior Year'), prior_start, prior_end),
            ]
        else:
            month_start = self.date_to.replace(day=1)
            definitions = [
                (self.env._('Year to Date'), current_start, self.date_to),
                (self.env._('Current Month'), month_start, self.date_to),
            ]
        return [
            {
                'label': label,
                'date_from': date_from,
                'date_to': date_to,
                'domain': base_domain + [('date', '>=', date_from), ('date', '<=', date_to)],
            }
            for label, date_from, date_to in definitions
        ]

    def _closing_moves(self, line_model, scope, mapped_ids, closing_ids):
        if not mapped_ids or not closing_ids:
            return self.env['account.move']
        candidates = self.env['account.move']
        target_domain = scope['domain'] + [('account_id', 'in', list(closing_ids))]
        for move, in line_model._read_group(target_domain, ['move_id'], []):
            candidates |= move
        if not candidates:
            return candidates
        by_move = defaultdict(dict)
        all_lines_domain = scope['domain'] + [('move_id', 'in', candidates.ids)]
        for move, account, balance in line_model._read_group(
            all_lines_domain, ['move_id', 'account_id'], ['balance:sum']
        ):
            if not self.currency_id.is_zero(balance):
                by_move[move.id][account.id] = balance
        accepted = self.env['account.move']
        permitted_ids = mapped_ids | closing_ids
        for move in candidates:
            account_balances = by_move.get(move.id, {})
            account_ids = set(account_balances)
            if (
                account_ids & mapped_ids
                and account_ids & closing_ids
                and account_ids <= permitted_ids
                and self.currency_id.is_zero(sum(account_balances.values()))
            ):
                accepted |= move
        return accepted

    def _sum_balance(self, line_model, domain):
        return sum(
            balance
            for _company, balance in line_model._read_group(domain, ['company_id'], ['balance:sum'])
        )

    def _apply_formulas(self, amounts):
        for index in (0, 1):
            amounts[3][index] += sum(amounts[row][index] for row in range(4, 11))
            amounts[11][index] += amounts[12][index] + amounts[13][index]
            amounts[14][index] += sum(amounts[row][index] for row in range(15, 18))
            amounts[18][index] += amounts[19][index]
            amounts[21][index] = (
                amounts[1][index]
                - amounts[2][index]
                - amounts[3][index]
                - amounts[11][index]
                - amounts[14][index]
                - amounts[18][index]
                + amounts[20][index]
            )
            amounts[22][index] += amounts[23][index]
            amounts[24][index] += sum(amounts[row][index] for row in range(25, 30))
            amounts[30][index] = amounts[21][index] + amounts[22][index] - amounts[24][index]
            amounts[32][index] = amounts[30][index] - amounts[31][index]

    def _report_data(self):
        self._check_mappings()
        scopes = self._scopes()
        line_model = self.env['account.move.line'].with_company(self.company_id)
        account_mapping = {
            line.account_id.id: int(line.line_code) for line in self.mapping_id.line_ids
        }
        mapped_ids = set(account_mapping)
        closing_ids = set(self.mapping_id.closing_account_ids.ids)
        balance_rows = {
            line.account_id.id: int(line.line_code) for line in self.balance_mapping_id.line_ids
        }
        capitalized_ids = {
            account_id for account_id, code in balance_rows.items() if 9 <= code <= 29
        }
        amounts = {code: [0.0, 0.0] for code in ROW_NAMES}
        details = defaultdict(lambda: [0.0, 0.0])
        issues = set()
        closing_moves = defaultdict(set)
        scope_closing_ids = []
        for index, scope in enumerate(scopes):
            accepted_closing = self._closing_moves(
                line_model, scope, mapped_ids, closing_ids
            )
            scope_closing_ids.append(accepted_closing.ids)
            for move in accepted_closing:
                closing_moves[move.id].add(scope['label'])
            contribution_domain = scope['domain'] + [('account_id', 'in', list(mapped_ids))]
            if accepted_closing:
                contribution_domain += [('move_id', 'not in', accepted_closing.ids)]
            for account, balance in line_model._read_group(
                contribution_domain, ['account_id'], ['balance:sum']
            ):
                code = account_mapping[account.id]
                signed = -balance if code in INCOME_ROWS else balance
                amounts[code][index] += signed
                details[account.id, code][index] += signed
            candidate_domain = scope['domain'] + [
                ('account_id.account_type', 'in', PROFIT_ACCOUNT_TYPES),
            ]
            if accepted_closing:
                candidate_domain += [('move_id', 'not in', accepted_closing.ids)]
            for account, balance in line_model._read_group(
                candidate_domain, ['account_id'], ['balance:sum']
            ):
                if (
                    not self.currency_id.is_zero(balance)
                    and account.id not in mapped_ids
                    and account.id not in capitalized_ids
                ):
                    issues.add(self.env._('Unmapped profit or loss account: %s', account.display_name))
                    details[account.id, 0][index] += balance
            ledger_balance = self._sum_balance(line_model, scope['domain'])
            if not self.currency_id.is_zero(ledger_balance):
                issues.add(self.env._('The accessible ledger is not balanced. Check permissions and source entries.'))
        for account_id in mapped_ids | closing_ids:
            if balance_rows.get(account_id) != 51:
                account = self.env['account.account'].browse(account_id)
                issues.add(self.env._(
                    'Profit or closing account is not mapped to balance sheet row 51: %s',
                    account.display_name,
                ))
        self._apply_formulas(amounts)
        amounts = {
            code: [self.currency_id.round(value) for value in values]
            for code, values in amounts.items()
        }
        current_start = self.date_to.replace(month=1, day=1)
        opening_date = current_start - timedelta(days=1)
        row_51_ids = [account_id for account_id, code in balance_rows.items() if code == 51]
        equity_domain = self._base_domain() + [('account_id', 'in', row_51_ids)]
        opening_equity = -self._sum_balance(
            line_model, equity_domain + [('date', '<=', opening_date)]
        )
        closing_equity = -self._sum_balance(
            line_model, equity_domain + [('date', '<=', self.date_to)]
        )
        equity_movement = self.currency_id.round(closing_equity - opening_equity)
        bridge_difference = self.currency_id.round(equity_movement - amounts[32][0])
        warnings = []
        if not self.currency_id.is_zero(bridge_difference):
            warnings.append(self.env._(
                'Retained earnings movement differs from year-to-date net profit by %s. '
                'Review profit distributions, prior-period adjustments, and equity reclassifications.',
                self._amount(bridge_difference),
            ))
        excluded = []
        move_model = self.env['account.move']
        for move_id, labels in sorted(closing_moves.items()):
            move = move_model.browse(move_id)
            excluded.append({
                'id': move.id,
                'name': move.name,
                'date': move.date,
                'reference': move.ref or '',
                'scopes': ', '.join(sorted(labels)),
            })
        return {
            'amounts': amounts,
            'bridge_difference': bridge_difference,
            'closing_equity': self.currency_id.round(closing_equity),
            'closing_moves': excluded,
            'details': details,
            'equity_movement': equity_movement,
            'issues': sorted(issues),
            'opening_date': opening_date,
            'opening_equity': self.currency_id.round(opening_equity),
            'scope_closing_ids': scope_closing_ids,
            'scopes': scopes,
            'warnings': warnings,
        }

    def _amount(self, value):
        return f'{self.currency_id.round(value):.{self.currency_id.decimal_places}f}'

    def _render_table(self, headers, rows):
        head = Markup('').join(Markup('<th>%s</th>') % label for label in headers)
        body = Markup('').join(
            Markup('<tr>%s</tr>')
            % Markup('').join(Markup('<td>%s</td>') % value for value in row)
            for row in rows
        )
        return Markup(
            '<div class="table-responsive"><table class="table table-bordered">'
            '<thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
        ) % (head, body)

    def _result_values(self, data):
        generated = fields.Datetime.now()
        if self.report_mode == 'annual':
            value_headers = ['本年金额', '上年金额']
        else:
            value_headers = ['本年累计金额', '本月金额']
        headers = ['项目', '行次', *value_headers]
        rows = [
            [ROW_NAMES[code], str(code), *[self._amount(value) for value in data['amounts'][code]]]
            for code in ROW_NAMES
        ]
        metadata = [
            self.company_id.display_name,
            str(self.date_to),
            dict(self._fields['report_mode']._description_selection(self.env))[self.report_mode],
            self.currency_id.name,
            self.mapping_id.name,
            str(self.mapping_id.id),
            self.balance_mapping_id.name,
            str(self.balance_mapping_id.id),
            TEMPLATE_VERSION,
            str(generated),
        ]
        meta_headers = [
            self.env._('Company'), self.env._('Report Date'), self.env._('Report Mode'),
            self.env._('Currency'), self.env._('Profit Mapping Version'),
            self.env._('Profit Mapping ID'), self.env._('Balance Mapping Version'),
            self.env._('Balance Mapping ID'), self.env._('Template Version'),
            self.env._('Generated at (UTC)'),
        ]
        status = (
            self.env._('Checks failed; resolve the listed issues before downloading.')
            if data['issues']
            else self.env._('Arithmetic checks passed for accessible records; financial review is still required.')
        )
        result = Markup('<h3>利润表 · 会小企02表</h3>')
        result += self._render_table(meta_headers, [metadata])
        result += Markup('<p><strong>%s</strong></p>') % status
        for issue in data['issues']:
            result += Markup('<p>%s</p>') % issue
        for warning in data['warnings']:
            result += Markup('<p>%s</p>') % warning
        result += self._render_table(headers, rows)
        bridge_headers = [
            self.env._('Opening Retained Earnings'), self.env._('Closing Retained Earnings'),
            self.env._('Retained Earnings Movement'), self.env._('Year-to-Date Net Profit'),
            self.env._('Equity Bridge Difference'),
        ]
        bridge = [[
            self._amount(data['opening_equity']), self._amount(data['closing_equity']),
            self._amount(data['equity_movement']), self._amount(data['amounts'][32][0]),
            self._amount(data['bridge_difference']),
        ]]
        result += Markup('<h4>%s</h4>') % self.env._('Balance Sheet Equity Bridge')
        result += self._render_table(bridge_headers, bridge)
        detail_headers = [
            self.env._('Account Code'), self.env._('Account Name'),
            self.env._('Statement Row'), *value_headers,
        ]
        details = []
        for (account_id, code), values in sorted(data['details'].items()):
            account = self.env['account.account'].browse(account_id)
            details.append([
                account.code, account.name,
                f'{code} · {ROW_NAMES[code]}' if code else self.env._('Unmapped'),
                *[self._amount(value) for value in values],
            ])
        result += Markup('<h4>%s</h4>') % self.env._('Account Contributions')
        result += self._render_table(detail_headers, details)
        closing_headers = [
            self.env._('Entry'), self.env._('Date'), self.env._('Reference'),
            self.env._('Excluded From'),
        ]
        closing_rows = [
            [move['name'], str(move['date']), move['reference'], move['scopes']]
            for move in data['closing_moves']
        ]
        result += Markup('<h4>%s</h4>') % self.env._('Excluded Earnings Closing Entries')
        result += self._render_table(closing_headers, closing_rows)
        payload = False
        if not data['issues']:
            stream = io.StringIO()
            writer = csv.writer(stream)
            safe = self.env['qing.trial.balance']._csv_text
            writer.writerow(meta_headers)
            writer.writerow([safe(value) for value in metadata])
            writer.writerow([status])
            for warning in data['warnings']:
                writer.writerow([safe(warning)])
            writer.writerow(headers)
            writer.writerows(rows)
            writer.writerow([])
            writer.writerow(bridge_headers)
            writer.writerows(bridge)
            writer.writerow([])
            writer.writerow(detail_headers)
            for row in details:
                writer.writerow([safe(value) for value in row[:3]] + row[3:])
            writer.writerow([])
            writer.writerow(closing_headers)
            for row in closing_rows:
                writer.writerow([safe(value) for value in row])
            payload = BinaryBytes(stream.getvalue().encode('utf-8-sig'))
        return {
            'result': result,
            'result_file': payload,
            'result_filename': f'profit_statement_{self.date_to}.csv' if payload else False,
        }

    @api.onchange(
        'balance_mapping_id', 'company_id', 'date_to', 'mapping_id', 'report_mode'
    )
    def _onchange_scope(self):
        self.result = self.result_file = self.result_filename = False

    balance_mapping_id = fields.Many2one(
        'qing.balance.sheet.mapping', required=True, check_company=True,
        domain="[('company_id', '=', company_id), ('state', '=', 'confirmed')]",
        string='Balance Mapping Version',
    )
    company_id = fields.Many2one(
        'res.company', required=True, readonly=True, default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(related='company_id.currency_id')
    date_to = fields.Date(string='Report Date', required=True, default=fields.Date.context_today)
    mapping_id = fields.Many2one(
        'qing.profit.statement.mapping', required=True, check_company=True,
        domain="[('company_id', '=', company_id), ('state', '=', 'confirmed')]",
        string='Profit Mapping Version',
    )
    report_mode = fields.Selection(REPORT_MODES, default='monthly', required=True)
    result = fields.Html(readonly=True)
    result_file = fields.Binary(string='CSV Download', readonly=True, attachment=False)
    result_filename = fields.Char(readonly=True)
    source_account_id = fields.Many2one(
        'account.account', string='Source Account', check_company=True,
        domain="[('company_ids', 'in', company_id)]",
    )
    source_scope = fields.Selection(SOURCE_SCOPES, default='primary', required=True)

    def action_generate(self):
        self.write(self._result_values(self._report_data()))
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Chinese Small Business Profit Statement'),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_open_lines(self):
        data = self._report_data()
        index = 0 if self.source_scope == 'primary' else 1
        domain = data['scopes'][index]['domain'] + [
            ('account_id', 'in', self.mapping_id.line_ids.account_id.ids),
        ]
        if data['scope_closing_ids'][index]:
            domain += [('move_id', 'not in', data['scope_closing_ids'][index])]
        if self.source_account_id:
            domain += [('account_id', '=', self.source_account_id.id)]
        action = self.env['ir.actions.actions']._for_xml_id('account.action_account_moves_all')
        action.update(domain=domain, context={'group_by': 'account_id'})
        return action

    def action_open_closing_entries(self):
        data = self._report_data()
        move_ids = [move['id'] for move in data['closing_moves']]
        action = self.env['ir.actions.actions']._for_xml_id('account.action_move_journal_line')
        action.update(domain=[('id', 'in', move_ids)], name=self.env._('Excluded Earnings Closing Entries'))
        return action

    def write(self, values):
        if {
            'balance_mapping_id', 'company_id', 'date_to', 'mapping_id', 'report_mode'
        } & values.keys():
            values = dict(values, result=False, result_file=False, result_filename=False)
        return super().write(values)
