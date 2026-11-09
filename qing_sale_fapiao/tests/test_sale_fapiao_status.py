from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged
from odoo.tools.binary import BinaryBytes


@tagged('post_install', '-at_install')
class TestSaleFapiaoStatus(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(
            cls.env, login='sale_fapiao_manager', groups='account.group_account_manager',
            company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.company.ids)])
        cls.salesperson = new_test_user(
            cls.env, login='sale_fapiao_salesperson', groups='sales_team.group_sale_salesman',
            company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.company.ids)])

    def _order(self):
        return self.env['sale.order'].sudo().create({
            'partner_id': self.partner_a.id,
            'user_id': self.salesperson.id,
            'order_line': [Command.create({
                'name': 'Synthetic goods', 'product_uom_qty': 1, 'price_unit': 100,
            })],
        })

    def _invoice(self, order, extra_order=None):
        sale_lines = order.order_line | (extra_order.order_line if extra_order else order.order_line.browse())
        move = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner_a.id,
            'invoice_date': '2026-09-23',
            'invoice_line_ids': [Command.create({
                'name': 'Synthetic goods', 'quantity': 1, 'price_unit': 100,
                'account_id': self.company_data['default_account_revenue'].id,
                'tax_ids': [Command.clear()],
                'sale_line_ids': [Command.set(sale_lines.ids)],
            })],
        })
        move.action_post()
        return move

    def _ticket(self, number, amount, move=None, **values):
        vals = {
            'number': number, 'direction': 'out', 'partner_id': self.partner_a.id,
            'buyer_name': 'Synthetic buyer', 'seller_name': 'Synthetic seller',
            'amount_untaxed': amount, 'amount_tax': 0,
            'source_filename': 'synthetic.txt',
            'source_file': BinaryBytes(b'Synthetic fixture; not a real tax invoice.'),
            'move_ids': [Command.set(move.ids)] if move else [Command.clear()],
            **values,
        }
        return self.env['qing.fapiao'].with_user(self.manager).create(vals)

    def _allocate(self, ticket, move, amount):
        allocation = self.env['qing.fapiao.allocation'].with_user(self.manager).create({
            'fapiao_id': ticket.id, 'move_id': move.id,
            'amount_untaxed': amount, 'amount_tax': 0,
        })
        allocation.action_confirm()
        return allocation

    def _states(self, order):
        self.env.invalidate_all()
        return (order.qing_fapiao_issuance_state,
                order.qing_fapiao_reversal_state,
                order.qing_fapiao_pending)

    def test_blue_and_red_progress_are_independent(self):
        order = self._order()
        self.assertEqual(self._states(order), ('no_invoice', 'none', False))
        move = self._invoice(order)
        self.assertEqual(self._states(order), ('not_issued', 'none', False))
        blue = self._ticket('SO-BLUE-1', 40, move)
        self.assertEqual(self._states(order), ('pending', 'none', True))
        blue.action_register()
        self.assertEqual(self._states(order), ('review', 'none', False))
        self._allocate(blue, move, 40)
        self.assertEqual(self._states(order), ('partial', 'none', False))
        second = self._ticket('SO-BLUE-2', 60, move)
        second.action_register()
        self._allocate(second, move, 60)
        self.assertEqual(self._states(order), ('issued', 'none', False))
        red = self._ticket('SO-RED-1', 20, color='red', original_id=blue.id,
                           original_number=blue.number)
        self.assertEqual(self._states(order), ('issued', 'pending', True))
        red.action_register()
        self.assertEqual(self._states(order), ('issued', 'partial', False))
        red2 = self._ticket('SO-RED-2', 20, color='red', original_id=blue.id,
                            original_number=blue.number)
        red2.action_register()
        red3 = self._ticket('SO-RED-3', 60, color='red', original_id=second.id,
                            original_number=second.number)
        red3.action_register()
        self.assertEqual(self._states(order), ('issued', 'reversed', False))

    def test_cancelled_registration_and_partial_blue_with_red(self):
        order = self._order()
        move = self._invoice(order)
        blue = self._ticket('SO-CANCEL-BLUE', 40, move)
        blue.action_register()
        self._allocate(blue, move, 40)
        red = self._ticket('SO-CANCEL-RED', 20, color='red', original_id=blue.id,
                           original_number=blue.number)
        red.action_register()
        self.assertEqual(self._states(order), ('partial', 'partial', False))
        red.cancel_reason = 'Synthetic correction'
        red.action_cancel_registration()
        self.assertEqual(self._states(order), ('partial', 'none', False))
        blue.cancel_reason = 'Synthetic correction'
        blue.action_cancel_registration()
        self.assertEqual(self._states(order), ('not_issued', 'none', False))

    def test_mixed_source_invoice_requires_review(self):
        first, second = self._order(), self._order()
        self._invoice(first, extra_order=second)
        self.assertEqual(self._states(first)[0], 'review')
        self.assertEqual(self._states(second)[0], 'review')

    def test_submitted_request_is_pending_not_issued(self):
        order = self._order()
        move = self._invoice(order)
        action = move.with_user(self.manager).action_create_fapiao_request()
        request = self.env['qing.fapiao.request'].with_user(self.manager).browse(action['res_id'])
        request.seller_tax_id = 'SYNTHETIC-SELLER'
        request.line_ids.write({'tax_code': '3040201010000000000'})
        request.action_submit()
        self.assertEqual(self._states(order), ('pending', 'none', True))

    def test_shared_blue_red_requires_review(self):
        first, second = self._order(), self._order()
        first_move, second_move = self._invoice(first), self._invoice(second)
        blue = self._ticket('SO-SHARED-BLUE', 100,
                            move=first_move | second_move)
        blue.action_register()
        self._allocate(blue, first_move, 50)
        self._allocate(blue, second_move, 50)
        self.assertEqual(self._states(first)[:2], ('partial', 'none'))
        red = self._ticket('SO-SHARED-RED', 20, color='red', original_id=blue.id,
                           original_number=blue.number)
        red.action_register()
        self.assertEqual(self._states(first)[1], 'review')
        self.assertEqual(self._states(second)[1], 'review')

    def test_sales_user_sees_summary_without_ledger_access(self):
        order = self._order()
        move = self._invoice(order)
        blue = self._ticket('SO-ACL-BLUE', 100, move)
        blue.action_register()
        self._allocate(blue, move, 100)
        self.env.invalidate_all()
        self.assertEqual(order.with_user(self.salesperson).qing_fapiao_issuance_state, 'issued')
        with self.assertRaises(AccessError):
            blue.with_user(self.salesperson).read(['number'])
