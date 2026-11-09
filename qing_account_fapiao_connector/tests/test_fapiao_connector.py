import base64
import logging
from copy import deepcopy
from unittest.mock import patch

from lxml import etree
from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, new_test_user, tagged
from odoo.tools.binary import BinaryBytes

_logger = logging.getLogger(__name__)

CAPABILITIES = {
    'issue_blue': True, 'query': True, 'documents': True, 'callback': False,
    'invoice_types': ['normal', 'special'], 'currencies': ['USD', 'CNY', 'EUR'],
}


@tagged('post_install', '-at_install')
class TestFapiaoConnector(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(cls.env, login='connector_manager', groups='account.group_account_manager',
            company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)])
        cls.billing = new_test_user(cls.env, login='connector_billing', groups='account.group_account_invoice',
            company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)])
        cls.tax = cls.env['account.tax'].create({'name': 'Synthetic VAT', 'amount': 13, 'type_tax_use': 'sale'})
        cls.provider = cls.env['qing.fapiao.provider'].with_user(cls.manager).create({
            'name': 'Synthetic provider', 'seller_tax_id': 'SYNTHETIC-SELLER',
            'company_id': cls.env.company.id, 'code': 'none', 'environment': 'test', 'enabled': True,
        })

    def _request(self, count=1, **bill_values):
        bill = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner_a.id, 'invoice_date': '2026-09-19',
            'invoice_line_ids': [Command.create({
                'name': 'Synthetic item', 'quantity': 10, 'price_unit': 10,
                'account_id': self.company_data['default_account_revenue'].id,
                'tax_ids': [Command.set(self.tax.ids)],
            }) for _ in range(count)], **bill_values,
        })
        bill.action_post()
        action = bill.with_user(self.manager).action_create_fapiao_request()
        request = self.env['qing.fapiao.request'].with_user(self.manager).browse(action['res_id'])
        request.seller_tax_id = 'SYNTHETIC-SELLER'
        request.line_ids.tax_code = '3040201010000000000'
        return request

    def _approve(self, request):
        request.action_submit()
        request.action_approve()

    def _queue(self, request, provider=None):
        provider = provider or self.provider
        with patch.object(type(provider), '_get_capabilities', return_value=CAPABILITIES):
            return request._create_provider_order(provider)

    def _result(self, order, **invoice_values):
        snapshot = order.snapshot
        invoice = {
            'number': 'SYNTHETIC-%s' % order.id, 'code': '', 'date': '2026-09-19',
            'amount_untaxed': snapshot['amount_untaxed'], 'amount_tax': snapshot['amount_tax'],
            'currency': snapshot['currency'], 'buyer': snapshot['buyer'], 'seller': snapshot['seller'],
            'files': [{'name': 'synthetic.xml', 'mimetype': 'application/xml',
                       'data': base64.b64encode(b'<synthetic>Not a tax invoice</synthetic>').decode()}],
        }
        invoice.update(invoice_values)
        return {'status': 'issued', 'external_id': 'SYNTHETIC-%s' % order.id, 'invoices': [invoice]}

    def _issue(self, order, **invoice_values):
        with patch.object(type(order.provider_id), '_get_capabilities', return_value=CAPABILITIES):
            self.assertEqual(order._prepare_attempt(), 'submit')
        self.assertEqual(order.state, 'unknown')
        order._apply_result(self._result(order, **invoice_values))

    def _allow_test_registration(self):
        self.env['ir.config_parameter'].sudo().set_bool('qing_fapiao.allow_test_registration', True)

    def test_finance_menu_hierarchy(self):
        tax_invoice_menu = self.env.ref('qing_account_fapiao.menu_fapiao_root')
        accounting_menu = self.env.ref('account.menu_finance_entries')
        interface_menu = self.env.ref('qing_fapiao.menu_fapiao_interface_root')

        self.assertEqual(tax_invoice_menu.parent_id, self.env.ref('account.menu_finance'))
        self.assertLess(tax_invoice_menu.sequence, accounting_menu.sequence)
        self.assertEqual(interface_menu.parent_id, tax_invoice_menu)

    def test_download_pdf_list_button_uses_arrow_without_tray(self):
        list_view = self.env.ref('qing_fapiao.view_qing_fapiao_order_list')
        arch = etree.fromstring(list_view.get_combined_arch().encode())
        button = arch.xpath("//button[@name='action_download_pdf']")[0]
        self.assertEqual(button.get('icon'), 'fa-arrow-down')

    def test_approved_request_queue_idempotent_and_structured_tax(self):
        request = self._request()
        with self.assertRaises(UserError):
            self._queue(request)
        self._approve(request)
        self.tax.amount = 27
        # The approved source snapshot survives later edits of tax configuration.
        order = self._queue(request)
        self.assertEqual(order.snapshot['lines'][0]['tax_rate'], '0.13')
        self.assertFalse(order.snapshot['lines'][0]['price_include'])
        self.assertEqual(order.qing_request_id, request)
        self.assertEqual(self._queue(request), order)
        self.assertEqual(len(request.provider_order_ids), 1)
        self.assertEqual(request.line_ids.move_id.qing_fapiao_reserved_untaxed, 100)
        other = self.provider.copy({'name': 'Another synthetic provider'})
        with self.assertRaises(UserError):
            self._queue(request, other)

    def test_native_single_tax_and_partial_rate_checks(self):
        request = self._request()
        request.line_ids.write({'amount_untaxed': 50, 'amount_tax': 13, 'quantity': 5})
        self._approve(request)
        with self.assertRaisesRegex(UserError, 'tax rate'):
            self._queue(request)
        self.assertFalse(request.provider_order_id)
        self.tax.amount_type = 'fixed'
        complex_request = self._request()
        self._approve(complex_request)
        with self.assertRaisesRegex(UserError, 'percentage'):
            self._queue(complex_request)

    def test_old_requests_keep_manual_path_and_snapshots_cannot_be_forged(self):
        request = self._request()
        line = request.line_ids
        for operation in (
            lambda: line.write({'provider_tax_snapshot': {'supported': True, 'tax_rate': '0'}}),
            lambda: line.write({'provider_tax_rate_display': '0%'}),
            lambda: line.with_context(default_provider_tax_snapshot={}).create({}),
            lambda: request.write({'provider_order_ids': [Command.clear()]}),
            lambda: request.with_context(default_provider_order_id=1).create({}),
            lambda: request.with_user(self.billing).action_select_provider(),
        ):
            with self.assertRaises(AccessError):
                operation()
        self.env.cr.execute('UPDATE qing_fapiao_request_line SET provider_tax_snapshot=NULL WHERE id=%s', [line.id])
        line.invalidate_recordset(['provider_tax_snapshot'])
        self._approve(request)
        with self.assertRaisesRegex(UserError, 'predates'):
            self._queue(request)
        request.write({
            'result_number': 'MANUAL-SYNTHETIC', 'result_date': '2026-09-19',
            'result_file': BinaryBytes(b'Synthetic manual evidence'), 'result_filename': 'synthetic.txt',
            'result_amount_untaxed': 100, 'result_amount_tax': 13, 'result_checked': True,
        })
        request.action_record_invoice()
        self.assertEqual(request.state, 'done')

    def test_queued_cancel_before_release_and_manual_bypass_blocked(self):
        request = self._request()
        self._approve(request)
        order = self._queue(request)
        request.cancel_reason = 'Synthetic cancellation'
        for operation in (
            request.action_cancel,
            request.action_record_invoice,
            lambda: request.write({'result_checked': True}),
            lambda: order.write({'qing_request_id': False}),
        ):
            with self.assertRaises((UserError, AccessError)):
                operation()
        order.action_cancel()
        self.assertEqual(request.line_ids.move_id.qing_fapiao_reserved_untaxed, 100)
        request.action_cancel()
        self.assertEqual(request.line_ids.move_id.qing_fapiao_reserved_untaxed, 0)

    def test_pending_unknown_and_issued_cannot_release_reservation(self):
        request = self._request()
        self._approve(request)
        order = self._queue(request)
        request.cancel_reason = 'Cannot cancel after send'
        with patch.object(type(self.provider), '_get_capabilities', return_value=CAPABILITIES):
            order._prepare_attempt()
        for state in ('unknown', 'pending'):
            order._apply_result({'status': state, 'external_id': 'SYNTHETIC', 'invoices': []})
            with self.assertRaises(UserError):
                request.action_cancel()
            with self.assertRaises(UserError):
                order.action_cancel()
            self.assertEqual(request.line_ids.move_id.qing_fapiao_reserved_tax, 13)
        result = self._result(order)
        result['external_id'] = 'SYNTHETIC'
        order._apply_result(result)
        with self.assertRaises(UserError):
            request.action_cancel()

    def test_definitive_rejection_allows_audited_release(self):
        request = self._request()
        self._approve(request)
        order = self._queue(request)
        with patch.object(type(self.provider), '_get_capabilities', return_value=CAPABILITIES):
            order._prepare_attempt()
        order._apply_result({'status': 'rejected', 'external_id': '', 'invoices': [], 'error_code': 'SYNTHETIC'})
        request.cancel_reason = 'Provider explicitly rejected the request'
        request.action_cancel()
        self.assertEqual(request.state, 'cancelled')
        self.assertEqual(request.line_ids.move_id.qing_fapiao_reserved_tax, 0)
        self.assertEqual(order.qing_request_id, request)

    def test_retry_after_definitive_rejection_creates_audited_task(self):
        request = self._request()
        self._approve(request)
        failed_order = self._queue(request)
        with patch.object(type(self.provider), '_get_capabilities', return_value=CAPABILITIES):
            failed_order._prepare_attempt()
        failed_order._apply_result({
            'status': 'rejected', 'external_id': '', 'invoices': [], 'error_code': 'SYNTHETIC',
        })

        with patch.object(type(self.provider), '_get_capabilities', return_value=CAPABILITIES):
            retry_order = request._retry_provider_order(failed_order)

        self.assertNotEqual(retry_order, failed_order)
        self.assertEqual(request.provider_order_id, retry_order)
        self.assertEqual(retry_order.state, 'queued')
        self.assertNotEqual(retry_order.request_key, failed_order.request_key)
        retry_event = retry_order.event_ids.filtered(
            lambda event: event.kind == 'queued' and event.payload.get('retry_of_task_id') == failed_order.id
        )
        self.assertEqual(len(retry_event), 1)

    def test_test_environment_blocked_by_default_and_registration_idempotent(self):
        request = self._request()
        self._approve(request)
        order = self._queue(request)
        self._issue(order)
        self.env['ir.config_parameter'].sudo().set_bool('qing_fapiao.allow_test_registration', False)
        self.assertEqual(request.provider_order_state, 'issued')
        self.assertEqual(request.provider_registration_state, 'pending')
        self.assertFalse(order.with_context(allow_test_registration=True).action_record_result())
        self.assertEqual(order.registration_state, 'error')
        self.assertEqual(request.provider_registration_state, 'error')
        self.assertEqual(request.state, 'approved')
        self.assertEqual(request.line_ids.move_id.qing_fapiao_reserved_tax, 13)
        before_moves = self.env['account.move'].search_count([])
        self._allow_test_registration()
        order.action_record_result()
        self.assertEqual(order.state, 'issued')
        self.assertEqual(order.registration_state, 'done')
        self.assertEqual(request.provider_registration_state, 'done')
        self.assertEqual(request.state, 'done')
        self.assertEqual(request.fapiao_id.state, 'registered')
        self.assertEqual(request.fapiao_id.allocation_ids.state, 'confirmed')
        self.assertEqual(request.line_ids.move_id.qing_fapiao_reserved_tax, 0)
        self.assertEqual(request.line_ids.move_id.qing_fapiao_request_remaining_tax, 0)
        ticket = request.fapiao_id
        self.assertTrue(order.action_record_result())
        self.assertEqual(request.fapiao_id, ticket)
        self.assertEqual(self.env['account.move'].search_count([]), before_moves)
        self.assertEqual(order.attempt_count, 1)

    def test_registration_failure_is_atomic_and_retry_does_not_reissue(self):
        self._allow_test_registration()
        request = self._request()
        self._approve(request)
        order = self._queue(request)
        self._issue(order)
        allocation_type = type(self.env['qing.fapiao.allocation'])
        before = self.env['qing.fapiao'].search_count([])
        with patch.object(allocation_type, 'action_confirm', side_effect=UserError('Synthetic retryable registration error')):
            self.assertFalse(order.action_record_result())
        self.assertEqual(order.state, 'issued')
        self.assertEqual(order.registration_state, 'error')
        self.assertEqual(request.state, 'approved')
        self.assertFalse(request.fapiao_id)
        self.assertEqual(self.env['qing.fapiao'].search_count([]), before)
        self.assertEqual(request.line_ids.move_id.qing_fapiao_reserved_untaxed, 100)
        self.assertFalse(request.result_number)
        order.action_record_result()
        self.assertEqual(request.state, 'done')
        self.assertEqual(order.attempt_count, 1)

    def test_provider_legal_seller_name_registers_by_matching_tax_id(self):
        self._allow_test_registration()
        request = self._request()
        self._approve(request)
        order = self._queue(request)
        legal_name = 'Synthetic registered legal seller'
        seller = {**order.snapshot['seller'], 'name': legal_name}
        self._issue(order, seller=seller)
        order._write_internal({'registration_state': 'error', 'registration_error': 'Earlier name mismatch'})

        order.action_record_result()

        self.assertEqual(order.registration_state, 'done')
        self.assertFalse(order.registration_error)
        self.assertEqual(request.state, 'done')
        self.assertEqual(request.fapiao_id.seller_name, legal_name)
        self.assertEqual(request.fapiao_id.seller_tax_id, order.snapshot['seller']['tax_id'])
        self.assertEqual(order.attempt_count, 1)
        self.assertFalse(order.pending_action)

    def test_original_file_required_and_multiple_invoices_held_for_review(self):
        self._allow_test_registration()
        request = self._request()
        self._approve(request)
        order = self._queue(request)
        self._issue(order, files=[])
        self.assertFalse(order.action_record_result())
        self.assertEqual(order.registration_state, 'error')
        order._apply_result(self._result(order))
        order.action_record_result()
        self.assertEqual(request.state, 'done')
        second = self._request()
        self._approve(second)
        multiple = self._queue(second)
        with patch.object(type(self.provider), '_get_capabilities', return_value=CAPABILITIES):
            multiple._prepare_attempt()
        result = self._result(multiple)
        invoice = result['invoices'][0]
        invoice['amount_untaxed'], invoice['amount_tax'] = '50.00', '6.50'
        other = deepcopy(invoice)
        other['number'] += '-2'
        result['invoices'].append(other)
        multiple._apply_result(result)
        self.assertFalse(multiple.action_record_result())
        self.assertEqual(second.state, 'approved')
        self.assertEqual(second.line_ids.move_id.qing_fapiao_reserved_untaxed, 100)

    def test_provider_company_and_subject_and_real_form(self):
        request = self._request()
        self._approve(request)
        self.provider.seller_tax_id = 'OTHER-SELLER'
        with self.assertRaises(UserError):
            self._queue(request)
        self.provider.seller_tax_id = 'SYNTHETIC-SELLER'
        action = request.action_select_provider()
        wizard_model = self.env[action['res_model']].with_user(self.manager).with_context(action['context'])
        with Form(wizard_model) as form:
            form.provider_id = self.provider
        wizard = form.record
        with patch.object(type(self.provider), '_get_capabilities', return_value=CAPABILITIES):
            opened = wizard.action_queue()
            order = request.provider_order_id
            self.provider.seller_tax_id = 'UPDATED-SELLER'
            order._check_submit_allowed()
        self.assertEqual(opened['res_id'], request.provider_order_id.id)
        self.assertEqual(opened['res_model'], 'qing.fapiao.order')
        self.assertEqual(order.provider_snapshot['seller_tax_id'], 'SYNTHETIC-SELLER')
        request._validate_provider_order(order)
        with Form(request) as form:
            form.cancel_reason = 'Synthetic native form check'

    def test_company_scope_and_billing_user_task_access(self):
        request = self._request()
        self._approve(request)
        other = self.env['res.company'].create({'name': 'Other synthetic connector company'})
        self.manager.company_ids = [Command.link(other.id)]
        foreign = self.env['qing.fapiao.provider'].with_user(self.manager).with_company(other).create({
            'name': 'Other company provider', 'company_id': other.id, 'seller_tax_id': 'SYNTHETIC-SELLER',
            'code': 'none', 'environment': 'test', 'enabled': True,
        })
        with self.assertRaises((UserError, AccessError)):
            self._queue(request, foreign.with_company(self.env.company))
        order = self._queue(request).with_user(self.billing)
        for operation in (lambda: order.read(['snapshot']), order.action_query, order.action_record_result):
            with self.assertRaises(AccessError):
                operation()
        unbound = self._request()
        self._approve(unbound)
        with self.assertRaises(AccessError):
            self._queue(unbound.with_context(default_qing_request_id=request.id))

    def test_payload_precision_for_many_fractional_lines(self):
        request = self._request(count=50)
        request.line_ids.write({'amount_untaxed': 1.00, 'amount_tax': 0.13, 'quantity': 0.1})
        self._approve(request)
        order = self._queue(request)
        self.assertEqual(order.snapshot['amount_tax'], '6.50')
        self.assertEqual(order.snapshot['amount_total'], '56.50')

    def test_native_document_tax_mode_controls_snapshot(self):
        included = self._request(document_tax_mode='tax_included')
        self.assertTrue(included.line_ids.provider_tax_snapshot['price_include'])
        self.tax.price_include_override = 'tax_excluded'
        excluded = self._request(document_tax_mode='tax_included')
        self.assertFalse(excluded.line_ids.provider_tax_snapshot['price_include'])

    def test_50_and_150_line_edit_and_queue_scaling(self):
        counts = []
        for size in (50, 150):
            request = self._request(count=size)
            self.env.flush_all()
            with self.cr.savepoint() as savepoint:
                before = self.cr.sql_log_count
                request.line_ids[0].description = 'Synthetic single-line edit'
                self.env.flush_all()
                edit_queries = self.cr.sql_log_count - before
                savepoint.rollback()
            self.env.invalidate_all()
            self.assertEqual(request.line_ids[0].description, 'Synthetic item')
            self._approve(request)
            self.env.flush_all()
            with self.cr.savepoint() as savepoint:
                before = self.cr.sql_log_count
                order = self._queue(request)
                self.env.flush_all()
                queue_queries = self.cr.sql_log_count - before
                self.assertEqual(len(order.snapshot['lines']), size)
                savepoint.rollback()
            self.env.invalidate_all()
            self.assertFalse(request.provider_order_id)
            counts.append((edit_queries, queue_queries))
            _logger.info('Fapiao connector performance: %s lines, single edit %s SQL, queue %s SQL',
                         size, edit_queries, queue_queries)
        self.assertLessEqual(counts[1][0], counts[0][0] + 8)
        self.assertLessEqual(counts[1][1], counts[0][1] + 40)

    def test_document_tax_mode_and_tax_engine_data_locked_after_submission(self):
        request = self._request()
        self._approve(request)
        bill = request.line_ids.move_id.with_user(self.billing)
        with self.assertRaises(UserError):
            bill.write({'document_tax_mode': 'tax_included'})
        with self.assertRaises(UserError):
            bill.invoice_line_ids.write({'extra_tax_data': {'computation_key': 'global_discount'}})
        self.assertEqual(bill.document_tax_mode, 'tax_excluded')
        self.assertFalse(bill.invoice_line_ids.extra_tax_data)

    def test_reset_draft_cannot_detach_provider_task(self):
        request = self._request()
        self._approve(request)
        order = self._queue(request)
        with self.assertRaises(UserError):
            request.action_reset_draft()
        order.action_cancel()
        request.cancel_reason = 'Synthetic cancellation'
        request.action_cancel()
        with self.assertRaises(UserError):
            request.action_reset_draft()
        self.assertEqual(request.provider_order_id, order)

    def test_open_linked_request_action(self):
        request = self._request()
        self._approve(request)
        order = self._queue(request)
        action = order.action_open_qing_request()
        self.assertEqual(action['res_model'], 'qing.fapiao.request')
        self.assertEqual(action['res_id'], request.id)
        self.assertEqual(action['views'], [(self.env.ref('qing_account_fapiao.view_fapiao_request_form').id, 'form')])
        self.assertEqual(request.state, 'approved')
