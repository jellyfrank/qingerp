import logging

from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, new_test_user, tagged
from odoo.tools.binary import BinaryBytes

_logger = logging.getLogger(__name__)
_DEFAULT_TAXES = object()


@tagged('post_install', '-at_install')
class TestFapiaoRequest(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(cls.env, login='request_manager', groups='account.group_account_manager',
            company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)])
        cls.billing = new_test_user(cls.env, login='request_billing', groups='account.group_account_invoice',
            company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)])
        cls.tax = cls.env['account.tax'].create({'name': 'Synthetic 13%', 'amount': 13, 'type_tax_use': 'sale'})
        cls.zero_tax = cls.env['account.tax'].create({'name': 'Synthetic 0%', 'amount': 0, 'type_tax_use': 'sale'})
        cls.tax_group = cls.env['account.tax'].create({'name': 'Synthetic 13% Group', 'amount_type': 'group',
            'type_tax_use': 'sale', 'children_tax_ids': [Command.set(cls.tax.ids)]})

    def _bill(self, count=1, product=None, taxes=_DEFAULT_TAXES, **vals):
        if taxes is _DEFAULT_TAXES:
            taxes = self.tax
        line_taxes = taxes if isinstance(taxes, (list, tuple)) else [taxes] * count
        bill = self.env['account.move'].create({'move_type': 'out_invoice', 'partner_id': self.partner_a.id,
            'invoice_date': '2026-09-19', 'invoice_line_ids': [Command.create({'name': 'Synthetic item', 'quantity': 10,
                'price_unit': 10, 'account_id': self.company_data['default_account_revenue'].id,
                'product_id': product.id if product else False,
                'tax_ids': [Command.set(line_taxes[index].ids if line_taxes[index] else [])]})
                for index in range(count)], **vals})
        bill.action_post()
        return bill

    def _request(self, bill=None, share=1):
        bill = bill if bill is not None else self._bill()
        action = bill.with_user(self.manager).action_create_fapiao_request()
        request = self.env['qing.fapiao.request'].with_user(self.manager).browse(action['res_id'])
        request.seller_tax_id = 'SYNTHETIC-SELLER'
        request.line_ids.write({'quantity': 10 * share, 'amount_untaxed': 100 * share,
                                'amount_tax': 13 * share})
        request.line_ids.filtered(lambda line: not line.tax_code).write({'tax_code': '3040201010000000000'})
        return request

    def _approve(self, request):
        request.action_submit()
        request.action_approve()

    def _result(self, request, **vals):
        request.write({'result_number': 'SYNTHETIC-%d' % request.id, 'result_date': '2026-09-19',
            'result_file': BinaryBytes(b'Synthetic QA. Not a tax invoice.'), 'result_filename': 'synthetic.txt',
            'result_amount_untaxed': request.amount_untaxed, 'result_amount_tax': request.amount_tax, **vals})
        request.result_checked = True

    def test_invoice_indicators_count_requests_once_and_follow_registration(self):
        bill = self._bill(count=2)
        move = bill.with_user(self.manager)
        self.assertEqual((move.qing_fapiao_request_count, move.qing_fapiao_count), (0, 0))
        self.assertEqual(move.qing_fapiao_status, 'not_invoiced')

        request = self._request(bill)
        self.assertEqual(len(request.line_ids), 2)
        self.assertEqual(move.qing_fapiao_request_count, 1)
        self.assertEqual(move.qing_fapiao_status, 'draft_request')
        self._approve(request)
        self.assertEqual(move.qing_fapiao_status, 'requested')

        self._result(request)
        request.action_record_invoice()
        self.assertEqual((move.qing_fapiao_request_count, move.qing_fapiao_count), (1, 1))
        self.assertEqual(move.qing_fapiao_status, 'invoiced')
        request.fapiao_id.cancel_reason = 'Synthetic correction'
        request.fapiao_id.action_cancel_registration()
        self.assertEqual((move.qing_fapiao_request_count, move.qing_fapiao_count), (1, 1))
        self.assertEqual(move.qing_fapiao_status, 'not_invoiced')

    def test_create_request_requires_nonzero_percentage_tax_lines(self):
        message = 'There are no invoice detail lines eligible for a tax invoice request.'
        for taxes in (False, self.zero_tax):
            bill = self._bill(taxes=taxes)
            with self.subTest(taxes=taxes):
                with self.assertRaisesRegex(UserError, message):
                    bill.with_user(self.manager).action_create_fapiao_request()

        bill = self._bill(count=3, taxes=[False, self.zero_tax, self.tax])
        action = bill.with_user(self.manager).action_create_fapiao_request()
        request = self.env['qing.fapiao.request'].with_user(self.manager).browse(action['res_id'])
        self.assertEqual(request.line_ids.mapped('source_line_id').ids, [bill.invoice_line_ids[-1].id])

        grouped_bill = self._bill(taxes=self.tax_group)
        grouped_action = grouped_bill.with_user(self.manager).action_create_fapiao_request()
        grouped_request = self.env['qing.fapiao.request'].with_user(self.manager).browse(grouped_action['res_id'])
        self.assertEqual(grouped_request.line_ids.mapped('source_line_id'), grouped_bill.invoice_line_ids)

    def test_invoice_indicator_distinguishes_partial_and_full_tax_coverage(self):
        bill = self._bill()
        move = bill.with_user(self.manager)
        first = self._request(bill, .4)
        self._approve(first)
        self._result(first)
        first.action_record_invoice()
        self.assertEqual(move.qing_fapiao_status, 'partial')
        self.assertEqual(move.qing_fapiao_count, 1)

        second = self._request(bill, .6)
        self._approve(second)
        self._result(second)
        second.action_record_invoice()
        self.assertEqual((move.qing_fapiao_request_count, move.qing_fapiao_count), (2, 2))
        self.assertEqual(move.qing_fapiao_status, 'invoiced')

        other = self._bill().with_user(self.manager)
        rows = (move | other).read(['qing_fapiao_status', 'qing_fapiao_count'])
        statuses = {row['id']: row['qing_fapiao_status'] for row in rows}
        self.assertEqual(statuses, {move.id: 'invoiced', other.id: 'not_invoiced'})

    def test_invoice_indicators_are_in_accounting_views_only(self):
        Move = self.env['account.move']
        list_view = self.env.ref('account.view_out_invoice_tree')
        form_view = self.env.ref('account.view_move_form')
        manager_list = Move.with_user(self.manager).get_view(view_id=list_view.id, view_type='list')['arch']
        manager_form = Move.with_user(self.manager).get_view(view_id=form_view.id, view_type='form')['arch']
        billing_list = Move.with_user(self.billing).get_view(view_id=list_view.id, view_type='list')['arch']
        self.assertIn('name="qing_fapiao_status"', manager_list)
        self.assertIn('name="qing_fapiao_request_count"', manager_form)
        self.assertIn('name="qing_fapiao_count"', manager_form)
        self.assertIn('icon="request_quote"', manager_form)
        self.assertIn('icon="receipt_long"', manager_form)
        self.assertNotIn('name="qing_fapiao_status"', billing_list)

    def test_manual_lifecycle_atomic_and_idempotent(self):
        bill = self._bill()
        request = self._request(bill)
        move = bill.with_user(self.manager)
        self.assertEqual(move.qing_fapiao_request_remaining_untaxed, 100)
        before = self.env['account.move'].search_count([])
        self._approve(request)
        self.assertEqual(move.qing_fapiao_reserved_tax, 13)
        self.assertEqual(move.qing_fapiao_request_remaining_untaxed, 0)
        self._result(request)
        action = request.action_record_invoice()
        self.assertEqual(request.state, 'done')
        self.assertEqual(request.fapiao_id.state, 'registered')
        self.assertEqual(request.fapiao_id.allocation_ids.state, 'confirmed')
        self.assertEqual(request.fapiao_id.registered_by, self.manager)
        self.assertEqual(request.approved_by, self.manager)
        self.assertEqual(request.action_record_invoice()['res_id'], action['res_id'])
        self.assertEqual(self.env['account.move'].search_count([]), before)
        self.assertEqual(move.qing_fapiao_reserved_tax, 0)
        self.assertEqual(move.qing_fapiao_request_remaining_tax, 0)
        request.fapiao_id.allocation_ids.cancel_reason = 'Attempt partial correction'
        with self.assertRaisesRegex(UserError, 'request'):
            request.fapiao_id.allocation_ids.action_cancel()
        request.fapiao_id.cancel_reason = 'Synthetic correction'
        request.fapiao_id.action_cancel_registration()
        self.assertEqual(move.qing_fapiao_request_remaining_untaxed, 100)
        self._request(bill).action_submit()

    def test_partial_requests_independent_net_tax_and_quantity(self):
        bill = self._bill()
        first = self._request(bill, .6)
        first.action_submit()
        second = self._request(bill, .5)
        with self.assertRaises(UserError):
            second.action_submit()
        self.assertEqual(second.state, 'draft')
        second.line_ids.write({'quantity': 4, 'amount_untaxed': 40, 'amount_tax': 5.2})
        self._approve(second)
        self._result(second)
        second.action_record_invoice()
        self.assertEqual(bill.with_user(self.manager).qing_fapiao_request_remaining_untaxed, 0)
        first.cancel_reason = 'Rejected'
        first.action_cancel()
        third = self._request(bill, .6)
        third.line_ids.quantity = 7
        with self.assertRaises(UserError):
            third.action_submit()
        third.line_ids.quantity = 6
        third.action_submit()

    def test_manual_allocations_cannot_consume_reserved_tax(self):
        bill = self._bill()
        request = self._request(bill, .8)
        request.action_submit()
        ticket = self.env['qing.fapiao'].with_user(self.manager).create({'number': 'MANUAL', 'direction': 'out',
            'partner_id': self.partner_a.id, 'buyer_name': 'Buyer', 'seller_name': 'Seller',
            'source_file': BinaryBytes(b'Synthetic'), 'source_filename': 'synthetic.txt', 'amount_untaxed': 100, 'amount_tax': 13})
        ticket.action_register()
        allocation = self.env['qing.fapiao.allocation'].with_user(self.manager).create({'fapiao_id': ticket.id,
            'move_id': bill.id, 'amount_untaxed': 20, 'amount_tax': 3})
        with self.assertRaises(UserError):
            allocation.action_confirm()
        self.assertEqual(allocation.state, 'draft')
        allocation.amount_tax = 2.6
        allocation.action_confirm()
        request.cancel_reason = 'Withdraw'
        request.action_cancel()
        another = self._request(bill, 1)
        with self.assertRaises(UserError):
            another.action_submit()

    def test_approval_validation_and_freeze(self):
        request = self._request()
        request.line_ids.tax_code = False
        with self.assertRaises(UserError):
            request.action_submit()
        request.line_ids.tax_code = '3040201010000000000'
        request.kind = 'special'
        request.buyer_tax_id = False
        with self.assertRaises(UserError):
            request.action_submit()
        request.buyer_tax_id = 'SYNTHETIC-BUYER'
        request.action_submit()
        for operation in [lambda: request.write({'buyer_name': 'Changed'}),
                          lambda: request.line_ids.write({'description': 'Changed'}),
                          lambda: request.line_ids.unlink(), lambda: request.unlink()]:
            with self.assertRaises(UserError):
                operation()
        bill = request.line_ids.move_id.with_user(self.billing)
        with self.assertRaises(UserError):
            bill.button_draft()
        with self.assertRaises(UserError):
            bill.invoice_line_ids.write({'name': 'Changed'})
        request.cancel_reason = 'Correction'
        request.action_cancel()
        bill.button_draft()
        self.assertEqual(bill.state, 'draft')

    def test_delete_cancelled_requests_cascades_lines_without_changing_capacity(self):
        bill = self._bill()
        cancelled = self._request(bill)
        self._approve(cancelled)
        cancelled.cancel_reason = 'Synthetic withdrawal'
        cancelled.action_cancel()
        draft = self._request(bill)
        lines = (cancelled | draft).line_ids
        with self.assertRaises(UserError):
            cancelled.line_ids.unlink()
        (cancelled | draft).unlink()
        self.assertFalse(lines.exists())
        self.assertEqual(bill.qing_fapiao_request_remaining_untaxed, 100)
        self.assertEqual(bill.qing_fapiao_reserved_tax, 0)
        self.assertEqual(bill.state, 'posted')

    def test_mixed_delete_does_not_remove_cancelled_request(self):
        cancelled = self._request()
        cancelled.cancel_reason = 'Synthetic withdrawal'
        cancelled.action_cancel()
        approved = self._request()
        self._approve(approved)
        with self.assertRaises(UserError):
            (cancelled | approved).unlink()
        self.assertEqual(len((cancelled | approved).exists()), 2)
        self.assertTrue(cancelled.line_ids.exists())

    def test_submission_tax_id_rules_report_the_missing_party(self):
        request = self._request()
        request.buyer_tax_id = False
        request.seller_tax_id = False
        with self.assertRaisesRegex(UserError, 'seller tax ID'):
            request.action_submit()
        request.seller_tax_id = 'SYNTHETIC-SELLER'
        request.action_submit()

        special = self._request()
        special.kind = 'special'
        special.buyer_tax_id = False
        with self.assertRaisesRegex(UserError, 'buyer tax ID for special VAT invoices'):
            special.action_submit()

    def test_partner_links_and_editable_snapshot_defaults(self):
        self.partner_a.vat = '91370200SYNTHETIC01'
        request = self._request()
        self.assertEqual(request.buyer_partner_id, self.partner_a.commercial_partner_id)
        self.assertEqual(request.seller_partner_id, self.env.company.partner_id.commercial_partner_id)
        self.assertEqual(request.buyer_name, self.partner_a.name)
        self.assertEqual(request.buyer_tax_id, self.partner_a.vat)
        request.buyer_name = 'Approved invoice title'
        request.action_submit()
        self.partner_a.name = 'Changed customer master data'
        self.assertEqual(request.buyer_name, 'Approved invoice title')

    def test_tax_classification_catalog_and_product_defaults(self):
        category_code = self.env['qing.fapiao.tax.classification'].with_user(self.manager).create({
            'code': '1090512010000000000', 'name': 'Synthetic office equipment',
            'short_name': 'Office equipment'})
        product_code = self.env['qing.fapiao.tax.classification'].with_user(self.manager).create({
            'code': '3040201010000000000', 'name': 'Synthetic information service'})
        category = self.env['product.category'].create({'name': 'Synthetic invoicing category',
            'qing_fapiao_tax_classification_id': category_code.id})
        product = self.env['product.product'].create({'name': 'Synthetic mapped product', 'categ_id': category.id})
        inherited = self._request(self._bill(product=product))
        self.assertEqual(inherited.line_ids.tax_classification_id, category_code)
        self.assertEqual(inherited.line_ids.tax_code, category_code.code)
        self.assertEqual(inherited.line_ids.tax_classification_name, category_code.name)

        product.product_tmpl_id.qing_fapiao_tax_classification_id = product_code
        overridden = self._request(self._bill(product=product))
        self.assertEqual(overridden.line_ids.tax_classification_id, product_code)
        overridden.line_ids.tax_code = category_code.code
        self.assertFalse(overridden.line_ids.tax_classification_id)
        self.assertFalse(overridden.line_ids.tax_classification_name)

        with self.assertRaises(ValidationError):
            self.env['qing.fapiao.tax.classification'].with_user(self.manager).create({
                'code': 'NOT-19-DIGITS', 'name': 'Invalid'})
        with self.assertRaises(AccessError):
            self.env['qing.fapiao.tax.classification'].with_user(self.billing).search([])

    def test_blocked_and_invalid_tax_classification_codes_cannot_submit(self):
        blocked = self.env['qing.fapiao.tax.classification'].with_user(self.manager).create({
            'code': '1090512990000000000', 'name': 'Synthetic summary code',
            'invoicing_allowed': False})
        request = self._request()
        request.line_ids.tax_classification_id = blocked
        with self.assertRaisesRegex(UserError, 'allowed for invoicing'):
            request.action_submit()
        request.line_ids.tax_code = '123'
        with self.assertRaisesRegex(UserError, 'exactly 19 digits'):
            request.action_submit()

    def test_result_validation_and_duplicate_rollback(self):
        bill = self._bill()
        first = self._request(bill, .5)
        self._approve(first)
        with self.assertRaises(UserError):
            first.action_record_invoice()
        self._result(first, result_amount_tax=0)
        with self.assertRaises(UserError):
            first.action_record_invoice()
        self.assertEqual(first.state, 'approved')
        self.assertFalse(first.fapiao_id)
        first.result_amount_tax = 6.5
        self.assertFalse(first.result_checked)
        first.result_checked = True
        first.action_record_invoice()
        second = self._request(bill, .5)
        self._approve(second)
        self._result(second, result_number=first.result_number)
        before = self.env['qing.fapiao'].search_count([])
        with self.assertRaises(UserError):
            second.action_record_invoice()
        self.assertEqual(second.state, 'approved')
        self.assertEqual(self.env['qing.fapiao'].search_count([]), before)
        self.assertFalse(second.fapiao_id)
        self.assertEqual(bill.with_user(self.manager).qing_fapiao_reserved_untaxed, 50)

    def test_acl_defaults_and_terminal_states(self):
        request = self._request()
        for operation in [lambda: request.with_user(self.billing).read(['name']),
                          lambda: request.with_user(self.billing).action_submit(),
                          lambda: request.write({'state': 'done'}),
                          lambda: request.line_ids.write({'tax_description': 'Changed'}),
                          lambda: request.with_context(default_state='done').create({})]:
            with self.assertRaises(AccessError):
                operation()
        other = self.env['res.company'].create({'name': 'Other synthetic'})
        self.manager.company_ids = [Command.link(other.id)]
        with self.assertRaises(AccessError):
            request.with_context(allowed_company_ids=other.ids).read(['name'])
        with self.assertRaises(UserError):
            request.copy()
        request.cancel_reason = 'Cancelled'
        request.action_cancel()
        for operation in [lambda: request.write({'cancel_reason': 'Overwrite'}), lambda: request.action_submit(),
                          lambda: request.line_ids.copy()]:
            with self.assertRaises((UserError, AccessError)):
                operation()
        with self.assertRaises(AccessError):
            request.with_user(self.billing).unlink()
        request.unlink()
        self.assertFalse(request.exists())

    def test_source_scope_and_changed_drafts(self):
        for vals in ({'move_type': 'in_invoice'}, {'move_type': 'out_refund'}):
            with self.assertRaises(UserError):
                self._request(self._bill(**vals))
        first, second = self._bill(), self._bill(partner_id=self.partner_b.id)
        with self.assertRaises(UserError):
            self._request(first + second)
        request = self._request(first)
        first.button_draft()
        with self.assertRaises(UserError):
            request.action_submit()
        self.assertEqual(request.state, 'draft')

    def test_chinese_validation_message(self):
        self.env['res.lang']._activate_lang('zh_CN')
        request = self._request().with_context(lang='zh_CN')
        request.seller_tax_id = False
        with self.assertRaisesRegex(UserError, '^请填写销售方税号。$'):
            request.action_submit()
        request.seller_tax_id = 'SYNTHETIC-SELLER'
        request.kind = 'special'
        request.buyer_tax_id = False
        with self.assertRaisesRegex(UserError, '^增值税专用发票请填写购买方税号。$'):
            request.action_submit()
        request.kind = 'normal'
        request.line_ids.tax_code = False
        with self.assertRaisesRegex(UserError, '请逐行填写'):
            request.action_submit()
        request.line_ids.tax_code = '123'
        with self.assertRaisesRegex(UserError, '必须为 19 位数字'):
            request.action_submit()

    def test_multi_bill_merge_and_form(self):
        request = self._request(self._bill() + self._bill())
        with Form(request) as form:
            form.note = 'Synthetic form validation'
        self._approve(request)
        self._result(request)
        request.action_record_invoice()
        self.assertEqual(len(request.fapiao_id.allocation_ids), 2)
        self.assertEqual(request.fapiao_id.amount_total, 226)

    def test_50_and_150_line_scaling(self):
        counts = []
        for size in (50, 150):
            request = self._request(self._bill(count=size))
            self.env.flush_all()
            before = self.cr.sql_log_count
            request.line_ids[0].description = 'Single changed item'
            self.env.flush_all()
            edit = self.cr.sql_log_count - before
            before = self.cr.sql_log_count
            self._approve(request)
            self._result(request)
            request.action_record_invoice()
            self.env.flush_all()
            transition = self.cr.sql_log_count - before
            counts.append((edit, transition))
            self.assertEqual(len(request.line_ids), size)
            self.assertEqual(request.fapiao_id.amount_untaxed, size * 100)
            _logger.info('REQUEST_PERF lines=%s single_edit_queries=%s lifecycle_queries=%s', size, edit, transition)
        self.assertLessEqual(counts[1][0], counts[0][0] + 5)
        self.assertLessEqual(counts[1][1], counts[0][1] + 30)

    def test_reset_draft_releases_capacity_and_requires_reapproval(self):
        request = self._request()
        bill = request.line_ids.move_id
        self._approve(request)
        self.assertEqual(bill.qing_fapiao_reserved_tax, 13)
        request.action_reset_draft()
        self.assertEqual(request.state, 'draft')
        self.assertFalse(request.approved_at)
        self.assertFalse(request.submitted_at)
        self.assertEqual(request.reset_by, self.manager)
        self.assertEqual(bill.qing_fapiao_reserved_tax, 0)
        request.line_ids.description = 'Corrected item'
        with self.assertRaises(UserError):
            request.action_record_invoice()
        self._approve(request)
        self.assertEqual(bill.qing_fapiao_reserved_tax, 13)
        request.cancel_reason = 'Correction'
        request.action_cancel()
        request.action_reset_draft()
        self.assertFalse(request.cancelled_at)
        self.assertFalse(request.cancel_reason)

    def test_reset_draft_permission_and_result_guards(self):
        request = self._request()
        request.action_submit()
        with self.assertRaises(AccessError):
            request.with_user(self.billing).action_reset_draft()
        request.action_reset_draft()
        self._approve(request)
        self._result(request)
        with self.assertRaises(UserError):
            request.action_reset_draft()
        request.action_record_invoice()
        with self.assertRaises(UserError):
            request.action_reset_draft()
