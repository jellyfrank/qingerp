from lxml import etree

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSaleKanban(TransactionCase):
    def test_order_cards_lead_with_number_and_state(self):
        # The base card and the views that only change its js_class share it.
        for xmlid in ('sale.view_sale_order_kanban', 'sale.view_quotation_kanban_with_onboarding',
                      'sale.sale_order_kanban_with_dashboard'):
            view = self.env.ref(xmlid)
            arch = etree.fromstring(self.env['sale.order'].get_views([(view.id, 'kanban')])['views']['kanban']['arch'])
            card = arch.find(".//t[@t-name='card']")
            names = [field.get('name') for field in card.iter('field')]
            self.assertEqual(names[:3], ['name', 'state', 'partner_id'], xmlid)
            self.assertIn('amount_total', names, xmlid)
            self.assertIsNotNone(card.find(".//span[@aria-hidden='true']"), xmlid)
