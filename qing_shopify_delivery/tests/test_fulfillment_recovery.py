from datetime import timedelta
from unittest import TestCase
from unittest.mock import patch

from ..models import store as delivery_service

from odoo import Command, fields
from odoo.addons.qing_channel.errors import ChannelReviewRequired, DeferredChannelError
from odoo.addons.qing_channel.models import action as action_queue, delivery as delivery_queue, request as request_queue
from odoo.addons.qing_shopify.models.store import CONNECTION_QUERY, REQUIRED_SCOPES
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFulfillmentRecovery(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.company
        warehouse = cls.env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
        cls.store = cls.env['qing.channel.store'].create({
            'name': 'Shipping Pilot', 'platform': 'shopify', 'company_id': company.id,
            'warehouse_id': warehouse.id, 'shopify_domain': 'delivery-2635.myshopify.com',
            'shopify_health': 'connected', 'shopify_location': 'gid://shopify/Location/1',
        })
        order = cls.env['qing.channel.order'].create({
            'store_id': cls.store.id, 'external_id': 'gid://shopify/Order/10', 'external_name': '#10',
            'snapshot': {'cancelledAt': False},
        })
        picking = cls.env['stock.picking'].create({
            'company_id': company.id, 'picking_type_id': warehouse.out_type_id.id,
            'location_id': warehouse.lot_stock_id.id,
            'location_dest_id': cls.env.ref('stock.stock_location_customers').id,
        })
        cls.shipment = cls.env['qing.shopify.shipment'].create({
            'order_id': order.id, 'picking_id': picking.id, 'tracking': 'TRACK-2635',
        })
        action = cls.env['qing.channel.action']._ensure_action(
            cls.store, 'fulfillment:1:1', 'fulfillment', order_id=order.id)
        cls.request = cls.env['qing.channel.request']._prepare_request(action, 'fulfillment_create',
            f'fulfillment:{order.id}', {
                'shipment': cls.shipment.id, 'version': 1, 'previous_ids': [], 'assignments': [],
                'tracking': {'number': 'TRACK-2635'}, 'line_quantities': {'gid://shopify/LineItem/11': 2},
            })
        cls.request.write({'state': 'uncertain', 'first_sent_at': fields.Datetime.now()})

    def test_tracking_edit_of_a_pre_cutover_picking_does_not_resend_that_shipment(self):
        store = self.store
        order = self.shipment.order_id
        order.historical = True
        store.cutover_at = fields.Datetime.now()
        warehouse = store.warehouse_id
        product = self.env['product.product'].create({
            'name': 'Opening shipment', 'type': 'consu', 'is_storable': True,
        })
        customer = self.env['res.partner'].create({'name': 'Opening shipment customer'})
        self.env['stock.quant']._update_available_quantity(product, warehouse.lot_stock_id, 2)
        sale = self.env['sale.order'].create({
            'partner_id': customer.id, 'warehouse_id': warehouse.id,
            'order_line': [Command.create({'product_id': product.id, 'product_uom_qty': 2})],
        })
        sale.action_confirm()
        first = sale.picking_ids
        first.action_assign()
        first.move_ids.quantity = 1
        first.move_ids.picked = True
        first.with_context(skip_backorder=True).button_validate()
        first.date_done = store.cutover_at - timedelta(days=1)
        self.assertEqual(first.state, 'done')

        self.env['qing.channel.order.line'].create({
            'order_id': order.id, 'external_id': 'gid://shopify/LineItem/11',
            'product_id': product.id, 'quantity': 2, 'sale_line_id': sale.order_line.id,
        })
        Shipment = self.env['qing.shopify.shipment']
        first.carrier_tracking_ref = 'HISTORIC-TRACKING-CORRECTION'
        self.assertFalse(Shipment.search([('picking_id', '=', first.id)]))

        remaining = sale.picking_ids - first
        remaining.action_assign()
        remaining.move_ids.picked = True
        remaining.with_context(skip_backorder=True).button_validate()
        self.assertEqual(remaining.state, 'done')
        self.assertGreaterEqual(remaining.date_done, store.cutover_at)
        new_shipment = Shipment.search([('picking_id', '=', remaining.id)])
        self.assertEqual(new_shipment.order_id, order)
        new_action = self.env['qing.channel.action'].search([
            ('order_id', '=', order.id), ('key', '=', f'fulfillment:{new_shipment.id}:{new_shipment.version}'),
        ])
        self.assertEqual(new_action.kind, 'fulfillment')
        self.assertEqual(new_action.data['shipment'], new_shipment.id)

    def test_pre_cutover_legacy_requests_are_only_read_if_they_might_have_been_sent(self):
        shipment = self.shipment
        order = shipment.order_id
        order.historical = True
        self.store.cutover_at = fields.Datetime.now()
        shipment.picking_id.write({'state': 'done', 'date_done': self.store.cutover_at - timedelta(days=1)})
        action = self.request.action_id
        action.data = {'shipment': shipment.id, 'version': shipment.version}
        self.request.write({'state': 'ready', 'first_sent_at': False})
        with (patch.object(type(self.store), '_shopify_shipment_lines', return_value=self.request.payload),
              patch.object(type(self.store), '_shopify_client') as client,
              patch.object(self.env.cr, 'commit')):
            self.assertEqual(self.store._execute_channel_action(action), 'obsolete')
            self.assertEqual(self.store._execute_channel_request(self.request, recovering=False), 'obsolete')
            client.assert_not_called()

        self.request.write({'state': 'uncertain', 'first_sent_at': fields.Datetime.now()})
        observed = {
            'fulfillments': [{
                'id': 'gid://shopify/Fulfillment/90', 'status': 'SUCCESS',
                'location': {'id': self.store.shopify_location}, 'trackingInfo': [{'number': 'TRACK-2635'}],
                'fulfillmentLineItems': {'nodes': [
                    {'quantity': 2, 'lineItem': {'id': 'gid://shopify/LineItem/11'}},
                ]},
            }],
        }
        with (patch.object(delivery_service, 'fetch_fulfillment_snapshot', return_value=observed) as read,
              patch.object(type(self.store), '_shopify_client') as client):
            self.assertEqual(self.store._execute_channel_request(self.request, recovering=True), 'satisfied')
            read.assert_called_once()
            client.return_value.query.assert_not_called()
        self.assertEqual(shipment.remote_id, 'gid://shopify/Fulfillment/90')

    def test_timeout_before_partial_shipment_is_visible_never_resends(self):
        location = self.store.shopify_location
        order = {'fulfillments': [], 'fulfillmentOrders': {'nodes': [
            {'lineItems': {'nodes': [{'remainingQuantity': 10}]}}
        ]}}
        calls = []

        class Client:
            def query(self, document, _variables):
                calls.append(document)
                return {'order': order}

        with patch.object(delivery_service, 'fetch_fulfillment_snapshot', side_effect=lambda _client, _id: order) as read, patch.object(
            type(self.store), '_shopify_client', return_value=Client()
        ):
            with self.assertRaisesRegex(DeferredChannelError, 'shopify_fulfillment_outcome_unknown'):
                self.store._send_shopify_fulfillment(self.request, self.shipment, recovering=True)
            self.assertFalse(self.shipment.remote_id)
            order['fulfillments'] = [{
                'id': 'gid://shopify/Fulfillment/1', 'status': 'SUCCESS', 'location': {'id': location},
                'trackingInfo': [{'number': 'TRACK-2635'}],
                'fulfillmentLineItems': {'nodes': [
                    {'quantity': 2, 'lineItem': {'id': 'gid://shopify/LineItem/11'}}
                ]},
            }]
            self.assertEqual(self.store._send_shopify_fulfillment(
                self.request, self.shipment, recovering=True), 'satisfied')
        self.assertEqual(read.call_count, 2)
        self.assertFalse(calls)
        self.assertEqual(self.shipment.remote_id, 'gid://shopify/Fulfillment/1')

    def test_paid_order_cancellation_after_send_marker_blocks_fulfillment(self):
        self.request.write({'state': 'ready', 'first_sent_at': False})
        shipment = self.shipment
        shipment.picking_id.state = 'done'
        mark = type(self.request)._mark_before_send

        def cancel_after_recording_started(request):
            mark(request)
            shipment.order_id.snapshot = {'cancelledAt': '2026-11-18T12:00:00Z'}

        sent = {'fulfillmentCreate': {'fulfillment': {'id': 'gid://shopify/Fulfillment/1'}, 'userErrors': []}}
        with (patch.object(self.env.cr, 'commit'),
              patch.object(type(self.request), '_mark_before_send', cancel_after_recording_started),
              patch.object(type(self.store), '_shopify_shipment_lines', return_value=self.request.payload),
              patch.object(type(self.store), '_shopify_client') as client):
            client.return_value.query.return_value = sent
            with TestCase().assertRaisesRegex(ChannelReviewRequired, 'shopify_shipment_requires_review'):
                self.store._execute_channel_request(self.request, recovering=False)
            client.assert_not_called()
        self.assertFalse(shipment.remote_id)
        self.assertTrue(self.request.confirmed_rejection)

    def test_shipment_uses_the_native_carrier_link_and_versions_corrections(self):
        shipment = self.shipment
        picking = shipment.picking_id
        service = self.env['product.product'].create({'name': 'QA Delivery', 'type': 'service'})
        carrier = self.env['delivery.carrier'].create({
            'name': 'QA Carrier', 'product_id': service.id, 'shopify_tracking_company': 'UPS',
            'tracking_url': 'https://example.com/track/<shipmenttrackingnumber>',
        })
        picking.write({'carrier_id': carrier.id, 'carrier_tracking_ref': 'TRACK-2635'})
        self.store._enqueue_shopify_shipment(shipment.order_id, picking)
        expected = {
            'number': 'TRACK-2635', 'company': 'UPS', 'url': 'https://example.com/track/TRACK-2635',
        }
        self.assertEqual(self.store._shopify_tracking(shipment), expected)
        version = shipment.version
        self.store._enqueue_shopify_shipment(shipment.order_id, picking)
        self.assertEqual(shipment.version, version)

        picking.carrier_tracking_ref = 'CORRECTED'
        self.store._enqueue_shopify_shipment(shipment.order_id, picking)
        self.assertEqual(shipment.version, version + 1)
        corrected = {
            'number': 'CORRECTED', 'company': 'UPS', 'url': 'https://example.com/track/CORRECTED',
        }
        self.assertEqual(self.store._shopify_tracking(shipment), corrected)
        self.assertFalse(self.store._shopify_tracking_matches(
            [{**corrected, 'url': expected['url']}], corrected,
        ))

    def test_tracking_correction_after_send_marker_disables_the_old_tracking_request(self):
        shipment = self.shipment
        shipment.picking_id.state = 'done'
        shipment.remote_id = 'gid://shopify/Fulfillment/1'
        order = shipment.order_id
        action = self.env['qing.channel.action']._ensure_action(
            self.store, 'tracking:gate:1', 'tracking', order_id=order.id)
        request = self.env['qing.channel.request']._prepare_request(
            action, 'tracking_update', f'fulfillment:{order.id}', {
                'shipment': shipment.id, 'version': 1, 'remote_id': shipment.remote_id,
                'tracking': {'number': 'TRACK-2635'},
            })
        mark = type(request)._mark_before_send

        def correct_after_recording_started(item):
            mark(item)
            shipment.write({'tracking': 'CORRECTED', 'version': 2})

        sent = {'fulfillmentTrackingInfoUpdate': {'fulfillment': {'id': shipment.remote_id}, 'userErrors': []}}
        with (patch.object(self.env.cr, 'commit'),
              patch.object(type(request), '_mark_before_send', correct_after_recording_started),
              patch.object(type(self.store), '_shopify_client') as client):
            client.return_value.query.return_value = sent
            request._process_one()
            client.assert_not_called()
        self.assertEqual(request.state, 'obsolete')
        self.assertEqual(shipment.tracking, 'CORRECTED')

    def test_uninstall_after_send_marker_defers_fulfillment_until_a_verified_connection(self):
        request = self.request
        request.write({'state': 'ready', 'first_sent_at': False})
        self.shipment.picking_id.state = 'done'
        mark = type(request)._mark_before_send

        def uninstall_after_recording_started(item):
            mark(item)
            self.store._record_shopify_failure('shopify_app_uninstalled')

        sent = {'fulfillmentCreate': {'fulfillment': {'id': 'gid://shopify/Fulfillment/1'}, 'userErrors': []}}
        with (patch.object(self.env.cr, 'commit'),
              patch.object(type(request), '_mark_before_send', uninstall_after_recording_started),
              patch.object(type(self.store), '_shopify_shipment_lines', return_value=request.payload),
              patch.object(type(self.store), '_shopify_client') as client):
            client.return_value.query.return_value = sent
            with TestCase().assertRaisesRegex(DeferredChannelError, 'shopify_disconnected'):
                self.store._execute_channel_request(request, recovering=False)
            client.assert_not_called()
        self.assertTrue(request.confirmed_rejection)
        self.assertFalse(self.shipment.remote_id)

    def test_queued_shipment_uses_its_own_version_after_an_order_refresh(self):
        store = self.store
        shipment = self.shipment
        order = shipment.order_id
        shipment.picking_id.state = 'done'
        store.shopify_invoice_policy = 'capture'
        self.request.state = 'obsolete'
        Action = self.env['qing.channel.action']
        for kind in ('fulfillment', 'tracking'):
            with self.subTest(kind=kind):
                order.snapshot_version = 3
                if kind == 'tracking':
                    shipment.remote_id = 'gid://shopify/Fulfillment/1'
                store._enqueue_shopify_shipment(order, shipment.picking_id)
                action = Action.search([
                    ('store_id', '=', store.id), ('key', '=', f'{kind}:{shipment.id}:{shipment.version}'),
                ])
                self.assertEqual(len(action), 1)
                order.snapshot_version = 4
                with (patch.object(self.env.cr, 'commit'),
                      patch.object(type(store), '_shopify_shipment_lines', return_value=self.request.payload)):
                    action._process_one()
                self.assertEqual(action.state, 'external')
                self.assertEqual(len(action.request_ids), 1)
                self.assertEqual(action.request_ids.payload['shipment'], shipment.id)

    def test_disconnected_shopify_jobs_keep_retry_budget_until_reconnected(self):
        store = self.store
        Request = self.env['qing.channel.request']
        Action = self.env['qing.channel.action']
        Delivery = self.env['qing.channel.delivery']
        self.request.action_id.state = 'external'
        order = self.shipment.order_id
        with (patch.object(self.env.cr, 'commit'), patch.object(self.env.cr, 'rollback'),
              patch.object(delivery_service, 'fetch_fulfillment_snapshot',
                           side_effect=ChannelReviewRequired('shopify_scope_denied'))):
            self.request._process_one()
        self.assertEqual(store.shopify_health, 'disconnected')
        pending = Action._ensure_action(store, 'held-shopify-action', 'sale', order_id=order.id)
        delivery = Delivery._receive(store, 'held-shopify-delivery', order.external_id, 'orders/updated')
        with (patch.object(action_queue, 'locked_job_environment') as action_worker,
              patch.object(delivery_queue, 'locked_job_environment') as delivery_worker,
              patch.object(request_queue, 'locked_job_environment') as request_worker):
            Action._cron_process(limit=100)
            Delivery._cron_process(limit=100)
            Request._cron_process(limit=100)
        for worker, prefix in ((action_worker, 'qing-action-order'), (delivery_worker, 'qing-channel-inbox'),
                               (request_worker, 'qing-channel-outbox')):
            self.assertFalse(any(call.args[1].startswith(f'{prefix}:{store.id}:')
                                 for call in worker.call_args_list))
        with (patch.object(self.env.cr, 'commit'), patch.object(self.env.cr, 'rollback'),
              patch.object(type(store), '_shopify_client') as client):
            for _attempt in range(10):
                delivery._process_one()
                pending._process_one()
                self.request._process_one()
            client.assert_not_called()
        self.assertEqual((delivery.state, pending.state, self.request.state), ('pending', 'pending', 'uncertain'))
        self.assertEqual((delivery.tries, pending.tries, self.request.checks), (0, 0, 0))
        self.assertFalse(self.request.remote_reference)
        self.assertFalse(self.request.retry_at or self.request.action_id.retry_at or pending.retry_at or delivery.retry_at)

        currency = self.env.company.currency_id
        store.write({'shopify_currency_id': currency.id, 'cutover_at': fields.Datetime.now() - timedelta(days=1)})
        connection = {
            'currentAppInstallation': {'accessScopes': [{'handle': scope} for scope in REQUIRED_SCOPES]},
            'shop': {'myshopifyDomain': store.shopify_domain, 'currencyCode': currency.name},
            'location': {'id': store.shopify_location, 'isActive': True},
            'customers': {'nodes': []},
            'orders': {'nodes': []},
        }
        with patch.object(type(store), '_shopify_client') as client:
            client.return_value.query.return_value = connection
            store.action_shopify_check_connection()
            client.return_value.query.assert_called_once_with(CONNECTION_QUERY, {'location': store.shopify_location})
        self.assertEqual(store.shopify_health, 'connected')
        self.assertFalse(store.onboarded)
        remote = 'gid://shopify/Fulfillment/90'
        observed = {'fulfillments': [{
            'id': remote, 'status': 'SUCCESS', 'location': {'id': store.shopify_location},
            'trackingInfo': [{'number': 'TRACK-2635'}],
            'fulfillmentLineItems': {'nodes': [{'quantity': 2, 'lineItem': {'id': 'gid://shopify/LineItem/11'}}]},
        }]}
        with (patch.object(self.env.cr, 'commit'), patch.object(type(store), '_shopify_client') as client,
              patch.object(delivery_service, 'fetch_fulfillment_snapshot', return_value=observed)):
            self.request._process_one()
            client.return_value.query.assert_not_called()
        self.assertEqual(self.request.state, 'satisfied')
        self.assertEqual(self.request.action_id.state, 'done')
        self.assertEqual(self.shipment.remote_id, remote)

    def test_fulfillment_can_retry_when_token_refresh_blocked_the_send(self):
        self.request.write({'state': 'ready', 'first_sent_at': False})
        self.shipment.picking_id.state = 'done'
        postponed = DeferredChannelError('shopify_token_refresh_in_progress', retry_after=5)
        sent = {'fulfillmentCreate': {'fulfillment': {'id': 'gid://shopify/Fulfillment/1'}, 'userErrors': []}}
        with (patch.object(self.env.cr, 'commit'),
              patch.object(type(self.store), '_shopify_shipment_lines', return_value=self.request.payload),
              patch.object(type(self.store), '_shopify_client') as client):
            client.return_value.query.side_effect = [postponed, sent]
            with TestCase().assertRaisesRegex(DeferredChannelError, 'shopify_token_refresh_in_progress'):
                self.store._send_shopify_fulfillment(self.request, self.shipment, recovering=False)
            self.assertTrue(self.request.confirmed_rejection)
            self.assertFalse(self.shipment.remote_id)
            self.assertEqual(self.store._send_shopify_fulfillment(
                self.request, self.shipment, recovering=True), 'success')
            self.assertEqual([call.args[0] for call in client.return_value.query.call_args_list],
                             [delivery_service.FULFILL, delivery_service.FULFILL])
        self.assertEqual(self.shipment.remote_id, 'gid://shopify/Fulfillment/1')

    def test_recover_old_tracking_request_when_correction_is_already_visible(self):
        shipment = self.shipment
        shipment.write({'remote_id': 'gid://shopify/Fulfillment/1', 'tracking': 'CORRECTED', 'version': 2})
        order = shipment.order_id
        action = self.env['qing.channel.action']._ensure_action(
            self.store, 'tracking:1:1', 'tracking', order_id=order.id)
        request = self.env['qing.channel.request']._prepare_request(action, 'tracking_update',
            f'fulfillment:{order.id}', {
                'shipment': shipment.id, 'version': 1, 'remote_id': shipment.remote_id,
                'tracking': {'number': 'TRACK-2635'},
            })
        request.write({'state': 'uncertain', 'first_sent_at': fields.Datetime.now()})
        remote = {'fulfillments': [{'id': shipment.remote_id, 'trackingInfo': [{'number': 'CORRECTED'}]}]}
        with (patch.object(delivery_service, 'fetch_fulfillment_snapshot', return_value=remote),
              patch.object(type(self.store), '_shopify_client') as client):
            self.assertEqual(self.store._send_shopify_tracking(request, shipment, recovering=True), 'obsolete')
            client.return_value.query.assert_not_called()
