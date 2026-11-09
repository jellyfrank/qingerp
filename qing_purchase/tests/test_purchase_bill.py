from lxml import etree

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged
from odoo.tools.safe_eval import safe_eval


@tagged('post_install', '-at_install')
class TestPurchaseBill(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.purchase_tax = cls.env['account.tax'].create({
            'name': 'Qing Purchase 13%',
            'amount': 13,
            'amount_type': 'percent',
            'type_tax_use': 'purchase',
            'price_include_override': 'tax_excluded',
        })
        cls.ordered_service = cls._create_product(
            name='Service billed on ordered quantity',
            type='service',
            purchase_method='purchase',
            supplier_taxes_id=cls.purchase_tax,
        )
        cls.received_service = cls.ordered_service.copy({
            'name': 'Service billed on received quantity',
            'purchase_method': 'receive',
        })
        cls.purchase_user = new_test_user(
            cls.env,
            login='qing_purchase_user',
            groups='purchase.group_purchase_user,base.group_partner_manager',
            company_id=cls.env.company.id,
        )
        cls.internal_user = new_test_user(
            cls.env,
            login='qing_purchase_internal_user',
            company_id=cls.env.company.id,
        )

    def _create_order(self, product=None, confirm=True):
        product = product or self.ordered_service
        order = self.env['purchase.order'].create({
            'partner_id': self.partner_a.id,
            'order_line': [Command.create({
                'name': product.name,
                'product_id': product.id,
                'product_qty': 10,
                'uom_id': product.uom_id.id,
                'price_unit': 100,
                'tax_ids': [Command.set(self.purchase_tax.ids)],
            })],
        })
        if confirm:
            order.button_confirm()
        return order

    def _assert_no_bill_created(self, order, exception=UserError):
        existing_bills = order.with_env(self.env).invoice_ids
        with self.assertRaises(exception):
            order.action_qing_create_bill()
        self.assertEqual(order.with_env(self.env).invoice_ids, existing_bills)

    def test_ordered_quantity_creates_draft_bill_without_attachment(self):
        order = self._create_order()
        self.assertEqual(order.order_line.qty_received, 0)
        action = order.action_qing_create_bill()
        bill = order.invoice_ids

        self.assertEqual(len(bill), 1)
        self.assertRecordValues(bill, [{
            'state': 'draft',
            'move_type': 'in_invoice',
            'partner_id': self.partner_a.id,
            'company_id': order.company_id.id,
            'currency_id': order.currency_id.id,
            'invoice_origin': order.name,
            'amount_untaxed': 1000,
            'amount_tax': 130,
            'amount_total': 1130,
        }])
        self.assertRecordValues(bill.invoice_line_ids, [{
            'product_id': self.ordered_service.id,
            'purchase_line_id': order.order_line.id,
            'quantity': 10,
            'price_unit': 100,
            'tax_ids': self.purchase_tax.ids,
        }])
        self.assertEqual(action['res_model'], 'account.move')
        self.assertEqual(action['res_id'], bill.id)
        attachment_domain = [('res_model', '=', 'account.move'), ('res_id', '=', bill.id)]
        self.assertFalse(self.env['ir.attachment'].search_count(attachment_domain))
        self.assertEqual(order.invoice_status, 'invoiced')

    def test_received_quantity_requires_receipt(self):
        order = self._create_order(self.received_service)
        self.assertEqual(order.invoice_status, 'no')
        self._assert_no_bill_created(order)

        order.order_line.qty_received_manual = 4
        order.action_qing_create_bill()
        self.assertEqual(order.invoice_ids.invoice_line_ids.quantity, 4)
        self.assertEqual(order.invoice_ids.amount_total, 452)

    def test_later_receipt_creates_only_remaining_bill_quantity(self):
        order = self._create_order(self.received_service)
        order.order_line.qty_received_manual = 4
        order.action_qing_create_bill()
        first_bill = order.invoice_ids
        self._assert_no_bill_created(order)

        order.order_line.qty_received_manual = 10
        order.action_qing_create_bill()
        second_bill = order.invoice_ids - first_bill
        self.assertEqual(first_bill.invoice_line_ids.quantity, 4)
        self.assertEqual(second_bill.invoice_line_ids.quantity, 6)
        self.assertEqual(second_bill.invoice_line_ids.purchase_line_id, order.order_line)
        self.assertEqual(second_bill.amount_total, 678)
        self.assertEqual(order.order_line.qty_invoiced, 10)
        self.assertEqual(order.order_line.qty_to_invoice, 0)

    def test_existing_draft_bill_prevents_repeat_creation(self):
        order = self._create_order()
        order.action_qing_create_bill()
        self.assertEqual(order.invoice_ids.state, 'draft')
        self._assert_no_bill_created(order)

    def test_fully_posted_bill_prevents_repeat_creation(self):
        order = self._create_order()
        order.action_qing_create_bill()
        order.invoice_ids.invoice_date = fields.Date.today()
        order.invoice_ids.action_post()
        self.assertEqual(order.invoice_ids.state, 'posted')
        self._assert_no_bill_created(order)

    def test_cancelled_draft_bill_allows_recreation(self):
        order = self._create_order()
        order.action_qing_create_bill()
        cancelled_bill = order.invoice_ids
        cancelled_bill.button_cancel()
        self.assertEqual(order.invoice_status, 'to invoice')

        order.action_qing_create_bill()
        replacement_bill = order.invoice_ids - cancelled_bill
        self.assertEqual(cancelled_bill.state, 'cancel')
        self.assertEqual(replacement_bill.state, 'draft')
        self.assertEqual(replacement_bill.invoice_line_ids.quantity, 10)
        self.assertEqual(order.order_line.qty_invoiced, 10)

    def test_rfq_and_cancelled_order_cannot_create_bill(self):
        order = self._create_order(confirm=False)
        self.assertEqual(order.state, 'draft')
        self._assert_no_bill_created(order)
        order.button_confirm()
        order.button_cancel()
        self.assertEqual(order.state, 'cancel')
        self._assert_no_bill_created(order)

    def test_purchase_user_can_create_draft_but_cannot_post(self):
        order = self._create_order()
        order.user_id = self.purchase_user
        order = order.with_user(self.purchase_user)
        order.action_qing_create_bill()
        bill = order.invoice_ids
        self.assertEqual(bill.state, 'draft')
        with self.assertRaises(AccessError):
            bill.action_post()
        self.assertEqual(bill.state, 'draft')

    def test_internal_user_without_purchase_access_cannot_create_bill(self):
        order = self._create_order().with_user(self.internal_user)
        self._assert_no_bill_created(order, AccessError)

    def test_purchase_user_cannot_create_bill_in_another_company(self):
        order = self._create_order()
        other_company = self.env['res.company'].create({'name': 'Qing Purchase Other Company'})
        self.purchase_user.write({
            'company_ids': [Command.set(other_company.ids)],
            'company_id': other_company.id,
        })
        restricted_order = order.with_user(self.purchase_user).with_context(
            allowed_company_ids=other_company.ids,
        )
        self._assert_no_bill_created(restricted_order, AccessError)

    def test_form_exposes_create_bill_before_existing_upload(self):
        purchase_order = self.env['purchase.order'].with_user(self.purchase_user)
        view = purchase_order.get_view(
            view_id=self.env.ref('purchase.purchase_order_form').id,
            view_type='form',
        )
        arch = etree.fromstring(view['arch'])
        buttons = arch.xpath('//header/button[@name="action_qing_create_bill"]')
        uploaders = arch.xpath('//header/widget[@name="purchase_file_uploader"]')
        self.assertEqual(len(buttons), 1)
        self.assertEqual(len(uploaders), 1)
        self.assertEqual(buttons[0].getnext(), uploaders[0])
        visibility_cases = [
            ('purchase', 'to invoice', False),
            ('purchase', 'no', True),
            ('purchase', 'invoiced', True),
            ('draft', 'to invoice', True),
            ('cancel', 'to invoice', True),
        ]
        for state, invoice_status, invisible in visibility_cases:
            with self.subTest(state=state, invoice_status=invoice_status):
                self.assertEqual(safe_eval(buttons[0].get('invisible'), {
                    'state': state,
                    'invoice_status': invoice_status,
                }), invisible)
