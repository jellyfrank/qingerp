import base64
from copy import deepcopy
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import HttpCase, TransactionCase, tagged

from ..protocol import ProtocolError, validate_payload, validate_result

CAPABILITIES = {
    'issue_blue': True, 'query': True, 'documents': True, 'callback': True,
    'invoice_types': ['normal', 'special'], 'currencies': ['CNY'],
}


@tagged('post_install', '-at_install')
class TestFapiaoCore(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Provider = cls.env['qing.fapiao.provider']
        cls.Order = cls.env['qing.fapiao.order']
        cls.provider = cls.Provider.create({
            'name': 'Synthetic core test provider', 'seller_tax_id': 'SYNTHETIC-SELLER',
            'environment': 'test', 'enabled': True,
        })
        cls.manager = cls.env['res.users'].create({
            'name': 'Synthetic invoice interface manager', 'login': 'synthetic_fapiao_manager',
            'company_id': cls.env.company.id, 'company_ids': [Command.set(cls.env.company.ids)],
            'group_ids': [Command.set(cls.env.ref('qing_fapiao.group_fapiao_manager').ids)],
        })
        cls.basic_user = cls.env['res.users'].create({
            'name': 'Synthetic basic user', 'login': 'synthetic_fapiao_basic',
            'company_id': cls.env.company.id, 'company_ids': [Command.set(cls.env.company.ids)],
            'group_ids': [Command.set(cls.env.ref('base.group_user').ids)],
        })

    def setUp(self):
        super().setUp()
        patcher = patch.object(type(self.provider), '_get_capabilities', return_value=deepcopy(CAPABILITIES))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _payload(self, key='core-test'):
        return {
            'schema_version': '1.0', 'request_key': key, 'color': 'blue',
            'invoice_type': 'special', 'currency': 'CNY',
            'buyer': {'name': 'Synthetic buyer', 'tax_id': 'SYNTHETIC-BUYER'},
            'seller': {'name': 'Synthetic seller', 'tax_id': 'SYNTHETIC-SELLER'},
            'lines': [{
                'line_key': 'line-1', 'description': 'Synthetic goods', 'quantity': '2',
                'amount_untaxed': '100.00', 'amount_tax': '13.00', 'tax_rate': '0.13',
                'price_include': False, 'tax_code': 'SYNTHETIC-CODE',
            }],
            'amount_untaxed': '100.00', 'amount_tax': '13.00', 'amount_total': '113.00',
        }

    def _order(self, key='core-test'):
        return self.Order._create_order(self.provider, self._payload(key))

    def _issued(self, order):
        return {
            'status': 'issued', 'external_id': 'SYNTHETIC-EXTERNAL',
            'invoices': [{
                'number': 'SYNTHETIC-INVOICE', 'code': '', 'date': '2026-09-19',
                'currency': order.snapshot['currency'],
                'buyer': deepcopy(order.snapshot['buyer']), 'seller': deepcopy(order.snapshot['seller']),
                'amount_untaxed': '100.00', 'amount_tax': '13.00',
                'files': [{'name': 'synthetic.txt', 'mimetype': 'text/plain',
                           'data': base64.b64encode(b'Synthetic test document; not a tax invoice.').decode()}],
            }],
        }

    def test_pdf_download_and_chatter_preserve_immutable_task(self):
        order = self._order('pdf-chatter')
        self.assertFalse(order.has_pdf)
        with self.assertRaises(UserError):
            order.action_download_pdf()
        order._prepare_attempt()
        result = self._issued(order)
        content = b'%PDF-1.4 synthetic test'
        result['invoices'][0]['files'] = [{'name': 'invoice.pdf', 'mimetype': 'application/pdf',
                                          'data': base64.b64encode(content).decode()}]
        order._apply_result(result)
        self.assertTrue(order.has_pdf)
        self.assertEqual(order.pdf_file.content, content)
        self.assertEqual(order.pdf_filename, 'invoice.pdf')
        self.assertIn('/pdf_file?download=true', order.with_user(self.manager).action_download_pdf()['url'])
        note = order.with_user(self.manager).message_post(body='Synthetic internal note', subtype_xmlid='mail.mt_note')
        self.assertEqual(note.res_id, order.id)
        self.assertTrue(order.message_ids)
        with self.assertRaises(AccessError):
            order.with_user(self.manager).write({'result': {}})

    def test_protocol_decimal_and_sum_guards(self):
        payload = self._payload()
        self.assertEqual(validate_payload(payload), payload)
        for value in ('NaN', 'Infinity', '-1', '1e2', 100, 100.0):
            invalid = deepcopy(payload)
            invalid['lines'][0]['amount_untaxed'] = value
            with self.assertRaises(ProtocolError):
                validate_payload(invalid)
        for field, value in [('amount_untaxed', '101.00'), ('amount_tax', '12.00'), ('amount_total', '112.00')]:
            invalid = deepcopy(payload)
            invalid[field] = value
            with self.assertRaises(ProtocolError):
                validate_payload(invalid)

    def test_protocol_requires_tax_rate_and_price_mode(self):
        for field, value in [('tax_rate', '1.13'), ('price_include', 'false'), ('quantity', '0'), ('tax_code', '')]:
            payload = self._payload()
            payload['lines'][0][field] = value
            with self.assertRaises(ProtocolError):
                validate_payload(payload)

    def test_creation_idempotence_and_provider_binding(self):
        order = self._order()
        self.assertEqual(self._order(), order)
        changed = self._payload()
        changed['buyer']['name'] = 'Changed buyer'
        with self.assertRaises(ValidationError):
            self.Order._create_order(self.provider, changed)
        other_provider = self.Provider.create({
            'name': 'Other synthetic provider', 'seller_tax_id': 'SYNTHETIC-SELLER', 'enabled': True,
        })
        with self.assertRaises(ValidationError):
            self.Order._create_order(other_provider, self._payload())

    def test_creation_scrubs_untrusted_defaults(self):
        Order = self.Order.with_context(
            default_external_id='FORGED', default_submitted_at='2026-01-01',
            default_registration_state='done', default_state='issued',
            default_result={'status': 'issued'}, default_attempt_count=99,
            default_event_ids=[Command.create({'kind': 'result', 'payload': {'forged': True}})],
        )
        order = Order._create_order(self.provider, self._payload())
        self.assertEqual(order.state, 'queued')
        self.assertEqual(order.registration_state, 'pending')
        self.assertFalse(order.submitted_at)
        self.assertFalse(order.external_id)
        self.assertFalse(order.result)
        self.assertEqual(order.attempt_count, 0)
        self.assertEqual(len(order.event_ids), 1)
        self.assertEqual(order.event_ids.kind, 'queued')

    def test_provider_tax_id_can_change_after_order_but_routing_is_frozen(self):
        order = self._order()
        self.provider.write({'seller_tax_id': 'OTHER'})
        self.assertEqual(order.provider_snapshot['seller_tax_id'], 'SYNTHETIC-SELLER')
        order._check_provider_identity()
        order._check_submit_allowed()
        for values in ({'company_id': self.env.company.id + 1},
                       {'environment': 'production'}, {'code': 'none'}):
            with self.assertRaises(UserError):
                self.provider.write(values)
        self.provider.write({'enabled': False})
        with self.assertRaises(UserError):
            self._order('new-order')

    def test_provider_checks_seller_and_capabilities(self):
        payload = self._payload()
        payload['seller']['tax_id'] = 'OTHER'
        with self.assertRaises(ValidationError):
            self.Order._create_order(self.provider, payload)
        with patch.object(type(self.provider), '_get_capabilities', return_value={}):
            with self.assertRaises(UserError):
                self._order()

    def test_public_crud_and_audit_protected(self):
        order = self._order().with_user(self.manager)
        with self.assertRaises(AccessError):
            order.write({'state': 'issued'})
        with self.assertRaises(AccessError):
            order.unlink()
        with self.assertRaises(AccessError):
            self.Order.with_user(self.manager).create({'request_key': 'forged'})
        event = order.event_ids[0]
        with self.assertRaises(AccessError):
            event.write({'payload': {'forged': True}})
        with self.assertRaises(AccessError):
            event.unlink()
        with self.assertRaises(AccessError):
            self.env['qing.fapiao.event'].with_user(self.manager).create({'order_id': order.id})

    def test_basic_user_cannot_read_or_trigger(self):
        order = self._order().with_user(self.basic_user)
        with self.assertRaises(AccessError):
            order.read(['request_key'])
        with self.assertRaises(AccessError):
            order.action_cancel()

    def test_company_isolation_and_active_company(self):
        other_company = self.env['res.company'].create({'name': 'Synthetic isolated company'})
        other_provider = self.Provider.with_company(other_company).create({
            'name': 'Isolated provider', 'seller_tax_id': 'SYNTHETIC-SELLER', 'enabled': True,
        })
        other_order = self.Order.with_company(other_company)._create_order(other_provider, self._payload())
        with self.assertRaises(AccessError):
            other_order.with_user(self.manager).read(['request_key'])
        self.manager.company_ids = [Command.link(other_company.id)]
        other_order = other_order.with_user(self.manager).with_context(allowed_company_ids=[self.env.company.id, other_company.id])
        with self.assertRaises(AccessError):
            other_order.action_cancel()

    def test_cancel_only_before_first_attempt(self):
        order = self._order()
        order.action_cancel()
        self.assertEqual(order.state, 'cancelled')
        self.assertFalse(order.pending_action)
        with self.assertRaises(UserError):
            order.action_queue()
        sent = self._order('sent')
        self.assertEqual(sent._prepare_attempt(), 'submit')
        self.assertEqual(sent.state, 'unknown')
        with self.assertRaises(UserError):
            sent.action_cancel()
        with self.assertRaises(UserError):
            sent.action_queue()

    def test_unknown_outcome_is_query_only(self):
        order = self._order()
        with patch.object(type(self.provider), '_submit_invoice') as submit:
            self.assertEqual(order._prepare_attempt(), 'submit')
            submit.assert_not_called()
        order.action_query()
        self.assertEqual(order._prepare_attempt(), 'query')
        self.assertEqual(order.attempt_count, 2)
        self.assertEqual(order.state, 'unknown')

    def test_result_validates_party_currency_and_amounts(self):
        order = self._order()
        result = self._issued(order)
        validate_result(result, order.snapshot)
        for key, value in [('currency', 'USD'), ('amount_tax', '12.00'), ('seller', {'name': 'Elsewhere', 'tax_id': 'OTHER'}), ('buyer', {'name': 'Elsewhere', 'tax_id': 'OTHER'})]:
            invalid = deepcopy(result)
            invalid['invoices'][0][key] = value
            with self.assertRaises(ProtocolError):
                validate_result(invalid, order.snapshot)

    def test_unsent_result_and_issued_regression_are_blocked(self):
        order = self._order()
        with self.assertRaises(ValidationError):
            order._apply_result(self._issued(order))
        order._prepare_attempt()
        order._apply_result(self._issued(order))
        self.assertEqual(order.state, 'issued')
        self.assertFalse(order._apply_result({'status': 'pending'}))
        self.assertEqual(order.state, 'issued')
        changed = self._issued(order)
        changed['invoices'][0]['number'] = 'ANOTHER-INVOICE'
        with self.assertRaises(ValidationError):
            order._apply_result(changed)
        self.assertEqual(order.result['invoices'][0]['number'], 'SYNTHETIC-INVOICE')

    def test_duplicate_issued_result_keeps_documents(self):
        order = self._order()
        order._prepare_attempt()
        order._apply_result(self._issued(order))
        without_files = self._issued(order)
        without_files['invoices'][0]['files'] = []
        order._apply_result(without_files)
        self.assertTrue(order.result['invoices'][0]['files'])

    def test_registration_rollback_and_retry_are_idempotent(self):
        order = self._order()
        order._prepare_attempt()
        order._apply_result(self._issued(order))

        def broken_hook(record):
            record._write_internal({'external_id': 'MUST-ROLLBACK'})
            raise ValueError('Synthetic unexpected registration failure')

        with patch.object(type(order), '_record_business_result', broken_hook):
            self.assertFalse(order.action_record_result())
        self.assertEqual(order.external_id, 'SYNTHETIC-EXTERNAL')
        self.assertEqual(order.state, 'issued')
        self.assertEqual(order.registration_state, 'error')
        with patch.object(type(order), '_record_business_result', return_value=True) as registration:
            order.action_record_result()
            order.action_record_result()
            registration.assert_called_once()
        self.assertEqual(order.registration_state, 'done')

    def test_unverified_callback_does_not_create_event(self):
        order = self._order()
        before = len(order.event_ids)
        with patch.object(type(self.provider), '_verify_notification', side_effect=ValidationError('Invalid signature')):
            with self.assertRaises(ValidationError):
                self.provider._process_notification({}, b'unverified')
        self.assertEqual(len(order.event_ids), before)

    def test_verified_callback_dedupes_and_processes_later(self):
        order = self._order()
        order._prepare_attempt()
        notification = {'event_id': 'event-1', 'request_key': order.request_key, 'result': self._issued(order)}
        with patch.object(type(self.provider), '_verify_notification', return_value=notification):
            event = self.provider._process_notification({}, b'verified')
            self.assertEqual(self.provider._process_notification({}, b'verified'), event)
        self.assertEqual(order.state, 'unknown')
        self.assertFalse(event.processed)
        order._process_notifications()
        self.assertTrue(event.processed)
        self.assertEqual(order.state, 'issued')
        changed = deepcopy(notification)
        changed['result']['invoices'][0]['number'] = 'OTHER'
        with patch.object(type(self.provider), '_verify_notification', return_value=changed):
            with self.assertRaises(ValidationError):
                self.provider._process_notification({}, b'conflict')

    def test_disabled_provider_can_query_existing_order(self):
        order = self._order()
        order._prepare_attempt()
        self.provider.enabled = False
        order.action_query()
        self.assertEqual(order._prepare_attempt(), 'query')
        order._apply_result(self._issued(order))
        self.assertEqual(order.state, 'issued')

    def test_cancel_processing_preserves_unknown_and_late_results(self):
        order = self._order('stop-query')
        order._prepare_attempt()
        order.with_user(self.manager).action_cancel_processing()
        self.assertTrue(order.processing_cancelled)
        self.assertEqual(order.state, 'unknown')
        self.assertEqual(order.processing_cancelled_by, self.manager)
        self.assertFalse(order.pending_action)
        self.assertFalse(order._prepare_attempt())
        order._apply_result({'status': 'pending'})
        self.assertFalse(order.pending_action)
        self.assertFalse(order.next_attempt_at)
        order.action_query()
        self.assertFalse(order.processing_cancelled)
        self.assertEqual(order._prepare_attempt(), 'query')
        order.action_cancel_processing()
        order._apply_result(self._issued(order))
        self.assertEqual(order.state, 'issued')
        self.assertFalse(order.processing_cancelled)
        with self.assertRaises(UserError):
            order.action_cancel_processing()

    def test_cancel_processing_unsent_and_permissions(self):
        order = self._order('stop-unsent')
        with self.assertRaises(AccessError):
            order.with_user(self.basic_user).action_cancel_processing()
        order.with_user(self.manager).action_cancel_processing()
        self.assertEqual(order.state, 'cancelled')
        self.assertFalse(order.pending_action)

    def test_delete_cancelled_task_admin_only_and_cascades_events(self):
        order = self._order('delete-cancelled')
        order.action_cancel()
        events = order.event_ids
        with self.assertRaises(AccessError):
            order.with_user(self.manager).unlink()
        with self.assertRaises(AccessError):
            events.with_user(self.manager).unlink()
        admin = self.manager.copy({'login': 'synthetic_delete_admin',
                                  'group_ids': [Command.set(self.manager.group_ids.ids + [self.env.ref('base.group_system').id])]})
        order.with_user(admin).unlink()
        self.assertFalse(order.exists())
        self.assertFalse(events.exists())

    def test_delete_definitively_rejected_task(self):
        order = self._order('delete-rejected')
        order._prepare_attempt()
        order._apply_result({'status': 'rejected', 'error_code': 'E0068', 'message': 'Invalid goods code'})
        events = order.event_ids
        with self.assertRaises(AccessError):
            order.with_user(self.manager).unlink()
        order.action_delete_cancelled()
        self.assertFalse(order.exists())
        self.assertFalse(events.exists())

    def test_delete_mixed_batch_and_cancelled_sent_task(self):
        cancelled = self._order('delete-batch-cancelled')
        cancelled.action_cancel()
        pending = self._order('delete-batch-pending')
        with self.assertRaises(UserError):
            (cancelled | pending).unlink()
        self.assertTrue(cancelled.exists())
        pending._prepare_attempt()
        pending.action_cancel_processing()
        self.assertEqual(pending.task_state, 'cancelled')
        self.assertEqual(pending.state, 'unknown')
        pending.unlink()
        self.assertFalse(pending.exists())
        pending = self._order('cannot-delete-issued')
        pending._prepare_attempt()
        pending._apply_result(self._issued(pending))
        with self.assertRaises(UserError):
            pending.unlink()

    def test_delete_waits_for_inflight_worker(self):
        from ..models.fapiao_order import LOCK_NAMESPACE
        order = self._order('delete-inflight')
        order._prepare_attempt()
        order.action_cancel_processing()
        with self.env.registry.cursor() as worker_cr:
            worker_cr.execute('SELECT pg_advisory_xact_lock(%s, %s)', [LOCK_NAMESPACE, order.id])
            with self.assertRaises(UserError):
                order.unlink()
        self.assertTrue(order.exists())
        order.unlink()
        self.assertFalse(order.exists())


@tagged('post_install', '-at_install')
class TestFapiaoPDFHttp(HttpCase):
    def test_authenticated_pdf_download(self):
        provider = self.env['qing.fapiao.provider'].create({
            'name': 'PDF HTTP synthetic provider', 'code': 'none',
            'seller_tax_id': 'SYNTHETIC-SELLER',
        })
        payload = TestFapiaoCore._payload(self, 'pdf-http')
        with patch.object(type(provider), '_check_order', return_value=True):
            order = self.env['qing.fapiao.order']._create_order(provider, payload)
        content = b'%PDF-1.4 synthetic HTTP test'
        result = TestFapiaoCore._issued(self, order)
        result['invoices'][0]['files'] = [{'name': 'invoice.pdf', 'mimetype': 'application/pdf',
                                          'data': base64.b64encode(content).decode()}]
        order._write_internal({'state': 'unknown'})
        order._apply_result(result)
        self.env.ref('base.user_admin').group_ids += self.env.ref('qing_fapiao.group_fapiao_manager')
        self.authenticate('admin', 'admin')
        response = self.url_open(order.action_download_pdf()['url'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, content)
        self.assertIn('attachment', response.headers['Content-Disposition'])
        self.authenticate(None, None)
        response = self.url_open(order.action_download_pdf()['url'])
        self.assertNotEqual(response.content, content)
