import csv
import io

from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestPartnerStatement(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(cls.env, login='statement_manager', groups='account.group_account_manager',
                                    company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)])
        cls.billing = new_test_user(cls.env, login='statement_billing', groups='account.group_account_invoice')

    def _line(self, amount, date='2026-09-01', due='2026-09-10', payable=False, currency=None, fx=None, posted=True):
        account = self.company_data['default_account_payable' if payable else 'default_account_receivable']
        values = {'account_id': account.id, 'partner_id': self.partner_a.id, 'balance': amount, 'date_maturity': due}
        if currency:
            values.update(currency_id=currency.id, amount_currency=fx)
        move = self.env['account.move'].create({
            'journal_id': self.company_data['default_journal_misc'].id, 'date': date,
            'line_ids': [Command.create(values), Command.create({
                'account_id': self.company_data['default_account_revenue'].id, 'balance': -amount})],
        })
        if posted:
            move.action_post()
        return move.line_ids.filtered(lambda line: line.account_id == account)

    def _wizard(self, **values):
        return self.env['qing.partner.statement'].with_user(self.manager).create({
            'date_from': '2026-09-01', 'date_to': '2026-09-30',
            'partner_ids': [Command.set(self.partner_a.ids)], **values,
        })

    def test_partial_and_future_payment_cutoff(self):
        invoice = self._line(1000, '2026-08-01')
        payment = self._line(-300, '2026-09-15')
        future = self._line(-200, '2026-10-01')
        (invoice | payment | future).reconcile()
        self.assertEqual(invoice.amount_residual, 500)
        wizard = self._wizard()
        before = (invoice | payment | future).read(['write_date', 'balance', 'amount_residual'])
        data = wizard._statement_data()
        group = data['groups'][0]
        self.assertEqual((group['opening'], group['credit'], group['closing'], group['residual']), (1000, 300, 700, 700))
        self.assertEqual(group['aging'][1], 700)
        self.assertEqual(len(data['details']), 2)
        wizard.action_generate()
        self.assertTrue(wizard.result_file)
        self.assertEqual(before, (invoice | payment | future).read(['write_date', 'balance', 'amount_residual']))
        wizard.date_to = '2026-10-31'
        self.assertFalse(wizard.result)
        self.assertFalse(wizard.result_file)
        self.assertEqual(wizard._statement_data()['groups'][0]['residual'], 500)

    def test_payable_credit_and_unapplied_payment(self):
        bill = self._line(-800, payable=True)
        payment = self._line(200, payable=True)
        (bill | payment).reconcile()
        self._line(100, payable=True)
        group = self._wizard(account_type='liability_payable')._statement_data()['groups'][0]
        self.assertEqual(group['residual'], -500)
        self.assertEqual(group['aging'][1], 600)
        self.assertEqual(group['aging'][5], -100)

    def test_foreign_currency_partial(self):
        currency = self.env.ref('base.EUR')
        if currency == self.env.company.currency_id:
            currency = self.env.ref('base.USD')
        currency.active = True
        invoice = self._line(700, currency=currency, fx=100)
        payment = self._line(-350, '2026-09-15', currency=currency, fx=-50)
        (invoice | payment).reconcile()
        group = self._wizard()._statement_data()['groups'][0]
        self.assertEqual((group['residual'], group['residual_currency']), (350, 50))

    def test_aging_boundaries(self):
        for due in ['2026-09-30', '2026-09-29', '2026-08-31', '2026-08-30',
                    '2026-08-01', '2026-07-31', '2026-07-02', '2026-07-01']:
            self._line(10, '2026-06-01', due)
        group = self._wizard()._statement_data()['groups'][0]
        self.assertEqual(group['aging'], [10, 20, 20, 20, 10, 0])

    def test_draft_excluded_credit_note_and_missing_partner(self):
        self._line(900, posted=False)
        self._line(-100)
        missing = self._line(50, posted=False)
        missing.partner_id = False
        missing.move_id.action_post()
        wizard = self._wizard(partner_ids=[Command.clear()])
        data = wizard._statement_data()
        self.assertEqual(len(data['details']), 2)
        self.assertIn(False, [group['partner'].id for group in data['groups']])
        wizard.action_generate()
        self.assertIn('未填写往来单位', wizard.result)

    def test_permissions_company_and_date(self):
        self._line(100)
        wizard = self._wizard()
        for method in ['action_generate', 'action_open_lines']:
            with self.assertRaises(AccessError):
                getattr(wizard.with_user(self.billing), method)()
        other = self.env['res.company'].create({'name': 'Statement Other'})
        with self.assertRaises(UserError):
            wizard.with_company(other).action_generate()
        wizard.date_from = '2026-10-01'
        with self.assertRaises(UserError):
            wizard.action_generate()

    def test_form_csv_escaping_and_drilldown(self):
        line = self._line(100)
        line.name = '=SUM(1,2)<script>bad</script>'
        with Form(self.env['qing.partner.statement'].with_user(self.manager)) as form:
            form.date_from = '2026-09-01'
            form.date_to = '2026-09-30'
        wizard = form.record
        self.partner_a.name = '<img src=x onerror=alert(1)>'
        self.env.company.name = '=Company'
        wizard.action_generate()
        self.assertNotIn('<img', wizard.result)
        rows = list(csv.reader(io.StringIO(wizard.result_file.content.decode('utf-8-sig'))))
        self.assertTrue(rows[1][5].startswith("'=SUM"))
        self.assertEqual(rows[1][16], "'=Company")
        self.assertEqual(rows[1][-1], "2026-09-30")
        action = wizard.action_open_lines()
        self.assertIn(line, self.env['account.move.line'].search(action['domain']))

    def test_unreconcile_recomputes_from_current_relationships(self):
        invoice = self._line(100)
        payment = self._line(-40)
        (invoice | payment).reconcile()
        wizard = self._wizard()
        before = wizard._statement_data()
        self.assertEqual(next(item['residual'] for item in before['details'] if item['line'] == invoice), 60)
        invoice.remove_move_reconcile()
        after = wizard._statement_data()
        self.assertEqual(next(item['residual'] for item in after['details'] if item['line'] == invoice), 100)
        self.assertEqual(after['groups'][0]['residual'], 60)
        self.assertEqual(after['groups'][0]['aging'][5], -40)

    def test_missing_due_date_and_partner_filter(self):
        line = self._line(100, date='2026-09-01', due=False)
        other = self._line(250, posted=False)
        other.partner_id = self.partner_b
        other.move_id.action_post()
        data = self._wizard()._statement_data()
        self.assertEqual([item['line'].id for item in data['details']], [line.id])
        self.assertEqual(data['groups'][0]['aging'][1], 100)

    def test_current_company_isolation(self):
        line = self._line(100)
        other = self.env['res.company'].create({'name': 'Other ledger company'})
        account = self.env['account.account'].create({
            'name': 'Other AR', 'code': '9910', 'account_type': 'asset_receivable',
            'reconcile': True, 'company_ids': [Command.set(other.ids)],
        })
        offset = self.env['account.account'].create({
            'name': 'Other equity', 'code': '9920', 'account_type': 'equity',
            'company_ids': [Command.set(other.ids)],
        })
        journal = self.env['account.journal'].create({'name': 'Other journal', 'code': 'OTH', 'type': 'general', 'company_id': other.id})
        move = self.env['account.move'].with_company(other).create({
            'journal_id': journal.id, 'date': '2026-09-01',
            'line_ids': [Command.create({'account_id': account.id, 'partner_id': self.partner_a.id, 'balance': 900}),
                         Command.create({'account_id': offset.id, 'balance': -900})],
        })
        move.action_post()
        self.manager.company_ids = [Command.link(other.id)]
        wizard = self._wizard().with_context(allowed_company_ids=[self.env.company.id, other.id])
        self.assertEqual([item['line'].id for item in wizard._statement_data()['details']], [line.id])

    def test_limit_rejects_incomplete_report(self):
        from unittest.mock import patch
        self._line(100)
        self._line(100)
        with patch('odoo.addons.qing_account.wizard.partner_statement.MAX_STATEMENT_LINES', 1):
            with self.assertRaises(UserError):
                self._wizard().action_generate()

    def test_snapshot_survives_reconciliation_and_renaming(self):
        invoice = self._line(1000)
        payment = self._line(-300, '2026-09-15')
        wizard = self._wizard()
        action = wizard.action_save_snapshot()
        first = self.env[action['res_model']].with_user(self.manager).browse(action['res_id'])
        original = first.read(['result', 'result_file', 'partner_scope', 'captured_by_name'])[0]
        self.assertEqual(first.captured_by, self.manager)
        self.assertIn('固定快照', first.result)
        self.assertTrue(first.result_file)
        (invoice | payment).reconcile()
        self.partner_a.name = 'Renamed after snapshot'
        self.manager.name = 'Renamed manager'
        wizard.unlink()
        first.invalidate_recordset()
        self.assertEqual(first.read(['result', 'result_file', 'partner_scope', 'captured_by_name'])[0], original)
        second_action = self._wizard().action_save_snapshot()
        second = first.browse(second_action['res_id'])
        self.assertNotEqual(first, second)
        self.assertNotEqual(first.result_file, second.result_file)
        self.assertNotEqual(first.partner_scope, second.partner_scope)
        self.assertEqual(invoice.amount_residual, 700)

    def test_snapshot_immutable_and_not_forgeable(self):
        snapshot = self.env['qing.statement.snapshot'].with_user(self.manager).browse(
            self._wizard().action_save_snapshot()['res_id'])
        for operation in [lambda: snapshot.write({'result': 'forged'}),
                          snapshot.unlink, snapshot.copy,
                          lambda: snapshot.create({'name': 'forged'})]:
            with self.assertRaises(AccessError):
                operation()
        with self.assertRaises(AccessError):
            snapshot.with_user(self.billing).read(['result_file'])
        with self.assertRaises(AccessError):
            self._wizard().with_user(self.billing).action_save_snapshot()

    def test_snapshot_company_isolation(self):
        snapshot = self.env['qing.statement.snapshot'].with_user(self.manager).browse(
            self._wizard().action_save_snapshot()['res_id'])
        other = self.env['res.company'].create({'name': 'Snapshot other company'})
        self.manager.company_ids = [Command.link(other.id)]
        with self.assertRaises(AccessError):
            snapshot.with_context(allowed_company_ids=other.ids).read(['result_file'])
        self.assertFalse(snapshot.with_context(allowed_company_ids=other.ids).search([('id', '=', snapshot.id)]))
