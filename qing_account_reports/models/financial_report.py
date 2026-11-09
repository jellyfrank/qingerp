from collections import defaultdict
from datetime import timedelta
import csv
import io

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain

from .balance_sheet_template import ROW_NAMES as BALANCE_ROWS, SPLIT_ROWS, TOTALS as BALANCE_TOTALS
from .profit_statement_template import ROW_NAMES as PROFIT_ROWS
from .cash_flow_template import ROW_NAMES as CASH_ROWS


REPORT_MODELS = {
    'balance': 'qing.balance.sheet',
    'profit': 'qing.profit.statement',
}
PROFIT_COMPONENTS = {
    3: tuple(range(4, 11)), 11: (12, 13), 14: (15, 16, 17), 18: (19,),
    21: (1, -2, -3, -11, -14, -18, 20), 22: (23,),
    24: tuple(range(25, 30)), 30: (21, 22, -24), 32: (30, -31),
}


class FinancialReport(models.AbstractModel):
    _name = 'qing.financial.report'
    _description = 'Financial Statement Read Service'

    def _prepare(self, report_type, date_to, report_mode='monthly'):
        model_name = REPORT_MODELS.get(report_type)
        if not model_name:
            raise UserError(self.env._('Select a supported financial statement.'))
        report_date = fields.Date.to_date(date_to or fields.Date.context_today(self))
        if not report_date:
            raise UserError(self.env._('Select a report date.'))
        if report_date > fields.Date.context_today(self):
            raise UserError(self.env._('The report date cannot be in the future.'))
        scheme = self.env['qing.financial.report.scheme']._resolve(self.env.company, report_date)
        if not scheme:
            return False, False
        values = {
            'company_id': self.env.company.id,
            'date_to': report_date,
            'mapping_id': (scheme.balance_mapping_id if report_type == 'balance' else scheme.profit_mapping_id).id,
        }
        if report_type == 'profit':
            if report_mode not in ('monthly', 'annual'):
                raise UserError(self.env._('Select a supported reporting period.'))
            values.update(balance_mapping_id=scheme.balance_mapping_id.id, report_mode=report_mode)
        return scheme, self.env[model_name].create(values)

    def _trial_balance_wizard(self, date_from=False, date_to=False):
        values = {}
        if date_from:
            values['date_from'] = date_from
        if date_to:
            values['date_to'] = date_to
        return self.env['qing.trial.balance'].create(values)

    def _trial_balance_report(self, date_from=False, date_to=False):
        wizard = self._trial_balance_wizard(date_from, date_to)
        data = wizard._report_data()
        rows = [
            {
                'code': row['code'],
                'name': row['name'],
                'amounts': [wizard._amount(row[key]) for key in (
                    'opening_debit', 'opening_credit', 'debit', 'credit',
                    'closing_debit', 'closing_credit',
                )],
            }
            for row in data['rows']
        ]
        totals = [wizard._amount(data['totals'][key]) for key in (
            'opening_debit', 'opening_credit', 'debit', 'credit',
            'closing_debit', 'closing_credit',
        )]
        return {
            'status': 'ready',
            'report_type': 'trial_balance',
            'company': wizard.company_id.display_name,
            'currency': wizard.currency_id.name,
            'date_from': str(wizard.date_from),
            'date_to': str(wizard.date_to),
            'generated_at': str(fields.Datetime.now()),
            'headers': wizard._headers(),
            'rows': rows,
            'totals': totals,
            'differences': [wizard._amount(value) for value in data['differences']],
            'difference_label': self.env._('Opening / period / closing debit-credit difference'),
            'empty': not bool(rows),
            'empty_message': self.env._('No posted ledger entries were found for the selected date range.'),
        }

    @api.model
    def open_trial_balance_lines(self, date_from=False, date_to=False):
        wizard = self._trial_balance_wizard(date_from, date_to)
        return wizard.action_open_lines()

    def _cash_report(self, date_to, report_mode):
        report_date = fields.Date.to_date(date_to or fields.Date.context_today(self))
        if not report_date or report_date > fields.Date.context_today(self):
            raise UserError(self.env._('Select a valid report date.'))
        scheme = self.env['qing.financial.report.scheme']._resolve(self.env.company, report_date)
        if not scheme:
            return {
                'status': 'setup', 'report_type': 'cash',
                'company': self.env.company.display_name,
                'date_to': str(report_date),
                'message': self.env._('No active report scheme applies to this company and date.'),
            }
        data = self.env['qing.cash.flow.calculator'].report_data(scheme, report_date, report_mode)
        data.update({
            'status': 'incomplete' if data['issues'] else 'ready',
            'report_type': 'cash', 'company': self.env.company.display_name,
            'currency': self.env.company.currency_id.name, 'date_to': str(report_date),
            'generated_at': str(fields.Datetime.now()),
            'scheme': scheme.name, 'scheme_id': scheme.id,
            'scheme_source': scheme.source, 'scheme_from': str(scheme.date_from),
            'scheme_to': str(scheme.date_to) if scheme.date_to else '',
            'balance_mapping': scheme.balance_mapping_id.name,
            'profit_mapping': scheme.profit_mapping_id.name,
        })
        return data

    def _amounts(self, wizard, values):
        return [wizard._amount(value) for value in values]

    def _source_row_codes(self, row_code, components):
        codes = {row_code}
        for part in components.get(row_code, ()):
            codes.update(self._source_row_codes(abs(part), components))
        return codes

    def _account_row_source_domain(self, wizard, data, report_type, row_code, column):
        components = BALANCE_TOTALS if report_type == 'balance' else PROFIT_COMPONENTS
        included_rows = self._source_row_codes(row_code, components)
        split_accounts = (
            {line.account_id.id for line in wizard.mapping_id.line_ids
             if int(line.line_code) in SPLIT_ROWS}
            if report_type == 'balance' else set()
        )
        unrestricted_accounts = set()
        partners_by_account = defaultdict(set)
        for key, amounts in data['details'].items():
            if report_type == 'balance':
                account_id, partner_id, source_row = key
            else:
                account_id, source_row = key
                partner_id = False
            if source_row not in included_rows or wizard.currency_id.is_zero(amounts[column]):
                continue
            if account_id in split_accounts:
                partners_by_account[account_id].add(partner_id or False)
            else:
                unrestricted_accounts.add(account_id)
        branches = []
        if unrestricted_accounts:
            branches.append(Domain('account_id', 'in', list(unrestricted_accounts)))
        for account_id, partner_ids in partners_by_account.items():
            branches.append(
                Domain('account_id', '=', account_id)
                & Domain('partner_id', 'in', list(partner_ids))
            )
        return Domain.OR(branches) if branches else Domain.FALSE

    def _cash_row_source_domain(self, data, row_code, column, report_mode):
        if row_code in (21, 22):
            scheme = self.env['qing.financial.report.scheme'].browse(data['scheme_id'])
            periods = self.env['qing.cash.flow.calculator']._periods(
                fields.Date.to_date(data['date_to']), report_mode,
            )
            start, end = periods[column]
            cutoff = start - timedelta(days=1) if row_code == 21 else end
            return Domain([
                ('company_id', '=', self.env.company.id),
                ('parent_state', '=', 'posted'),
                ('account_id', 'in', scheme.cash_account_ids.ids),
                ('date', '<=', cutoff),
            ])
        source_ids = {
            detail['move_line_id']
            for detail in data['details'].get(str(row_code), [])
            if detail['amounts'][column] != ''
        }
        return Domain('id', 'in', list(source_ids))

    def _expanded_details(self, wizard, direct, components, row_names):
        cache = {}

        def expand(code):
            if code not in cache:
                entries = list(direct.get(code, []))
                for part in components.get(code, ()):
                    factor = 1 if part > 0 else -1
                    for item in expand(abs(part)):
                        entries.append(dict(item, values=[factor * value for value in item['values']]))
                cache[code] = entries
            return cache[code]

        return {
            str(code): [
                {key: value for key, value in item.items() if key != 'values'}
                | {'amounts': self._amounts(wizard, item['values'])}
                for item in expand(code)
            ]
            for code in row_names
        } | {
            '0': [
                {key: value for key, value in item.items() if key != 'values'}
                | {'amounts': self._amounts(wizard, item['values'])}
                for item in direct.get(0, [])
            ],
        }

    def _balance_payload(self, wizard, data):
        rows = [
            {'code': code, 'name': name, 'amounts': self._amounts(wizard, data['amounts'][code]),
             'side': 'asset' if code <= 30 else 'liability'}
            for code, name in BALANCE_ROWS.items()
        ]
        direct = defaultdict(list)
        account_model = self.env['account.account']
        partner_model = self.env['res.partner']
        for (account_id, partner_id, row), values in sorted(data['details'].items()):
            account = account_model.browse(account_id)
            partner = partner_model.browse(partner_id)
            direct[row].append({
                'account_id': account_id, 'account_code': account.code,
                'account_name': account.name, 'partner_id': partner_id,
                'partner': partner.display_name if partner else '', 'values': values,
            })
        return {
            'rows': rows, 'details': self._expanded_details(wizard, direct, BALANCE_TOTALS, BALANCE_ROWS),
            'differences': self._amounts(wizard, data['differences']),
            'opening_date': str(data['opening_date']),
            'headers': ['年初余额', '期末余额'],
        }

    def _profit_payload(self, wizard, data):
        labels = (['本年金额', '上年金额'] if wizard.report_mode == 'annual'
                  else ['本年累计金额', '本月金额'])
        rows = [
            {'code': code, 'name': name, 'amounts': self._amounts(wizard, data['amounts'][code])}
            for code, name in PROFIT_ROWS.items()
        ]
        direct = defaultdict(list)
        account_model = self.env['account.account']
        for (account_id, row), values in sorted(data['details'].items()):
            account = account_model.browse(account_id)
            direct[row].append({
                'account_id': account_id, 'account_code': account.code,
                'account_name': account.name, 'partner_id': False,
                'partner': '', 'values': values,
            })
        return {
            'rows': rows, 'details': self._expanded_details(wizard, direct, PROFIT_COMPONENTS, PROFIT_ROWS),
            'headers': labels,
            'warnings': data['warnings'],
            'bridge_difference': wizard._amount(data['bridge_difference']),
            'closing_moves': data['closing_moves'],
        }

    @api.model
    def get_report(self, report_type, date_to=False, report_mode='monthly', date_from=False):
        if report_type == 'trial_balance':
            return self._trial_balance_report(date_from, date_to)
        if report_type == 'cash':
            return self._cash_report(date_to, report_mode)
        scheme, wizard = self._prepare(report_type, date_to, report_mode)
        if not scheme:
            return {
                'status': 'setup', 'report_type': report_type,
                'company': self.env.company.display_name,
                'date_to': str(fields.Date.to_date(date_to or fields.Date.context_today(self))),
                'message': self.env._('No active report scheme applies to this company and date. Configure the scheme once in Accounting Configuration.'),
            }
        data = wizard._report_data()
        payload = (self._balance_payload(wizard, data) if report_type == 'balance'
                   else self._profit_payload(wizard, data))
        payload.update({
            'status': 'incomplete' if data['issues'] else 'ready',
            'report_type': report_type, 'company': wizard.company_id.display_name,
            'currency': wizard.currency_id.name, 'date_to': str(wizard.date_to),
            'generated_at': str(fields.Datetime.now()),
            'empty': not bool(data['details']),
            'empty_message': self.env._('No non-zero statement amounts for the selected date.'),
            'issues': data['issues'], 'scheme': scheme.name, 'scheme_id': scheme.id,
            'scheme_source': scheme.source, 'scheme_from': str(scheme.date_from),
            'scheme_to': str(scheme.date_to) if scheme.date_to else '',
            'balance_mapping': scheme.balance_mapping_id.name,
            'profit_mapping': scheme.profit_mapping_id.name,
        })
        return payload

    @api.model
    def export_csv(self, report_type, date_to, report_mode='monthly', date_from=False):
        if report_type == 'trial_balance':
            wizard = self._trial_balance_wizard(date_from, date_to)
            wizard.action_generate()
            return {
                'type': 'ir.actions.act_url',
                'target': 'download',
                'url': '/web/content/%s/%s/result_file/%s?download=true' % (
                    wizard._name, wizard.id, wizard.result_filename,
                ),
            }
        if report_type == 'cash':
            return self._cash_export(date_to, report_mode, 'csv')
        _scheme, wizard = self._prepare(report_type, date_to, report_mode)
        if not wizard:
            raise UserError(self.env._('Configure the report scheme before downloading.'))
        wizard.action_generate()
        if not wizard.result_file:
            raise UserError(self.env._('Resolve the listed report issues before downloading a complete statement.'))
        return {
            'type': 'ir.actions.act_url',
            'target': 'download',
            'url': '/web/content/%s/%s/result_file/%s?download=true' % (
                wizard._name, wizard.id, wizard.result_filename,
            ),
        }

    @api.model
    def export_formatted(self, report_type, date_to, report_mode='monthly', file_type='pdf'):
        if file_type not in ('pdf', 'xlsx'):
            raise UserError(self.env._('Select a supported export format.'))
        if report_type == 'cash':
            return self._cash_export(date_to, report_mode, file_type)
        scheme, wizard = self._prepare(report_type, date_to, report_mode)
        if not wizard:
            raise UserError(self.env._('Configure the report scheme before downloading.'))
        data = wizard._report_data()
        if data['issues']:
            raise UserError(self.env._('Resolve the listed report issues before downloading a complete statement.'))
        Export = self.env['qing.financial.report.export']
        if file_type == 'pdf':
            values = wizard._result_values(data)
            document = Export.create({'company_id': self.env.company.id, 'html': values['result']})
            return self.env.ref('qing_account_reports.action_financial_statement_pdf').report_action(document, config=False)
        metadata = {
            'company': self.env.company.display_name, 'date_to': str(wizard.date_to),
            'currency': wizard.currency_id.name, 'scheme': scheme.name,
            'balance_mapping': scheme.balance_mapping_id.name,
            'profit_mapping': scheme.profit_mapping_id.name,
        }
        content = Export._make_xlsx(report_type, wizard, data, metadata)
        filename = f'{report_type}_statement_{wizard.date_to}.xlsx'
        document = Export.create({
            'company_id': self.env.company.id, 'file': content, 'filename': filename,
        })
        return {
            'type': 'ir.actions.act_url', 'target': 'download',
            'url': f'/web/content/{document._name}/{document.id}/file/{filename}?download=true',
        }

    def _cash_export(self, date_to, report_mode, file_type):
        data = self._cash_report(date_to, report_mode)
        if data['status'] != 'ready':
            raise UserError(self.env._('Resolve the listed cash flow issues before downloading a complete statement.'))
        Export = self.env['qing.financial.report.export']
        if file_type == 'pdf':
            rows = Markup('').join(
                Markup('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>') % (
                    row['code'], row['name'], *row['amounts'],
                ) for row in data['rows']
            )
            html = (Markup('<h3>现金流量表 · 会小企03表</h3><p>%s · %s · %s</p>') % (
                data['company'], data['date_to'], data['currency'],
            ) + Markup('<table class="table table-bordered"><thead><tr><th>行次</th><th>项目</th><th>%s</th><th>%s</th></tr></thead><tbody>%s</tbody></table>') % (
                *data['headers'], rows,
            ))
            document = Export.create({'company_id': self.env.company.id, 'html': html})
            return self.env.ref('qing_account_reports.action_financial_statement_pdf').report_action(document, config=False)
        if file_type == 'xlsx':
            content = Export._make_cash_xlsx(data)
            extension = 'xlsx'
        else:
            stream = io.StringIO()
            writer = csv.writer(stream)
            safe = self.env['qing.trial.balance']._csv_text
            writer.writerow(['公司', safe(data['company']), '报表日期', data['date_to']])
            writer.writerow(['行次', '项目', *data['headers']])
            writer.writerows([row['code'], safe(row['name']), *row['amounts']]
                             for row in data['rows'])
            from odoo.tools.binary import BinaryBytes
            content = BinaryBytes(stream.getvalue().encode('utf-8-sig'))
            extension = 'csv'
        filename = f'cash_flow_{data["date_to"]}.{extension}'
        document = Export.create({
            'company_id': self.env.company.id, 'file': content, 'filename': filename,
        })
        return {
            'type': 'ir.actions.act_url', 'target': 'download',
            'url': f'/web/content/{document._name}/{document.id}/file/{filename}?download=true',
        }

    @api.model
    def export_diagnostics(self, report_type, date_to, report_mode='monthly'):
        data = self.get_report(report_type, date_to, report_mode)
        if data['status'] != 'incomplete':
            raise UserError(self.env._('A diagnostic file is available only for an incomplete statement.'))
        stream = io.StringIO()
        writer = csv.writer(stream)
        safe = self.env['qing.trial.balance']._csv_text
        writer.writerow(['INCOMPLETE · DIAGNOSTIC ONLY · NOT A FINANCIAL STATEMENT'])
        writer.writerow(['Company', safe(data['company']), 'Report date', data['date_to']])
        writer.writerow(['Issue'])
        writer.writerows([[safe(issue)] for issue in data['issues']])
        if report_type == 'cash':
            writer.writerow([])
            writer.writerow(['Cash journal item ID', 'Date', 'Journal entry', 'Unclassified amount'])
            for item in data['unclassified_lines']:
                writer.writerow([item['id'], item['date'], safe(item['move']), item['amount']])
        elif data['details'].get('0'):
            writer.writerow([])
            writer.writerow(['Account code', 'Account name', 'Partner', 'Unmapped amount 1',
                             'Unmapped amount 2'])
            for item in data['details']['0']:
                writer.writerow([safe(item['account_code']), safe(item['account_name']),
                                 safe(item['partner']), *item['amounts']])
        from odoo.tools.binary import BinaryBytes
        filename = f'{report_type}_issues_{data["date_to"]}.csv'
        document = self.env['qing.financial.report.export'].create({
            'company_id': self.env.company.id,
            'file': BinaryBytes(stream.getvalue().encode('utf-8-sig')),
            'filename': filename,
        })
        return {
            'type': 'ir.actions.act_url', 'target': 'download',
            'url': f'/web/content/{document._name}/{document.id}/file/{filename}?download=true',
        }

    @api.model
    def open_row_source(self, report_type, date_to, row_code, column,
                        report_mode='monthly'):
        if type(column) is not int or column not in (0, 1):
            raise UserError(self.env._('Select a supported reporting column.'))
        try:
            row_code = int(row_code)
        except (TypeError, ValueError):
            raise UserError(self.env._('Select a supported statement row.')) from None
        row_names = {'balance': BALANCE_ROWS, 'profit': PROFIT_ROWS, 'cash': CASH_ROWS}.get(report_type)
        if not row_names or row_code not in row_names:
            raise UserError(self.env._('Select a supported statement row.'))
        if report_type == 'cash':
            data = self._cash_report(date_to, report_mode)
            if data['status'] == 'setup':
                raise UserError(self.env._('Configure the report scheme before opening source entries.'))
            source_domain = self._cash_row_source_domain(data, row_code, column, report_mode)
        else:
            _scheme, wizard = self._prepare(report_type, date_to, report_mode)
            if not wizard:
                raise UserError(self.env._('Configure the report scheme before opening source entries.'))
            data = wizard._report_data()
            source_domain = self._account_row_source_domain(
                wizard, data, report_type, row_code, column,
            )
            if report_type == 'balance':
                base_domain = wizard._base_domain()
                if column == 0:
                    base_domain += [('date', '<=', data['opening_date'])]
            else:
                base_domain = list(data['scopes'][column]['domain'])
                if data['scope_closing_ids'][column]:
                    base_domain += [('move_id', 'not in', data['scope_closing_ids'][column])]
            source_domain &= Domain(base_domain)
        if report_type == 'balance':
            headers = ['年初余额', '期末余额']
        elif report_type == 'profit':
            headers = (['本年金额', '上年金额'] if report_mode == 'annual'
                       else ['本年累计金额', '本月金额'])
        else:
            headers = data['headers']
        header = headers[column]
        action = self.env['ir.actions.actions']._for_xml_id('account.action_account_moves_all')
        action.update(
            name=f"{row_names[row_code]} · {header} · {self.env._('Journal Items')}",
            domain=list(source_domain),
            context={},
        )
        return action

    @api.model
    def open_source(self, report_type, date_to, account_id, partner_id=False,
                    report_mode='monthly', scope='primary', move_line_id=False):
        if report_type == 'cash':
            self._cash_report(date_to, report_mode)
            line = self.env['account.move.line'].browse(move_line_id).exists()
            line.check_access('read')
            if not line or line.company_id != self.env.company:
                raise UserError(self.env._('The source entry does not belong to the current company.'))
            action = self.env['ir.actions.actions']._for_xml_id('account.action_account_moves_all')
            action.update(domain=[('id', '=', line.id)], context={})
            return action
        _scheme, wizard = self._prepare(report_type, date_to, report_mode)
        if not wizard:
            raise UserError(self.env._('Configure the report scheme before opening source entries.'))
        account = self.env['account.account'].browse(account_id).exists()
        account.check_access('read')
        if not account or self.env.company not in account.company_ids:
            raise UserError(self.env._('The source account does not belong to the current company.'))
        wizard.source_account_id = account
        if scope not in ('primary', 'secondary'):
            raise UserError(self.env._('Select a supported reporting period.'))
        if report_type == 'profit':
            wizard.source_scope = scope
        action = wizard.action_open_lines()
        if report_type == 'balance' and scope == 'secondary':
            opening_date = wizard.date_to.replace(month=1, day=1) - timedelta(days=1)
            action['domain'] += [('date', '<=', opening_date)]
        if partner_id:
            action['domain'] += [('partner_id', '=', partner_id)]
        return action

    @api.model
    def open_cash_classification(self, date_to, move_line_id, report_mode='monthly'):
        data = self._cash_report(date_to, report_mode)
        line = self.env['account.move.line'].browse(move_line_id).exists()
        line.check_access('read')
        scheme = self.env['qing.financial.report.scheme'].browse(data.get('scheme_id'))
        if (not line or line.company_id != self.env.company or line.parent_state != 'posted'
                or line.account_id not in scheme.cash_account_ids):
            raise UserError(self.env._('Select a posted cash journal item in this company.'))
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('Classify Cash Flow'),
            'res_model': 'qing.cash.flow.allocation', 'view_mode': 'list,form',
            'domain': [('cash_line_id', '=', line.id)],
            'context': {'default_cash_line_id': line.id, 'default_company_id': line.company_id.id},
            'target': 'current',
        }
