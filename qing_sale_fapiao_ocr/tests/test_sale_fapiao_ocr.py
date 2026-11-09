import logging
from pathlib import Path
import time
from unittest.mock import patch

from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged
from odoo.tools.binary import BinaryBytes

_logger = logging.getLogger(__name__)

TEXT = '''仅测试样票
发票号码：12345678901234567890
开票日期：2026年09月16日
购买方名称：合成采购公司
购买方税号：TESTBUYER
销售方名称：合成销售公司
销售方税号：TESTSELLER
不含税金额：100.00
税额：13.00
价税合计：113.00
'''


@tagged('post_install', '-at_install')
class TestSaleFapiaoOcr(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(
            cls.env, login='sale_ocr_manager',
            groups='account.group_account_manager,sales_team.group_sale_manager',
            company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)],
        )
        cls.salesperson = new_test_user(
            cls.env, login='sale_ocr_salesperson', groups='sales_team.group_sale_manager',
            company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)],
        )
        cls.tax = cls.env['account.tax'].create({
            'name': 'Synthetic sale VAT 13%', 'amount': 13, 'amount_type': 'percent',
            'type_tax_use': 'sale', 'company_id': cls.env.company.id,
            'price_include_override': 'tax_excluded',
        })
        cls.product = cls._create_product(
            name='Synthetic sale invoice service', type='service', invoice_policy='order',
            list_price=100, taxes_id=cls.tax,
        )
        cls.delivery_product = cls.product.copy({'invoice_policy': 'delivery'})
        # A generic document may use arbitrary names. Tax IDs are compared only
        # when present on both the reviewed document and the business partner.
        cls.partner_a.with_context(no_vat_validation=True).vat = False
        cls.env.company.partner_id.with_context(no_vat_validation=True).vat = False

    def _order(self, quantity=1, product=None, lines=1):
        product = product or self.product
        order = self.env['sale.order'].with_user(self.manager).create({
            'partner_id': self.partner_a.id,
            'partner_invoice_id': self.partner_a.id,
            'company_id': self.env.company.id,
            'user_id': self.manager.id,
            'order_line': [Command.create({
                'name': 'Synthetic line %03d' % index, 'sequence': index,
                'product_id': product.id, 'product_uom_qty': quantity,
                'price_unit': 100, 'tax_ids': [Command.set(self.tax.ids)],
            }) for index in range(lines)],
        })
        order.action_confirm()
        return order

    def _wizard(self, order, **values):
        return self.env['qing.fapiao.ocr'].with_user(self.manager).create({
            'sale_order_id': order.id, 'source_file': BinaryBytes(b'synthetic'),
            'source_filename': 'synthetic.png', **values,
        })

    def _review(self, wizard, untaxed=100, tax=13, number=None):
        result = {
            'text': TEXT, 'engine': 'local',
            'pages': [{'number': 1, 'text': TEXT, 'source': 'ocr'}], 'warnings': [],
        }
        with patch.object(type(self.env['qing.ocr.service']), '_extract_text', return_value=result):
            wizard.action_recognize()
        values = {'amount_untaxed': untaxed, 'amount_tax': tax,
                  'invoice_total': untaxed + tax, 'reviewed': True}
        if number:
            values['number'] = number
        wizard.write(values)
        return wizard

    def _invoice(self, wizard):
        action = wizard.action_create_draft()
        self.assertEqual(action['res_model'], 'account.move')
        return self.env['account.move'].browse(action['res_id'])

    def _counts(self):
        return tuple(self.env[model].search_count([]) for model in (
            'account.move', 'qing.fapiao', 'ir.attachment',
        ))

    def test_real_pdf_generates_linked_customer_draft_and_keeps_source(self):
        order = self._order()
        fixture = Path(__file__).parents[2] / 'qing_account_fapiao_ocr/tests/fixtures/synthetic_tax_invoice.pdf'
        document = fixture.read_bytes()
        wizard = self._wizard(order, source_file=BinaryBytes(document), source_filename='synthetic-tax.pdf')
        wizard.action_recognize()
        self.assertTrue(wizard.recognized)
        self.assertEqual(wizard.number, '12345678901234567890')
        self.assertEqual(wizard.invoice_total, 113)
        wizard.reviewed = True
        invoice = self._invoice(wizard)
        ticket = wizard.fapiao_id
        self.assertEqual(invoice.state, 'draft')
        self.assertEqual(invoice.move_type, 'out_invoice')
        self.assertEqual(invoice.invoice_date, wizard.invoice_date)
        self.assertEqual(invoice.amount_total, 113)
        self.assertEqual(invoice.invoice_line_ids.sale_line_ids, order.order_line)
        self.assertEqual(order.order_line.qty_invoiced, 1)
        self.assertEqual(order.order_line.qty_to_invoice, 0)
        self.assertEqual(ticket.state, 'draft')
        self.assertEqual(ticket.direction, 'out')
        self.assertEqual(ticket.color, 'blue')
        self.assertEqual(ticket.move_ids, invoice)
        self.assertFalse(ticket.allocation_ids)
        self.assertEqual(bytes(ticket.source_file), document)
        attachments = self.env['ir.attachment'].search([
            ('res_model', '=', 'account.move'), ('res_id', '=', invoice.id),
            ('name', '=', 'synthetic-tax.pdf'),
        ])
        self.assertEqual(len(attachments), 1)
        self.assertEqual(bytes(attachments.raw), document)

    def test_entry_has_order_context_and_blocks_quotation(self):
        order = self._order()
        action = order.with_user(self.manager).action_upload_fapiao()
        self.assertEqual(action['res_model'], 'qing.fapiao.ocr')
        self.assertEqual(action['context']['default_sale_order_id'], order.id)
        order._action_cancel()
        with self.assertRaises(UserError):
            order.with_user(self.manager).action_upload_fapiao()
        with self.assertRaises(UserError):
            self._wizard(order)

    def test_recognition_and_review_are_both_required(self):
        wizard = self._wizard(self._order())
        with self.assertRaises(UserError):
            wizard.action_create_draft()
        self._review(wizard)
        wizard.reviewed = False
        with self.assertRaises(UserError):
            wizard.action_create_draft()
        self.assertFalse(wizard.fapiao_id)
        self.assertFalse(wizard.sale_order_id.invoice_ids)

    def test_existing_draft_is_reused_without_changing_date_or_reference(self):
        order = self._order()
        invoice = order._create_invoices()
        invoice.write({'invoice_date': '2026-09-01', 'ref': 'Existing business reference'})
        original = (invoice.invoice_date, invoice.ref, invoice.state)
        wizard = self._review(self._wizard(order, sale_invoice_id=invoice.id))
        before = self.env['account.move'].search_count([])
        self.assertIn(invoice, wizard.available_sale_invoice_ids)
        self.assertEqual(self._invoice(wizard), invoice)
        self.assertEqual(self.env['account.move'].search_count([]), before)
        self.assertEqual((invoice.invoice_date, invoice.ref, invoice.state), original)
        self.assertEqual(wizard.fapiao_id.move_ids, invoice)

    def test_existing_posted_invoice_is_reused_without_new_receivable(self):
        order = self._order()
        invoice = order._create_invoices()
        invoice.action_post()
        original = (invoice.name, invoice.invoice_date, invoice.ref, invoice.amount_residual)
        wizard = self._review(self._wizard(order, sale_invoice_id=invoice.id))
        before = self.env['account.move'].search_count([])
        self.assertEqual(self._invoice(wizard), invoice)
        self.assertEqual(invoice.state, 'posted')
        self.assertEqual((invoice.name, invoice.invoice_date, invoice.ref, invoice.amount_residual), original)
        self.assertEqual(self.env['account.move'].search_count([]), before)
        self.assertEqual(wizard.fapiao_id.state, 'draft')

    def test_unique_existing_matching_invoice_is_automatically_reused(self):
        order = self._order()
        invoice = order._create_invoices()
        wizard = self._review(self._wizard(order))
        self.assertEqual(self._invoice(wizard), invoice)
        self.assertEqual(order.invoice_ids, invoice)

    def test_ambiguous_existing_invoices_require_explicit_selection(self):
        order = self._order(quantity=2)
        first = order._create_invoices()
        first.invoice_line_ids.quantity = 1
        second = order._create_invoices()
        self.assertEqual(first.amount_total, second.amount_total)
        wizard = self._review(self._wizard(order))
        before = self._counts()
        with self.assertRaises(UserError):
            wizard.action_create_draft()
        self.assertEqual(self._counts(), before)
        wizard.write({'sale_invoice_id': second.id, 'reviewed': True})
        self.assertEqual(self._invoice(wizard), second)

    def test_billing_contact_keeps_commercial_customer_on_tax_invoice(self):
        order = self._order()
        billing = self.env['res.partner'].create({
            'name': 'Synthetic billing department', 'parent_id': self.partner_a.id, 'type': 'invoice',
        })
        order.partner_invoice_id = billing
        wizard = self._review(self._wizard(order))
        invoice = self._invoice(wizard)
        self.assertEqual(invoice.partner_id, billing)
        self.assertEqual(wizard.partner_id, self.partner_a)
        self.assertEqual(wizard.fapiao_id.partner_id, self.partner_a)

    def test_partial_delivery_invoices_only_delivered_quantity(self):
        order = self._order(quantity=5, product=self.delivery_product)
        order.order_line.qty_delivered = 2
        wizard = self._review(self._wizard(order), untaxed=200, tax=26)
        invoice = self._invoice(wizard)
        self.assertEqual(invoice.invoice_line_ids.quantity, 2)
        self.assertEqual(order.order_line.qty_invoiced, 2)
        order.order_line.qty_delivered = 3
        self.assertEqual(order.order_line.qty_to_invoice, 1)

    def test_undelivered_order_creates_nothing(self):
        order = self._order(product=self.delivery_product)
        wizard = self._review(self._wizard(order))
        before = self._counts()
        with self.assertRaises(UserError):
            wizard.action_create_draft()
        self.assertEqual(self._counts(), before)
        self.assertFalse(order.invoice_ids)
        self.assertFalse(wizard.fapiao_id)

    def test_amount_and_tax_mismatches_roll_back_new_invoice(self):
        for untaxed, tax in ((101, 13), (100, 14), (99, 14)):
            with self.subTest(untaxed=untaxed, tax=tax):
                order = self._order()
                wizard = self._review(self._wizard(order), untaxed=untaxed, tax=tax)
                before = self._counts()
                # No test-side savepoint: the action itself must be atomic even
                # when a caller handles the UserError in the same transaction.
                with self.assertRaises(UserError):
                    wizard.action_create_draft()
                self.assertEqual(self._counts(), before)
                self.assertFalse(order.invoice_ids)
                self.assertEqual(order.order_line.qty_to_invoice, 1)
                self.assertFalse(wizard.fapiao_id)

    def test_repeated_action_and_duplicate_number_do_not_duplicate_invoices(self):
        wizard = self._review(self._wizard(self._order()))
        invoice = self._invoice(wizard)
        before = self._counts()
        self.assertEqual(self._invoice(wizard), invoice)
        self.assertEqual(self._counts(), before)
        second = self._review(self._wizard(self._order()))
        before = self._counts()
        with self.assertRaises((UserError, ValidationError)):
            second.action_create_draft()
        self.assertEqual(self._counts(), before)
        self.assertFalse(second.sale_order_id.invoice_ids)

    def test_existing_invoice_cannot_receive_second_full_value_tax_invoice(self):
        order = self._order()
        first = self._review(self._wizard(order))
        invoice = self._invoice(first)
        second = self._review(self._wizard(order, sale_invoice_id=invoice.id), number='22345678901234567890')
        before = self._counts()
        with self.assertRaises(UserError):
            second.action_create_draft()
        self.assertEqual(self._counts(), before)

    def test_invoice_from_another_order_is_rejected(self):
        order = self._order()
        other_invoice = self._order()._create_invoices()
        before = self._counts()
        with self.assertRaises((UserError, ValidationError)):
            wizard = self._review(self._wizard(order, sale_invoice_id=other_invoice.id))
            wizard.action_create_draft()
        self.assertEqual(self._counts(), before)
        self.assertFalse(order.invoice_ids)

    def test_invoice_consolidating_multiple_orders_is_rejected(self):
        first, second = self._order(), self._order()
        invoice = (first | second)._create_invoices(grouped=False)
        self.assertEqual(len(invoice), 1)
        self.assertEqual(invoice.invoice_line_ids.sale_line_ids.order_id, first | second)
        wizard = self._review(self._wizard(first, sale_invoice_id=invoice.id), untaxed=200, tax=26)
        before = self._counts()
        with self.assertRaises(UserError):
            wizard.action_create_draft()
        self.assertEqual(self._counts(), before)
        self.assertFalse(wizard.fapiao_id)

    def test_existing_allocation_without_direct_tax_source_link_is_rejected(self):
        order = self._order()
        invoice = order._create_invoices()
        invoice.action_post()
        ticket = self.env['qing.fapiao'].with_user(self.manager).create({
            'number': 'SYNTHETIC-EXISTING-ALLOCATION', 'direction': 'out',
            'company_id': order.company_id.id, 'currency_id': order.currency_id.id,
            'partner_id': self.partner_a.id, 'amount_untaxed': 100, 'amount_tax': 13,
            'source_file': BinaryBytes(b'synthetic'), 'source_filename': 'earlier-invoice.png',
        })
        ticket.action_register()
        allocation = self.env['qing.fapiao.allocation'].with_user(self.manager).create({
            'fapiao_id': ticket.id, 'move_id': invoice.id, 'amount_untaxed': 100, 'amount_tax': 13,
        })
        allocation.action_confirm()
        self.assertFalse(ticket.move_ids)
        wizard = self._review(self._wizard(order, sale_invoice_id=invoice.id))
        before = self._counts()
        with self.assertRaises(UserError):
            wizard.action_create_draft()
        self.assertEqual(self._counts(), before)
        self.assertEqual(allocation.state, 'confirmed')
        self.assertFalse(wizard.fapiao_id)

    def test_submitted_approved_and_completed_invoice_requests_block_upload(self):
        order = self._order()
        invoice = order._create_invoices()
        invoice.action_post()
        action = invoice.with_user(self.manager).action_create_fapiao_request()
        request = self.env['qing.fapiao.request'].with_user(self.manager).browse(action['res_id'])
        request.seller_tax_id = 'TESTSELLER'
        request.line_ids.write({'tax_code': '3040201010000000000'})
        wizard = self._review(self._wizard(order, sale_invoice_id=invoice.id))
        request.action_submit()
        for state in ('submitted', 'approved', 'done'):
            with self.subTest(state=state):
                self.assertEqual(request.state, state)
                before = self._counts()
                with self.assertRaises(UserError):
                    wizard.action_create_draft()
                self.assertEqual(self._counts(), before)
                self.assertFalse(wizard.fapiao_id)
                if state == 'submitted':
                    request.action_approve()
                elif state == 'approved':
                    request.write({
                        'result_number': 'SYNTHETIC-REQUEST-RESULT', 'result_date': '2026-09-16',
                        'result_file': BinaryBytes(b'synthetic'), 'result_filename': 'request-result.png',
                        'result_amount_untaxed': 100, 'result_amount_tax': 13, 'result_checked': True,
                    })
                    request.action_record_invoice()

    def test_existing_invoice_changed_partner_currency_or_state_is_rejected(self):
        for change in ('partner', 'currency', 'cancelled'):
            with self.subTest(change=change):
                order = self._order()
                invoice = order._create_invoices()
                if change == 'partner':
                    invoice.partner_id = self.partner_b
                elif change == 'currency':
                    currency = self.env.ref('base.EUR')
                    if currency == order.currency_id:
                        currency = self.env.ref('base.USD')
                    invoice.currency_id = currency
                else:
                    invoice.button_cancel()
                wizard = self._review(self._wizard(order, sale_invoice_id=invoice.id))
                before = self._counts()
                with self.assertRaises(UserError):
                    wizard.action_create_draft()
                self.assertEqual(self._counts(), before)

    def test_tax_ids_are_checked_but_customer_names_are_reviewed_manually(self):
        self.partner_a.with_context(no_vat_validation=True).vat = 'TESTBUYER'
        self.env.company.partner_id.with_context(no_vat_validation=True).vat = 'TESTSELLER'
        order = self._order()
        wizard = self._review(self._wizard(order))
        wizard.write({'buyer_name': 'Customer trading name', 'seller_name': 'Seller trading name', 'reviewed': True})
        self.assertEqual(self._invoice(wizard).amount_total, 113)
        for key in ('buyer_tax_id', 'seller_tax_id'):
            with self.subTest(key=key):
                second = self._review(self._wizard(self._order()), number='22345678901234567890')
                second.write({key: 'WRONG-TAX-ID', 'reviewed': True})
                before = self._counts()
                with self.assertRaises(UserError):
                    second.action_create_draft()
                self.assertEqual(self._counts(), before)

    def test_fixed_order_values_and_context_cannot_be_forged(self):
        order = self._order()
        other_order = self._order()
        wizard = self.env['qing.fapiao.ocr'].with_user(self.manager).with_context(
            default_sale_order_id=order.id, default_direction='in', default_color='red',
            default_partner_id=self.partner_b.id, default_recognized=True,
        ).create({'source_file': BinaryBytes(b'x'), 'source_filename': 'x.png',
                  'direction': 'in', 'color': 'red', 'partner_id': self.partner_b.id,
                  'currency_id': self.env.ref('base.CNY').id})
        self.assertEqual(wizard.sale_order_id, order)
        self.assertEqual(wizard.company_id, order.company_id)
        self.assertEqual(wizard.currency_id, order.currency_id)
        self.assertEqual(wizard.partner_id, order.partner_invoice_id.commercial_partner_id)
        self.assertEqual((wizard.direction, wizard.color), ('out', 'blue'))
        self.assertFalse(wizard.recognized)
        for values in ({'sale_order_id': other_order.id}, {'sale_order_id': False},
                       {'direction': 'in'}, {'color': 'red'}, {'partner_id': self.partner_b.id}):
            with self.subTest(values=values), self.assertRaises((AccessError, UserError, ValidationError)):
                wizard.write(values)

    def test_sales_only_and_order_write_restricted_users_are_blocked(self):
        order = self._order()
        with self.assertRaises(AccessError):
            order.with_user(self.salesperson).action_upload_fapiao()
        with self.assertRaises(AccessError):
            self.env['qing.fapiao.ocr'].with_user(self.salesperson).create({
                'sale_order_id': order.id, 'source_file': BinaryBytes(b'x'), 'source_filename': 'x.png',
            })
        wizard = self._wizard(order)
        self.env['ir.access'].create({
            'name': 'Synthetic restriction on this sale order',
            'model_id': self.env['ir.model']._get_id('sale.order'),
            'operation': 'u', 'domain': "[('id', '!=', %s)]" % order.id,
        })
        with self.assertRaises(AccessError):
            order.with_user(self.manager).action_upload_fapiao()
        with self.assertRaises(AccessError):
            self._review(wizard)

    def test_current_company_must_match_the_order(self):
        order = self._order()
        other_company = self.env['res.company'].create({'name': 'Synthetic other OCR company'})
        self.manager.company_ids += other_company
        with self.assertRaises(UserError):
            order.with_user(self.manager).with_company(other_company).action_upload_fapiao()
        with self.assertRaises(UserError):
            self.env['qing.fapiao.ocr'].with_user(self.manager).with_company(other_company).create({
                'sale_order_id': order.id, 'source_file': BinaryBytes(b'x'), 'source_filename': 'x.png',
            })

    def test_replacing_source_invalidates_recognition_and_review(self):
        order = self._order()
        wizard = self._review(self._wizard(order))
        wizard.write({'source_file': BinaryBytes(b'replacement'), 'reviewed': True})
        self.assertFalse(wizard.recognized)
        self.assertFalse(wizard.reviewed)
        self.assertFalse(wizard.number)
        self.assertEqual(wizard.sale_order_id, order)
        with self.assertRaises(UserError):
            wizard.action_create_draft()
        self.assertFalse(order.invoice_ids)

    def test_native_down_payment_is_deducted_from_final_invoice(self):
        order = self._order()
        self.env['sale.advance.payment.inv'].with_user(self.manager).create({
            'sale_order_ids': [Command.set(order.ids)],
            'advance_payment_method': 'percentage', 'amount': 20,
        }).create_invoices()
        deposit = order.invoice_ids
        deposit.action_post()
        self.assertEqual(deposit.amount_total, 22.6)
        wizard = self._review(self._wizard(order), untaxed=80, tax=10.4)
        self.assertTrue(wizard.deduct_down_payments)
        invoice = self._invoice(wizard)
        self.assertEqual(invoice.amount_total, 90.4)
        self.assertEqual(invoice.state, 'draft')
        self.assertEqual(len(order.invoice_ids), 2)
        self.assertTrue(invoice.invoice_line_ids.filtered(lambda line: line.quantity < 0))

    def test_standalone_tax_upload_keeps_the_original_tax_only_workflow(self):
        wizard = self.env['qing.fapiao.ocr'].with_user(self.manager).create({
            'source_file': BinaryBytes(b'synthetic'), 'source_filename': 'standalone.png',
            'partner_id': self.partner_a.id,
        })
        self._review(wizard)
        before = self.env['account.move'].search_count([])
        action = wizard.action_create_draft()
        self.assertEqual(action['res_model'], 'qing.fapiao')
        self.assertEqual(wizard.fapiao_id.direction, 'in')
        self.assertFalse(wizard.fapiao_id.move_ids)
        self.assertEqual(self.env['account.move'].search_count([]), before)

    def test_50_and_150_line_invoices_and_single_line_edit_scaling(self):
        measurements = []
        for count in (50, 150):
            with self.cr.savepoint() as savepoint:
                order = self._order(lines=count)
                self.env.flush_all()
                written_ids = set()
                line_type = type(self.env['sale.order.line'])
                original_write = line_type.write

                def track_write(lines, values):
                    written_ids.update(lines.ids)
                    return original_write(lines, values)

                before = self.cr.sql_log_count
                with patch.object(line_type, 'write', track_write):
                    order.order_line[0].write({'name': 'Edited one source line'})
                    self.env.flush_all()
                edit_queries = self.cr.sql_log_count - before
                self.assertEqual(written_ids, set(order.order_line[:1].ids))
                wizard = self._review(self._wizard(order), untaxed=100 * count, tax=13 * count)
                before = self.cr.sql_log_count
                started = time.monotonic()
                invoice = self._invoice(wizard)
                self.env.flush_all()
                measurement = {
                    'lines': count, 'edit_queries': edit_queries, 'edited_rows': len(written_ids),
                    'invoice_queries': self.cr.sql_log_count - before,
                    'invoice_seconds': round(time.monotonic() - started, 3),
                }
                measurements.append(measurement)
                self.assertEqual(len(invoice.invoice_line_ids), count)
                self.assertEqual(invoice.invoice_line_ids.sale_line_ids, order.order_line)
                self.assertEqual(invoice.invoice_line_ids.mapped('name'), order.order_line.mapped('name'))
                _logger.info('SALE_OCR_PERFORMANCE %s', measurement)
                savepoint.close()
        # Small fixed costs may vary with warmed caches, but a single row edit
        # must not become a whole-order SQL or ORM write operation.
        self.assertLessEqual(measurements[1]['edit_queries'], measurements[0]['edit_queries'] + 5)
