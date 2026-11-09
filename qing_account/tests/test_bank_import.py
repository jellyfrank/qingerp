from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, new_test_user, tagged
from odoo.tools.binary import BinaryBytes


@tagged('post_install', '-at_install')
class TestBankImport(AccountTestInvoicingCommon):
    def _wizard(self, data='2026-09-01,-300,USD,Payment,REF1,Vendor,123\n'):
        journal = self.company_data['default_journal_bank']
        if not journal.bank_account_id:
            journal.bank_account_id = self.env['res.partner.bank'].create({
                'account_number': 'TEST123456', 'partner_id': self.env.company.partner_id.id,
                'company_id': self.env.company.id,
            })
        currency = journal.currency_id or journal.company_id.currency_id
        data = data.replace('USD', currency.name)
        return self.env['qing.bank.import'].create({
            'journal_id': journal.id, 'bank_account': journal.bank_account_id.account_number,
            'profile_id': self.env.ref('qing_account.bank_profile_standard').id,
            'filename': 'synthetic.csv',
            'file': BinaryBytes(('date,amount,currency,memo,transaction_id,partner_name,account_number\n' + data).encode()),
        })

    def test_delimiter_uses_english_option_keys(self):
        profile = self.env.ref('qing_account.bank_profile_standard')
        selection = profile._fields['delimiter'].selection
        self.assertEqual([value for value, _label in selection], ['comma', 'semicolon', 'tab'])
        for value, character in [('comma', ','), ('semicolon', ';'), ('tab', '\t')]:
            profile.delimiter = value
            self.assertEqual(profile._delimiter_character(), character)

    def test_preview_import_and_duplicate(self):
        wizard = self._wizard()
        count = self.env['account.bank.statement.line'].search_count([])
        wizard.action_preview()
        self.assertEqual(self.env['account.bank.statement.line'].search_count([]), count)
        action = wizard.action_import()
        line = self.env['account.bank.statement.line'].search(action['domain'])
        self.assertEqual(line.amount, -300)
        self.assertFalse(line.is_reconciled)
        self.assertTrue(line.line_ids.filtered(lambda l: l.account_id == wizard.journal_id.suspense_account_id))
        second = self._wizard()
        second.action_preview()
        self.assertIn('跳过', second.preview)
        self.assertEqual(second.action_import()['domain'], [('id', 'in', [])])

    def test_conflicting_reference(self):
        first = self._wizard()
        first.action_preview()
        first.action_import()
        second = self._wizard('2026-09-01,-400,USD,Payment,REF1,Vendor,123\n')
        with self.assertRaises(UserError):
            second.action_preview()

    def test_suspected_requires_explicit_confirmation(self):
        wizard = self._wizard('2026-09-01,-300,USD,Payment,,Vendor,123\n' * 2)
        wizard.action_preview()
        with self.assertRaises(UserError):
            wizard.action_import()
        wizard.allow_suspected = True
        action = wizard.action_import()
        self.assertEqual(len(action['domain'][0][2]), 2)

    def test_changed_file_requires_preview(self):
        wizard = self._wizard()
        wizard.action_preview()
        wizard.file = BinaryBytes(wizard.file.content.replace(b'-300', b'-400'))
        with self.assertRaises(UserError):
            wizard.action_import()

    def test_invalid_data(self):
        for row in ['bad,-300,USD,Payment,R,V,1', '2026-09-01,NaN,USD,Payment,R,V,1',
                    '2026-09-01,0,USD,Payment,R,V,1', '2026-09-01,1.001,USD,Payment,R,V,1',
                    '2026-09-01,300,ZZZ,Payment,R,V,1']:
            with self.subTest(row=row), self.assertRaises(UserError):
                self._wizard(row).action_preview()

    def test_wrong_bank_account(self):
        wizard = self._wizard()
        wizard.bank_account = 'WRONG'
        with self.assertRaises(UserError):
            wizard.action_preview()

    def test_split_mapping_and_form(self):
        wizard = self._wizard()
        profile = wizard.profile_id.copy({'name': 'Synthetic split', 'amount_mode': 'split'})
        wizard.profile_id = profile
        wizard.file = BinaryBytes(b'date,income,expense,currency,memo,transaction_id,partner_name,account_number\n2026-09-01,0,300,,Test,SPLIT,,\n')
        wizard.action_preview()
        line = self.env['account.bank.statement.line'].search(wizard.action_import()['domain'])
        self.assertEqual(line.amount, -300)
        with Form(wizard, view='qing_account.view_bank_import_form') as form:
            self.assertEqual(form.bank_account, wizard.bank_account)

    def test_match_existing_payment_and_undo(self):
        wizard = self._wizard()
        wizard.action_preview()
        line = self.env['account.bank.statement.line'].search(wizard.action_import()['domain'])
        payment = self.env['account.payment'].create({
            'partner_id': self.partner_a.id, 'partner_type': 'supplier', 'payment_type': 'outbound',
            'amount': 300, 'date': '2026-09-01', 'journal_id': wizard.journal_id.id,
        })
        payment.action_post()
        match = self.env['qing.bank.payment.match'].create({'line_id': line.id, 'payment_id': payment.id})
        match.action_match()
        self.assertTrue(line.is_reconciled)
        self.assertEqual(line.qing_matched_payment_id, payment)
        line.action_qing_undo_match()
        self.assertFalse(line.is_reconciled)
        self.assertFalse(line.qing_matched_payment_id)
        self.assertEqual(payment.move_id.state, 'posted')

    def test_match_wrong_amount(self):
        wizard = self._wizard()
        wizard.action_preview()
        line = self.env['account.bank.statement.line'].search(wizard.action_import()['domain'])
        payment = self.env['account.payment'].create({
            'partner_id': self.partner_a.id, 'partner_type': 'supplier', 'payment_type': 'outbound',
            'amount': 400, 'date': '2026-09-01', 'journal_id': wizard.journal_id.id,
        })
        payment.action_post()
        match = self.env['qing.bank.payment.match'].create({'line_id': line.id, 'payment_id': payment.id})
        with self.assertRaises(UserError):
            match.action_match()
        self.assertFalse(line.is_reconciled)

    def test_other_company_rejected(self):
        wizard = self._wizard()
        other = self.env['res.company'].create({'name': 'Synthetic other company'})
        with self.assertRaises(UserError):
            wizard.with_company(other).action_preview()

    def test_billing_user_cannot_import(self):
        wizard = self._wizard()
        user = new_test_user(self.env, login='bank_billing', groups='account.group_account_invoice')
        with self.assertRaises(AccessError):
            wizard.with_user(user).action_preview()
        with self.assertRaises(AccessError):
            wizard.with_user(user).action_import()

    def test_gb18030_and_html_escaping(self):
        wizard = self._wizard()
        wizard.profile_id = wizard.profile_id.copy({'name': 'GB test', 'encoding': 'gb18030'})
        wizard.file = BinaryBytes(wizard.file.content.decode().replace('Payment', '<script>收款</script>').encode('gb18030'))
        wizard.action_preview()
        self.assertNotIn('<script>', wizard.preview)
        self.assertIn('&lt;script&gt;', wizard.preview)

    def test_duplicate_ids_in_file_rejected(self):
        wizard = self._wizard('2026-09-01,-300,USD,Payment,REF1,Vendor,123\n' * 2)
        with self.assertRaises(UserError):
            wizard.action_preview()
