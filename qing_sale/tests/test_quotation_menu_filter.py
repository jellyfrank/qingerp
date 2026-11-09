import ast

from odoo import Command
from odoo.tests import TransactionCase, tagged

from ..hooks import uninstall_hook
from ..models.res_config_settings import SETTING_KEY


@tagged('post_install', '-at_install')
class TestQuotationMenuFilter(TransactionCase):

    def _menu_domain(self, menu_xmlid):
        menu = self.env.ref(menu_xmlid)
        return ast.literal_eval(menu.action.run()['domain'] or '[]')

    def _reset_to_default(self):
        self.env['ir.config_parameter'].sudo().search([('key', '=', SETTING_KEY)]).unlink()

    def test_default_setting_separates_menu_records(self):
        self._reset_to_default()
        Settings = self.env['res.config.settings'].sudo()
        self.assertTrue(Settings.create({}).qing_show_only_quotations)
        self.assertEqual(
            self.env.ref('sale.menu_sale_quotations').action,
            self.env.ref('qing_sale.action_sale_quotations_menu'),
        )
        self.assertEqual(
            self.env.ref('sale.menu_sale_order').action,
            self.env.ref('qing_sale.action_sale_orders_menu'),
        )
        quotation_domain = self._menu_domain('sale.menu_sale_quotations')
        order_domain = self._menu_domain('sale.menu_sale_order')
        self.assertEqual(quotation_domain, [('state', 'in', ('draft', 'sent'))])
        self.assertEqual(order_domain, [('state', '=', 'sale')])
        self.assertFalse(self.env.ref('sale.action_quotations_with_onboarding').domain)
        self.assertFalse(self.env.ref('sale.action_quotations').domain)
        self.assertFalse(self.env.ref('sale.action_orders').domain)
        SaleOrder = self.env['sale.order'].sudo()
        partner = self.env['res.partner'].sudo().create({'name': 'Synthetic quotation menu customer'})
        orders = {
            state: SaleOrder.create({'partner_id': partner.id, 'state': state})
            for state in ('draft', 'sent', 'sale', 'cancel')
        }
        quotation_ids = set(SaleOrder.search(quotation_domain).ids)
        sale_ids = set(SaleOrder.search(order_domain).ids)
        self.assertTrue({orders['draft'].id, orders['sent'].id} <= quotation_ids)
        self.assertFalse({orders['sale'].id, orders['cancel'].id} & quotation_ids)
        self.assertIn(orders['sale'].id, sale_ids)
        self.assertFalse({orders['draft'].id, orders['sent'].id, orders['cancel'].id} & sale_ids)

    def test_switch_restores_native_menu_behavior_and_saved_choice(self):
        Settings = self.env['res.config.settings'].sudo()
        Settings.create({'qing_show_only_quotations': False}).set_values()
        self.assertFalse(self.env['ir.config_parameter'].sudo().get_bool(SETTING_KEY, default=True))
        self.assertFalse(Settings.create({}).qing_show_only_quotations)
        for menu_xmlid in ('sale.menu_sale_quotations', 'sale.menu_sale_order'):
            self.assertEqual(self._menu_domain(menu_xmlid), [])
        Settings.create({'qing_show_only_quotations': True}).set_values()
        self.assertTrue(Settings.create({}).qing_show_only_quotations)
        self.assertEqual(
            self._menu_domain('sale.menu_sale_quotations'), [('state', 'in', ('draft', 'sent'))]
        )
        self.assertEqual(self._menu_domain('sale.menu_sale_order'), [('state', '=', 'sale')])

    def test_salesperson_can_open_both_menus(self):
        self._reset_to_default()
        user = self.env['res.users'].sudo().create({
            'name': 'Quotation menu salesperson',
            'login': 'quotation-menu-salesperson',
            'group_ids': [Command.set([
                self.env.ref('base.group_user').id,
                self.env.ref('sales_team.group_sale_salesman').id,
            ])],
        })
        for menu_xmlid, expected_domain in (
            ('sale.menu_sale_quotations', [('state', 'in', ('draft', 'sent'))]),
            ('sale.menu_sale_order', [('state', '=', 'sale')]),
        ):
            action = self.env.ref(menu_xmlid).action.with_user(user).run()
            self.assertEqual(ast.literal_eval(action['domain']), expected_domain)

    def test_settings_view_and_uninstall_restore(self):
        self._reset_to_default()
        view = self.env.ref('qing_sale.view_res_config_settings_form').sudo()
        self.assertIn(b'qing_show_only_quotations', view.get_combined_arch().encode())
        uninstall_hook(self.env)
        self.assertEqual(
            self.env.ref('sale.menu_sale_quotations').action,
            self.env.ref('sale.action_quotations_with_onboarding'),
        )
        self.assertEqual(
            self.env.ref('sale.menu_sale_order').action,
            self.env.ref('sale.action_orders'),
        )
        self.assertFalse(self.env['ir.config_parameter'].sudo().search([('key', '=', SETTING_KEY)]))
