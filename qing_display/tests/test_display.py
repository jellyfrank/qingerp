from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestQingDisplay(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env['res.company'].create({'name': 'Display test company'})
        cls.other_company = cls.env['res.company'].create({'name': 'Other display company'})
        cls.viewer = cls.env['res.users'].sudo().create({
            'name': 'Display viewer',
            'login': 'qing-display-viewer',
            'company_id': cls.company.id,
            'company_ids': [Command.set(cls.company.ids)],
            'group_ids': [Command.set([cls.env.ref('qing_display.group_display_viewer').id])],
        })
        cls.outsider = cls.env['res.users'].sudo().create({
            'name': 'Display outsider',
            'login': 'qing-display-outsider',
            'company_id': cls.company.id,
            'company_ids': [Command.set(cls.company.ids)],
            'group_ids': [Command.set([cls.env.ref('base.group_user').id])],
        })
        cls.partner = cls.env['res.partner'].create({'name': 'Display customer'})
        cls.product = cls.env['product.product'].create({'name': 'Display product'})

    def _order(self, company=None, price=100):
        company = company or self.company
        return self.env['sale.order'].with_company(company).create({
            'company_id': company.id,
            'partner_id': self.partner.id,
            'order_line': [Command.create({
                'product_id': self.product.id,
                'product_uom_qty': 1,
                'price_unit': price,
            })],
        })

    def test_confirmation_creates_one_event_and_first_screen_skips_history(self):
        company_model = self.env['res.company'].with_user(self.viewer)
        first = self._order()
        first.action_confirm()
        Event = self.env['qing.display.event']
        event = Event.search([('order_id', '=', first.id)])
        self.assertEqual(len(event), 1)
        self.assertEqual(event.amount_company, first.amount_total)

        initial = company_model.qing_display_snapshot(self.company.id, None)
        self.assertEqual(initial['events'], [])
        self.assertEqual(initial['cursor'], event.id)
        self.assertEqual(initial['today_count'], 1)
        self.assertGreater(initial['today_amount'], 0)
        self.assertEqual(initial['recent_orders'][0]['name'], first.name)

        second = self._order(price=250)
        second.action_confirm()
        update = company_model.qing_display_snapshot(self.company.id, initial['cursor'])
        self.assertEqual([item['name'] for item in update['events']], [second.name])
        self.assertEqual(update['today_count'], 2)

    def test_empty_initial_cursor_receives_first_order(self):
        company_model = self.env['res.company'].with_user(self.viewer)
        initial = company_model.qing_display_snapshot(self.company.id, None)
        self.assertEqual(initial['cursor'], 0)
        order = self._order()
        order.action_confirm()
        update = company_model.qing_display_snapshot(self.company.id, initial['cursor'])
        self.assertEqual([item['name'] for item in update['events']], [order.name])

    def test_draft_no_event_and_company_access(self):
        draft = self._order()
        Event = self.env['qing.display.event']
        self.assertFalse(Event.search([('order_id', '=', draft.id)]))
        other_order = self._order(company=self.other_company)
        other_order.action_confirm()
        company_model = self.env['res.company'].with_user(self.viewer)
        own = company_model.qing_display_snapshot(self.company.id, 0)
        self.assertEqual(own['events'], [])
        with self.assertRaises(AccessError):
            company_model.qing_display_snapshot(self.other_company.id, 0)
        with self.assertRaises(AccessError):
            self.env['res.company'].with_user(self.outsider).qing_display_snapshot(
                self.company.id, 0,
            )
        self.assertFalse(Event.with_user(self.viewer).search([
            ('order_id', '=', other_order.id),
        ]))

    def test_old_events_are_pruned(self):
        order = self._order()
        order.action_confirm()
        Event = self.env['qing.display.event']
        event = Event.search([('order_id', '=', order.id)])
        event.sudo().write({'occurred_at': fields.Datetime.now() - timedelta(days=31)})
        Event._cron_prune()
        self.assertFalse(event.exists())

    def test_reconfirmation_does_not_repeat_event(self):
        order = self._order()
        order.action_confirm()
        order.action_cancel()
        order.action_draft()
        order.action_confirm()
        Event = self.env['qing.display.event']
        self.assertEqual(Event.search_count([('order_id', '=', order.id)]), 1)
