from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged


@tagged('post_install', '-at_install')
class TestClearingConfiguration(AccountTestInvoicingCommon):
    def test_valid_missing_and_direct_bank(self):
        journal = self.company_data['default_journal_bank']
        self.assertFalse(journal._qing_clearing_issues())
        method = journal.outbound_payment_method_line_ids[:1]
        method.payment_account_id = False
        self.assertTrue(any('未显式设置' in issue for issue in journal._qing_clearing_issues('outbound')))
        payment = self.env['account.payment'].create({
            'journal_id': journal.id, 'payment_method_line_id': method.id,
            'partner_id': self.partner_a.id, 'partner_type': 'supplier',
            'payment_type': 'outbound', 'amount': 100,
        })
        payment.action_post()
        self.assertEqual(payment.move_id.state, 'posted')
        line = self.env['account.bank.statement.line'].create({
            'journal_id': journal.id, 'amount': -101, 'payment_ref': 'Config test',
        })
        self.assertIn('银行清算配置', line.qing_workbench_candidates()['notice'])
        old_move = payment.move_id
        method.payment_account_id = journal.default_account_id
        self.assertTrue(any('独立' in issue for issue in journal._qing_clearing_issues('outbound')))
        self.assertEqual(payment.move_id, old_move)  # Configuration change must not rebook old payments.

    def test_non_reconcilable_and_access(self):
        journal = self.company_data['default_journal_bank']
        journal.outbound_payment_method_line_ids[:1].payment_account_id.reconcile = False
        self.assertTrue(any('允许核销' in issue for issue in journal._qing_clearing_issues()))
        user = new_test_user(self.env, login='clearing_config_billing', groups='account.group_account_invoice')
        with self.assertRaises(AccessError):
            journal.with_user(user).action_qing_check_clearing()
        other = self.env['res.company'].create({'name': 'Other config company'})
        with self.assertRaises(AccessError):
            journal.with_company(other).action_qing_check_clearing()

    def test_manager_view_exposes_both_accounts(self):
        from lxml import etree
        user = new_test_user(self.env, login='clearing_config_manager', groups='account.group_account_manager')
        view = self.env['account.journal'].with_user(user).get_view(
            self.env.ref('account.view_account_journal_form').id, 'form')
        root = etree.fromstring(view['arch'])
        for field in ['inbound_payment_method_line_ids', 'outbound_payment_method_line_ids']:
            self.assertTrue(root.xpath(".//field[@name='%s']//field[@name='payment_account_id']" % field))
