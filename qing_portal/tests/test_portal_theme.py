from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestQingPortalTheme(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env['res.partner']
        cls.customer = Partner.create({'name': 'Qing Portal Customer', 'is_company': True})
        cls.contact = Partner.create({'name': 'Portal Contact', 'parent_id': cls.customer.id, 'email': 'contact@example.com'})
        cls.portal_user = new_test_user(cls.env, login='qing_portal_user', password='qing_portal_user', name='Portal Contact',
                                        groups='base.group_portal', partner_id=cls.contact.id)

    def _website_installed(self):
        return bool(self.env['ir.module.module'].search_count([('name', '=', 'website'), ('state', '=', 'installed')]))

    def _make_quote(self, validity_days=8):
        product = self.env['product.product'].create({'name': 'Qing Portal Flange', 'list_price': 100.0})
        order = self.env['sale.order'].create({
            'partner_id': self.customer.id,
            'validity_date': fields.Date.today() + timedelta(days=validity_days),
            'require_signature': True,
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 3, 'price_unit': 100.0})],
        })
        order.state = 'sent'
        return order

    def _make_invoice(self, due_days):
        move = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.customer.id,
            'invoice_date': fields.Date.today() - timedelta(days=30),
            'invoice_date_due': fields.Date.today() + timedelta(days=due_days),
            'invoice_line_ids': [(0, 0, {'name': 'Qing Portal Service', 'quantity': 1, 'price_unit': 250.0})],
        })
        move.invoice_payment_term_id = False
        move.invoice_date_due = fields.Date.today() + timedelta(days=due_days)
        move.action_post()
        return move

    def test_account_home_is_themed(self):
        self.authenticate('qing_portal_user', 'qing_portal_user')
        page = self.url_open('/my/home').text
        self.assertIn('o_qing_portal', page)
        self.assertIn('o_qing_portal_home', page)
        self.assertIn('o_qing_portal_greeting', page)
        self.assertIn('Portal Contact', page)
        self.assertIn('Qing Portal Customer', page)

    def test_home_values_put_overdue_first(self):
        if 'sale.order' not in self.env or 'account.move' not in self.env:
            self.skipTest('Needs Sales and Invoicing.')
        quote = self._make_quote()
        overdue = self._make_invoice(-4)
        later = self._make_invoice(20)
        values = self.contact.with_user(self.portal_user)._qing_portal_home_values()
        names = [item['name'] for item in values['todo']]
        self.assertEqual(names[:3], [overdue.name, quote.name, later.name])
        self.assertTrue(values['todo'][0]['overdue'])
        self.assertEqual(values['todo'][0]['days'], 4)
        self.assertEqual(values['todo'][1]['days'], 8)
        self.assertEqual(values['overdue_count'], 1)
        self.assertAlmostEqual(values['unpaid_total'], overdue.amount_residual + later.amount_residual)
        self.assertIn(quote.name, [item['name'] for item in values['recent']])

    def test_home_shows_waiting_cards(self):
        if 'sale.order' not in self.env:
            self.skipTest('Needs Sales.')
        quote = self._make_quote()
        self.authenticate('qing_portal_user', 'qing_portal_user')
        page = self.url_open('/my/home').text
        self.assertIn('o_qing_portal_todo_card', page)
        self.assertIn(quote.name, page)
        self.assertIn(quote.get_portal_url(), page)

    def test_other_customers_documents_stay_hidden(self):
        if 'sale.order' not in self.env:
            self.skipTest('Needs Sales.')
        self._make_quote()
        other = self.env['res.partner'].create({'name': 'Other Portal Customer'})
        other_user = new_test_user(self.env, login='qing_portal_other', password='qing_portal_other',
                                   groups='base.group_portal', partner_id=other.id)
        values = other.with_user(other_user)._qing_portal_home_values()
        self.assertFalse(values['todo'])
        self.assertFalse(values['recent'])

    def test_default_company_colour_uses_theme_accent(self):
        self.env.company.email_secondary_color = '#875A7B'
        self.authenticate('qing_portal_user', 'qing_portal_user')
        page = self.url_open('/my/home').text
        self.assertIn('--qp-accent: var(--qp-accent-default)', page)
        self.env.company.email_secondary_color = '#123456'
        page = self.url_open('/my/home').text
        self.assertNotIn('--qp-accent: var(--qp-accent-default)', page)
        if self._website_installed():
            # With the Website app the portal content still follows the
            # company colour rather than the website palette.
            self.assertIn('--qp-accent: #123456', page)

    def test_header_links_to_documents(self):
        if self._website_installed():
            self.skipTest('With the Website app the website keeps its own header.')
        self.authenticate('qing_portal_user', 'qing_portal_user')
        page = self.url_open('/my/home').text
        self.assertIn('o_qing_portal_nav', page)
        if 'account.move' in self.env:
            self.assertIn('href="/my/invoices"', page)

    def test_record_sidebar_signed_by_qingerp(self):
        arch = self.env.ref('portal.portal_record_sidebar').get_combined_arch()
        self.assertIn('qingsolution.com', arch)
        self.assertNotIn('www.odoo.com', arch)

    def test_login_page_not_themed(self):
        page = self.url_open('/web/login').text
        self.assertNotIn('o_qing_portal', page)

    def test_frontend_translations_include_this_module(self):
        if not self._website_installed():
            self.skipTest('Checked through the website translation route.')
        self.env['res.lang']._activate_lang('zh_CN')
        data = self.url_open('/website/translations?lang=zh_CN').json()
        self.assertIn('qing_portal', data['modules'])

    def test_native_portal_terms_get_chinese(self):
        if 'sale.order' not in self.env:
            self.skipTest('Needs Sales.')
        self.env['res.lang']._activate_lang('zh_CN')
        self.env['ir.module.module']._load_module_terms(['qing_portal'], ['zh_CN'])
        arch = self.env.ref('sale.sale_order_portal_template').with_context(lang='zh_CN').arch_db
        self.assertNotIn('Reject this quotation', arch)
