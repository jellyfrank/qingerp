import csv
import io

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.binary import BinaryBytes


AMOUNT_KEYS = ('opening_debit', 'opening_credit', 'debit', 'credit', 'closing_debit', 'closing_credit')


class TrialBalance(models.TransientModel):
    _name = 'qing.trial.balance'
    _description = 'Account Balances and Trial Balance'

    def _base_domain(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(self.env._('Only accounting administrators can generate this report.'))
        if self.company_id != self.env.company:
            raise UserError(self.env._('Switch to the company selected on this report.'))
        if not self.date_from or not self.date_to or self.date_from > self.date_to:
            raise UserError(self.env._('The start date must not be later than the end date.'))
        return [('company_id', '=', self.company_id.id), ('parent_state', '=', 'posted'),
                ('account_id', '!=', False), ('date', '<=', self.date_to)]

    def _report_data(self):
        """Aggregate posted ledger lines without changing balances or closing earnings."""
        domain = self._base_domain()
        Line = self.env['account.move.line'].with_company(self.company_id)
        opening = Line._read_group(domain + [('date', '<', self.date_from)],
                                   ['account_id'], ['balance:sum'])
        period = Line._read_group(domain + [('date', '>=', self.date_from)],
                                  ['account_id'], ['debit:sum', 'credit:sum'])
        amounts = {account: [balance, 0.0, 0.0] for account, balance in opening}
        for account, debit, credit in period:
            amounts.setdefault(account, [0.0, 0.0, 0.0])[1:] = [debit, credit]
        rows = []
        currency = self.currency_id
        for account, (balance, debit, credit) in sorted(amounts.items(), key=lambda item: (item[0].code, item[0].id)):
            closing = currency.round(balance + debit - credit)
            rows.append(dict(zip(AMOUNT_KEYS, [max(balance, 0), max(-balance, 0), debit, credit,
                                               max(closing, 0), max(-closing, 0)]),
                             account_id=account.id, code=account.code, name=account.name))
        totals = {key: sum(row[key] for row in rows) for key in AMOUNT_KEYS}
        differences = [currency.round(totals[left] - totals[right]) for left, right in (
            ('opening_debit', 'opening_credit'), ('debit', 'credit'), ('closing_debit', 'closing_credit'))]
        return {'rows': rows, 'totals': totals, 'differences': differences}

    def _headers(self):
        return [
            self.env._('Account Code'),
            self.env._('Account Name'),
            self.env._('Opening Debit'),
            self.env._('Opening Credit'),
            self.env._('Period Debit'),
            self.env._('Period Credit'),
            self.env._('Closing Debit'),
            self.env._('Closing Credit'),
        ]

    def _amount(self, value):
        return f'{self.currency_id.round(value):.{self.currency_id.decimal_places}f}'

    def _result_values(self, data):
        headers = self._headers()
        body = [[row['code'], row['name']] + [self._amount(row[key]) for key in AMOUNT_KEYS]
                for row in data['rows']]
        body.append([self.env._('Total'), ''] + [self._amount(data['totals'][key]) for key in AMOUNT_KEYS])
        head = Markup('').join(Markup('<th>%s</th>') % label for label in headers)
        rows = Markup('').join(Markup('<tr>%s</tr>') % Markup('').join(
            Markup('<td>%s</td>') % value for value in row) for row in body)
        metadata = [self.company_id.display_name, str(self.date_from), str(self.date_to), self.currency_id.name]
        title = Markup('<p>%s / %s — %s / %s</p>') % tuple(metadata)
        check = Markup('<p>%s: %s / %s / %s</p>') % (
            self.env._('Opening / period / closing debit-credit difference'),
            *[self._amount(value) for value in data['differences']])
        result = title + check + Markup('<div class="table-responsive"><table class="table table-bordered"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>') % (head, rows)
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow([self.env._('Company'), self.env._('Start Date'), self.env._('End Date'), self.env._('Currency')])
        writer.writerow([self._csv_text(value) for value in metadata])
        writer.writerow(headers)
        for row in body:
            writer.writerow([self._csv_text(row[0]), self._csv_text(row[1]), *row[2:]])
        return {'result': result, 'result_file': BinaryBytes(stream.getvalue().encode('utf-8-sig')),
                'result_filename': f'trial_balance_{self.date_from}_{self.date_to}.csv'}

    def _csv_text(self, value):
        text = str(value or '')
        if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
            return "'" + text
        return text

    @api.onchange('company_id', 'date_from', 'date_to')
    def _onchange_scope(self):
        self.result = False
        self.result_file = False
        self.result_filename = False

    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True, readonly=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    date_from = fields.Date(string='Start Date', required=True,
                            default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date(string='End Date', required=True, default=fields.Date.context_today)
    result = fields.Html(readonly=True)
    result_file = fields.Binary(string='CSV Download', readonly=True, attachment=False)
    result_filename = fields.Char(readonly=True)

    def action_generate(self):
        data = self._report_data()
        self.write(self._result_values(data))
        return {'type': 'ir.actions.act_window', 'name': self.env._('Account Balances and Trial Balance'),
                'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'new'}

    def action_open_lines(self):
        domain = self._base_domain()
        action = self.env['ir.actions.actions']._for_xml_id('account.action_account_moves_all')
        action.update(domain=domain, context={'group_by': 'account_id'}, name=self.env._('Posted Ledger through End Date'))
        return action

    def write(self, values):
        if {'company_id', 'date_from', 'date_to'} & values.keys():
            values = dict(values, result=False, result_file=False, result_filename=False)
        return super().write(values)
