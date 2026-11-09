import csv
import io

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Domain
from odoo.tools.binary import BinaryBytes


class AccountLedger(models.AbstractModel):
    _name = 'qing.account.ledger'
    _description = 'Account Ledger'

    def _check_ledger_access(self):
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(self.env._('Only accounting managers can open the account ledger.'))
        self.env['account.move.line'].check_access('read')

    @api.model
    def get_accounts(self):
        self._check_ledger_access()
        accounts = self.env['account.account'].with_context(active_test=False).search(
            [('company_ids', 'in', self.env.company.ids)], order='code, id',
        )
        return [{'id': account.id, 'name': f'{account.code} {account.name}'} for account in accounts]

    def _scope(self, account_id, date_from, date_to):
        self._check_ledger_access()
        if type(account_id) is not int or account_id <= 0:
            raise UserError(self.env._('Select an account.'))
        account = self.env['account.account'].browse(account_id).exists()
        account.check_access('read')
        if not account or self.env.company not in account.company_ids:
            raise UserError(self.env._('The account does not belong to the current company.'))
        try:
            start, end = fields.Date.to_date(date_from), fields.Date.to_date(date_to)
        except (ValueError, TypeError):
            raise UserError(self.env._('Select a valid date range.')) from None
        if not start or not end or start > end:
            raise UserError(self.env._('Select a valid date range.'))
        domain = Domain([
            ('company_id', '=', self.env.company.id), ('account_id', '=', account.id),
            ('parent_state', '=', 'posted'), ('date', '<=', end),
        ])
        return account, start, end, domain

    @api.model
    def get_report(self, account_id, date_from, date_to, offset=0, limit=80):
        account, start, end, domain = self._scope(account_id, date_from, date_to)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 10000:
            raise UserError(self.env._('Select a valid ledger page.'))
        Line = self.env['account.move.line'].with_context(recon_limit=end)
        currency = self.env.company.currency_id
        opening = Line._read_group(domain & Domain('date', '<', start), aggregates=['balance:sum'])[0][0] or 0
        period = domain & Domain('date', '>=', start)
        debit, credit = Line._read_group(period, aggregates=['debit:sum', 'credit:sum'])[0]
        debit, credit = debit or 0, credit or 0
        count = Line.search_count(period)
        lines = Line.search(period, order='date, id', offset=offset, limit=limit)
        running = opening
        if lines:
            first = lines[0]
            before_page = Domain('date', '<', first.date) | (
                Domain('date', '=', first.date) & Domain('id', '<', first.id)
            )
            running = Line._read_group(domain & before_page, aggregates=['balance:sum'])[0][0] or 0
        rows = []
        for line in lines:
            running = currency.round(running + line.balance)
            rows.append({
                'id': line.id, 'move_id': line.move_id.id, 'date': str(line.date),
                'move': line.move_id.name, 'journal': line.journal_id.name,
                'partner': line.partner_id.display_name or '', 'label': line.name or '',
                'debit': currency.round(line.debit), 'credit': currency.round(line.credit),
                'balance': currency.round(line.balance), 'running': running,
                'residual': currency.round(line.residual_at_date) if account.reconcile else False,
                'currency': line.currency_id.name,
                'amount_currency': line.amount_currency,
            })
        return {
            'company': self.env.company.name, 'currency': currency.name,
            'precision': currency.decimal_places, 'account': f'{account.code} {account.name}',
            'account_id': account.id, 'date_from': str(start), 'date_to': str(end),
            'opening': currency.round(opening), 'debit': currency.round(debit),
            'credit': currency.round(credit), 'closing': currency.round(opening + debit - credit),
            'rows': rows, 'count': count, 'offset': offset, 'limit': limit,
        }

    @api.model
    def export_csv(self, account_id, date_from, date_to):
        report = self.get_report(account_id, date_from, date_to, limit=10000)
        if report['count'] > 10000:
            raise UserError(self.env._('Limit the date range to 10,000 entries before exporting.'))
        stream = io.StringIO()
        writer = csv.writer(stream)
        safe = self.env['qing.trial.balance']._csv_text
        writer.writerow([safe(report['company']), safe(report['account']), report['currency'], date_from, date_to])
        writer.writerow([self.env._(text) for text in (
            'Date', 'Journal Entry', 'Journal', 'Partner', 'Label', 'Debit', 'Credit',
            'Entry Balance', 'Running Balance', 'Residual at End Date', 'Entry Currency', 'Amount in Currency',
        )])
        writer.writerow([self.env._('Opening Balance'), '', '', '', '', '', '', '', report['opening']])
        for row in report['rows']:
            writer.writerow([safe(str(row[key])) if key in ('move', 'journal', 'partner', 'label') else ('' if row[key] is False else row[key])
                             for key in ('date', 'move', 'journal', 'partner', 'label', 'debit', 'credit',
                                         'balance', 'running', 'residual', 'currency', 'amount_currency')])
        writer.writerow([self.env._('Closing Balance'), '', '', '', '', report['debit'], report['credit'], '', report['closing']])
        filename = f'account_ledger_{account_id}_{report["date_from"]}_{report["date_to"]}.csv'
        document = self.env['qing.financial.report.export'].create({
            'company_id': self.env.company.id, 'filename': filename,
            'file': BinaryBytes(stream.getvalue().encode('utf-8-sig')),
        })
        return {
            'type': 'ir.actions.act_url', 'target': 'download',
            'url': f'/web/content/{document._name}/{document.id}/file/{filename}?download=true',
        }
