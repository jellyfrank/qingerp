from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta, timezone
import os
from secrets import token_urlsafe
from unittest import TestCase
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.addons.qing_channel.errors import ChannelReviewRequired, DeferredChannelError
from odoo.addons.qing_channel.models import action as action_queue, request as request_queue
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user

from ..models import api_budget, bulk_import, store as shopify_store, store_catalog, store_order, store_refund
from ..services import bulk, client as shopify_client, orders



@tagged('post_install', '-at_install')
class TestShopifyPilot(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        encryption = patch.dict(os.environ, {'QING_SHOPIFY_CREDENTIAL_KEY': Fernet.generate_key().decode()})
        encryption.start()
        cls.addClassCleanup(encryption.stop)
        company = cls.env.company
        cls.product = cls.env['product.product'].create({
            'name': 'Channel Item', 'default_code': 'QA-2635', 'is_storable': True,
            'type': 'consu', 'list_price': 42,
        })
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Shop Test', 'currency_id': company.currency_id.id, 'company_id': company.id,
        })
        cls.store = cls.env['qing.channel.store'].create({
            'name': 'Pilot', 'platform': 'shopify', 'shopify_domain': 'pilot-2635.myshopify.com',
            'company_id': company.id, 'warehouse_id': cls.warehouse.id,
            'cutover_at': '2026-11-17 12:00:00', 'shopify_health': 'connected',
            'shopify_location': 'gid://shopify/Location/1', 'shopify_currency_id': company.currency_id.id,
            'shopify_pricelist_id': cls.pricelist.id,
        })
        cls.CurrencyCode = company.currency_id.name
        cls.test_binding = cls.env['qing.channel.binding'].create({
            'store_id': cls.store.id, 'kind': 'variant', 'external_id': 'gid://shopify/ProductVariant/1',
            'product_id': cls.product.id,
        })

    def _money(self, amount):
        return {'shopMoney': {'amount': amount, 'currencyCode': self.CurrencyCode},
                'presentmentMoney': {'amount': '50.00', 'currencyCode': 'CAD'}}

    def _snapshot(self):
        address = {'firstName': 'Sample', 'lastName': 'Buyer', 'address1': '1 Example St',
                   'countryCodeV2': 'US', 'provinceCode': 'CA', 'city': 'Oakland'}
        return {
            'id': 'gid://shopify/Order/1', 'name': '#1', 'createdAt': '2026-11-18T12:00:00Z',
            'updatedAt': '2026-11-18T12:00:00Z', 'sourceName': 'web', 'currencyCode': self.CurrencyCode,
            'taxesIncluded': False, 'shippingAddress': address, 'billingAddress': address,
            'customer': {'id': 'gid://shopify/Customer/1'},
            'currentTotalPriceSet': self._money('24.00'), 'currentTotalTaxSet': self._money('0.00'),
            'transactions': [], 'refunds': [], 'shippingLines': {'nodes': []}, 'fulfillments': [],
            'fulfillmentOrders': {'nodes': [
                {'id': 'gid://shopify/FulfillmentOrder/1', 'status': 'OPEN',
                 'assignedLocation': {'location': {'id': self.store.shopify_location}}}
            ]},
            'lineItems': {'nodes': [{
                'id': 'gid://shopify/LineItem/1', 'sku': self.product.default_code,
                'quantity': 2, 'currentQuantity': 2, 'requiresShipping': True, 'isGiftCard': False,
                'variant': {'id': 'gid://shopify/ProductVariant/1'},
                'priceAfterAllDiscountsBeforeTaxesSet': self._money('24.00'), 'taxLines': [],
            }]},
        }

    def _sale_action(self, order):
        domain = [('order_id', '=', order.id), ('snapshot_version', '=', order.snapshot_version), ('kind', '=', 'sale')]
        return self.env['qing.channel.action'].search(domain, limit=1)

    def _inventory_binding(self):
        return self.env['qing.channel.binding'].create({
            'store_id': self.store.id, 'kind': 'inventory', 'external_id': 'gid://shopify/InventoryItem/1',
            'location_external_id': self.store.shopify_location, 'product_id': self.product.id,
            'last_quantity': 10, 'quantity_version': 1,
        })

    def test_snapshot_and_draft_sales_order_keep_shop_price_and_claim(self):
        snapshot = self._snapshot()
        order = self.store._apply_shopify_order(snapshot)
        self.assertEqual(order.snapshot_version, 1)
        self.assertEqual(order, self.store._apply_shopify_order(deepcopy(snapshot)))
        self.assertEqual(order.snapshot_version, 1)
        refreshed = dict(snapshot, updatedAt='2026-11-18T12:01:00Z')
        self.store._apply_shopify_order(refreshed)
        self.assertEqual(order.snapshot_version, 1)
        self.assertEqual(fields.Datetime.to_string(order.external_updated_at), '2026-11-18 12:01:00')
        self.store._execute_channel_action(self._sale_action(order))
        self.assertEqual(order.sale_id.state, 'draft')
        self.assertEqual(order.sale_id.amount_total, 24)
        self.assertEqual(order.sale_id.order_line.price_unit, 12)
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 10)
        self.assertEqual(self.store._shopify_stock_values(self._inventory_binding()), (8, 2))

    def test_local_sale_cancellation_retains_shopify_claim_and_warns_until_shopify_cancels(self):
        binding = self._inventory_binding()
        self.store.onboarded = True
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 10)
        snapshot = self._snapshot()
        order = self.store._apply_shopify_order(snapshot)
        self.store._execute_channel_action(self._sale_action(order))
        sale = order.sale_id
        sale.action_confirm()
        sale.picking_ids.action_assign()
        warehouse_product = self.product.with_context(warehouse_id=self.warehouse.id)
        self.assertEqual(warehouse_product.free_qty, 8)
        self.assertEqual(self.store._shopify_stock_values(binding), (8, 2))

        sale.action_cancel()
        self.assertEqual(sale.state, 'cancel')
        self.assertEqual(warehouse_product.free_qty, 10)
        self.assertEqual(self.store._shopify_stock_values(binding), (8, 2))
        self.store._cron_shopify_inventory()
        self.store._cron_shopify_inventory()
        domain = [
            ('store_id', '=', self.store.id), ('code', '=', 'shopify_erp_stock_handover_missing'),
            ('object_model', '=', order._name), ('object_id', '=', order.id),
        ]
        issue = self.env['qing.channel.exception'].search(domain)
        self.assertEqual(len(issue), 1)
        self.assertFalse(issue.resolved)

        cancelled = dict(snapshot, cancelledAt='2026-11-18T12:02:00Z', updatedAt='2026-11-18T12:02:00Z')
        self.store._apply_shopify_order(cancelled)
        self.store._cron_shopify_inventory()
        self.assertTrue(issue.resolved)
        self.assertEqual(self.store._shopify_stock_values(binding)[0], 10)

    def test_removed_draft_sale_line_retains_shopify_claim_and_warns_until_fulfilled(self):
        binding = self._inventory_binding()
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 10)
        snapshot = self._snapshot()
        order = self.store._apply_shopify_order(snapshot)
        self.store._execute_channel_action(self._sale_action(order))
        self.assertEqual(order.sale_id.state, 'draft')
        order.sale_id.order_line.unlink()
        self.env.invalidate_all()
        self.assertFalse(order.line_ids.sale_line_id)
        self.assertEqual(self.store._shopify_stock_values(binding), (8, 2))
        self.store._cron_shopify_inventory()
        issue = self.env['qing.channel.exception'].search([
            ('store_id', '=', self.store.id), ('code', '=', 'shopify_erp_stock_handover_missing'),
            ('object_model', '=', order._name), ('object_id', '=', order.id),
        ])
        self.assertEqual(len(issue), 1)
        self.assertFalse(issue.resolved)

        detail = snapshot['lineItems']['nodes'][0]
        snapshot['updatedAt'] = '2026-11-18T12:03:00Z'
        snapshot['fulfillments'] = [{
            'status': 'SUCCESS', 'createdAt': snapshot['updatedAt'],
            'fulfillmentLineItems': {'nodes': [{'quantity': 2, 'lineItem': {'id': detail['id']}}]},
        }]
        assignment = snapshot['fulfillmentOrders']['nodes'][0]
        assignment.update(status='CLOSED', lineItems={'nodes': [
            {'remainingQuantity': 0, 'lineItem': {'id': detail['id']}},
        ]})
        self.store._apply_shopify_order(snapshot)
        self.store._cron_shopify_inventory()
        self.assertTrue(issue.resolved)

    def test_cancelled_opening_sale_warns_before_history_import(self):
        binding = self._inventory_binding()
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 10)
        snapshot = self._snapshot()
        snapshot['createdAt'] = '2026-08-01T12:00:00Z'
        sale = self.env['sale.order'].create({
            'partner_id': self.env.company.partner_id.id, 'warehouse_id': self.warehouse.id,
            'order_line': [Command.create({'product_id': self.product.id, 'product_uom_qty': 2})],
        })
        opening = self.env['qing.shopify.opening.commitment'].create({
            'store_id': self.store.id, 'external_order': snapshot['id'],
            'external_line_id': snapshot['lineItems']['nodes'][0]['id'],
            'product_id': self.product.id, 'quantity': 2, 'sale_line_id': sale.order_line.id,
        })
        sale.action_cancel()
        self.store._cron_shopify_inventory()
        issue = self.env['qing.channel.exception'].search([
            ('store_id', '=', self.store.id), ('code', '=', 'shopify_erp_stock_handover_missing'),
            ('object_model', '=', opening._name), ('object_id', '=', opening.id),
        ])
        self.assertEqual(len(issue), 1)
        self.assertFalse(issue.resolved)
        self.assertEqual(self.store._shopify_stock_values(binding), (8, 2))
        snapshot['cancelledAt'] = snapshot['updatedAt']
        self.store._apply_shopify_order(snapshot)
        self.store._cron_shopify_inventory()
        self.assertTrue(issue.resolved)
        self.assertEqual(self.store._shopify_stock_values(binding)[0], 10)

    def test_order_reader_collects_all_selected_connections(self):
        external_id = 'gid://shopify/Order/1'
        refund_id = 'gid://shopify/Refund/1'
        assignment_id = 'gid://shopify/FulfillmentOrder/1'
        fulfillment_id = 'gid://shopify/Fulfillment/1'
        version = '2026-11-18T12:00:00Z'

        def page(key, second=False):
            return {'nodes': [{'id': f'{key}:{2 if second else 1}'}],
                    'pageInfo': {'hasNextPage': not second, 'endCursor': f'{key}:{2 if second else 1}'}}

        refund_pages = {
            'transactions': orders.REFUND_TRANSACTION_PAGE,
            'refundLineItems': orders.REFUND_LINE_PAGE,
            'refundShippingLines': orders.REFUND_SHIPPING_PAGE,
            'orderAdjustments': orders.REFUND_ADJUSTMENT_PAGE,
        }
        replies = {
            (orders.ORDER_QUERY, external_id, None): {'order': {
                'id': external_id, 'updatedAt': version, 'refunds': [{'id': refund_id}],
                'lineItems': page('order-lines'), 'shippingLines': page('shipping'),
            }},
            (orders.FULFILLMENT_REFERENCES, external_id, None): {'order': {
                'id': external_id, 'updatedAt': version, 'fulfillments': [{'id': fulfillment_id}],
                'fulfillmentOrders': {'nodes': [{'id': assignment_id}],
                                      'pageInfo': {'hasNextPage': True, 'endCursor': 'assignments:1'}},
            }},
            (orders.FULFILLMENT_REFERENCE_PAGE, external_id, 'assignments:1'): {'order': {
                'id': external_id, 'fulfillmentOrders': page('assignments', True),
            }},
            (orders.FULFILLMENT_ORDER, assignment_id, None): {'fulfillmentOrder': {
                'id': assignment_id, 'lineItems': page('assigned-lines'),
            }},
            (orders.FULFILLMENT_ORDER, 'assignments:2', None): {'fulfillmentOrder': {
                'id': 'assignments:2', 'lineItems': {'nodes': []},
            }},
            (orders.FULFILLMENT, fulfillment_id, None): {'node': {
                'id': fulfillment_id, 'fulfillmentLineItems': page('fulfilled-lines'),
            }},
            (orders.REFUND_QUERY, refund_id, None): {'node': {
                'id': refund_id, **{field: page(field) for field in refund_pages},
            }},
            (orders.ORDER_TRANSACTIONS, external_id, None): {'order': {'updatedAt': version, 'transactions': []}},
        }
        for document, root, identifier, field, key in (
            (orders.ORDER_LINE_PAGE, 'order', external_id, 'lineItems', 'order-lines'),
            (orders.SHIPPING_PAGE, 'order', external_id, 'shippingLines', 'shipping'),
            (orders.ASSIGNMENT_LINE_PAGE, 'fulfillmentOrder', assignment_id, 'lineItems', 'assigned-lines'),
            (orders.FULFILLED_LINE_PAGE, 'node', fulfillment_id, 'fulfillmentLineItems', 'fulfilled-lines'),
            *((document, 'node', refund_id, field, field) for field, document in refund_pages.items()),
        ):
            replies[document, identifier, f'{key}:1'] = {root: {'id': identifier, field: page(key, True)}}

        client = MagicMock()
        client.query.side_effect = lambda document, values: replies.pop((document, values['id'], values.get('after')))
        snapshot = orders.fetch_order(client, external_id)
        self.assertFalse(replies)
        self.assertEqual([item['id'] for item in orders.nodes(snapshot['lineItems'])], ['order-lines:1', 'order-lines:2'])
        self.assertNotIn('pageInfo', snapshot['lineItems'])
        self.assertNotIn('pageInfo', snapshot['shippingLines'])
        self.assertEqual(len(orders.nodes(snapshot['shippingLines'])), 2)
        assignments = orders.nodes(snapshot['fulfillmentOrders'])
        self.assertEqual([assignment['id'] for assignment in assignments], [assignment_id, 'assignments:2'])
        self.assertEqual(len(orders.nodes(assignments[0]['lineItems'])), 2)
        self.assertEqual(len(orders.nodes(snapshot['fulfillments'][0]['fulfillmentLineItems'])), 2)
        for field in refund_pages:
            self.assertEqual(len(orders.nodes(snapshot['refunds'][0][field])), 2)

    def test_order_snapshot_changed_after_read_retries_without_creating_an_order(self):
        snapshot = self._snapshot()
        version = snapshot['updatedAt']
        changed = '2026-11-18T12:01:00Z'
        delivery = self.env['qing.channel.delivery']._receive(self.store, 'version-change', snapshot['id'], 'orders/create')
        replies = {
            orders.ORDER_QUERY: {'order': snapshot},
            orders.FULFILLMENT_REFERENCES: {'order': {
                'id': snapshot['id'], 'updatedAt': version, 'fulfillmentOrders': {'nodes': []}, 'fulfillments': [],
            }},
            orders.ORDER_TRANSACTIONS: {'order': {'updatedAt': changed, 'transactions': []}},
        }
        client = MagicMock()
        client.query.side_effect = lambda document, _values: deepcopy(replies[document])
        with (patch.object(type(self.store), '_shopify_client', return_value=client),
              patch.object(self.env.cr, 'commit')):
            delivery._process_one()
            self.assertEqual(delivery.state, 'retry')
            self.assertFalse(self.env['qing.channel.order'].search([('store_id', '=', self.store.id)]))
            replies[orders.ORDER_QUERY]['order']['updatedAt'] = changed
            replies[orders.FULFILLMENT_REFERENCES]['order']['updatedAt'] = changed
            delivery._process_one()
        self.assertEqual(delivery.state, 'done')
        channel_order = self.env['qing.channel.order'].search([('store_id', '=', self.store.id)])
        self.assertEqual(channel_order.external_updated_at, orders.parse_date(changed))
        self.assertEqual(channel_order.snapshot_version, 1)

    def test_paged_order_refuses_duplicate_details_or_repeated_cursor(self):
        initial = {'id': 'order', 'lineItems': {'nodes': [{'id': 'first'}],
                                              'pageInfo': {'hasNextPage': True, 'endCursor': 'same'}}}
        for next_id, code in (('first', 'shopify_snapshot_page_overlap'),
                              ('second', 'shopify_snapshot_cursor_invalid')):
            with self.subTest(next_id=next_id):
                client = MagicMock()
                client.query.return_value = {'order': {
                    'id': 'order', 'lineItems': dict(initial['lineItems'], nodes=[{'id': next_id}]),
                }}
                with self.assertRaisesRegex(DeferredChannelError, code):
                    orders._complete_connection(client, initial, 'lineItems', orders.ORDER_LINE_PAGE, 'order')
                self.assertEqual(client.query.call_count, 1)
                self.assertEqual(initial['lineItems']['nodes'], [{'id': 'first'}])

    def test_unique_catalog_barcode_maps_variant_and_prepare_inventory_item(self):
        self.test_binding.unlink()
        self.product.barcode = '123456789'
        snapshot = self._snapshot()
        line = snapshot['lineItems']['nodes'][0]
        line['sku'] = 'Shop-SKU'
        variant = line['variant']
        variant['inventoryItem'] = {'id': 'gid://shopify/InventoryItem/22', 'tracked': True}
        barcode_values = [{'value': 'unmatched-primary'}, {'value': self.product.barcode}]
        result_variant = {'id': variant['id'], 'barcodes': {'nodes': barcode_values, 'pageInfo': {'hasNextPage': False}}}
        sku_matches = {'nodes': [{'id': variant['id'], 'sku': line['sku']}], 'pageInfo': {'hasNextPage': False}}
        barcode_matches = {'nodes': [result_variant], 'pageInfo': {'hasNextPage': False}}
        client = MagicMock()
        client.query.side_effect = [
            {'productVariant': result_variant, 'skuMatches': sku_matches},
            {'productVariants': barcode_matches},
        ]
        with patch.object(type(self.store), '_shopify_client', return_value=client):
            order = self.store._apply_shopify_order(snapshot)
        self.assertEqual(order.line_ids.product_id, self.product)
        self.assertEqual(client.query.call_args.args, (store_catalog.MATCH_BARCODE, {'query': 'barcode:"123456789"'}))
        bindings = self.env['qing.channel.binding'].search([('store_id', '=', self.store.id)])
        self.assertEqual(set(bindings.mapped('kind')), {'inventory', 'variant'})
        item = bindings.filtered(lambda binding: binding.kind == 'inventory')
        self.assertEqual(item.external_id, variant['inventoryItem']['id'])
        self.assertEqual(item.location_external_id, self.store.shopify_location)

    def test_blank_order_sku_maps_from_unique_catalog_barcode(self):
        self.test_binding.unlink()
        self.product.barcode = '123456789'
        snapshot = self._snapshot()
        line = snapshot['lineItems']['nodes'][0]
        line['sku'] = ''
        catalog_variant = {
            'id': line['variant']['id'], 'sku': None,
            'barcodes': {'nodes': [{'value': self.product.barcode}], 'pageInfo': {'hasNextPage': False}},
        }
        client = MagicMock()
        client.query.side_effect = [
            {'productVariant': catalog_variant},
            {'productVariants': {'nodes': [catalog_variant], 'pageInfo': {'hasNextPage': False}}},
        ]
        with patch.object(type(self.store), '_shopify_client', return_value=client):
            order = self.store._apply_shopify_order(snapshot)
        self.assertEqual(order.line_ids.product_id, self.product)
        self.assertEqual(client.query.call_args_list[0].args[0], store_catalog.LOOKUP_VARIANT)
        self.assertEqual(client.query.call_args_list[1].args[0], store_catalog.MATCH_BARCODE)

    def test_blank_order_sku_maps_from_current_shopify_sku(self):
        self.test_binding.unlink()
        snapshot = self._snapshot()
        line = snapshot['lineItems']['nodes'][0]
        line['sku'] = None
        variant_id = line['variant']['id']
        client = MagicMock()
        client.query.side_effect = [
            {'productVariant': {
                'id': variant_id, 'sku': self.product.default_code,
                'barcodes': {'nodes': [], 'pageInfo': {'hasNextPage': False}},
            }},
            {'skuMatches': {'nodes': [{'id': variant_id, 'sku': self.product.default_code}],
                            'pageInfo': {'hasNextPage': False}}},
        ]
        with patch.object(type(self.store), '_shopify_client', return_value=client):
            order = self.store._apply_shopify_order(snapshot)
        self.assertEqual(order.line_ids.product_id, self.product)
        self.assertEqual(client.query.call_args_list[1].args[0], store_catalog.MATCH_SKU)

    def test_automatic_import_recovers_unmapped_orders_once(self):
        self.test_binding.unlink()
        snapshot = self._snapshot()
        line = snapshot['lineItems']['nodes'][0]
        line['sku'] = ''
        variant_id = line['variant']['id']
        line['variant']['inventoryItem'] = {'id': 'gid://shopify/InventoryItem/22', 'tracked': True}
        client = MagicMock()
        client.query.return_value = {'productVariant': {
            'id': variant_id, 'sku': None, 'displayName': 'Imported Shopify Product',
            'product': {'status': 'ACTIVE'},
            'barcodes': {'nodes': [], 'pageInfo': {'hasNextPage': False}},
        }}
        with patch.object(type(self.store), '_shopify_client', return_value=client):
            order = self.store._apply_shopify_order(snapshot)
            sale_action = self._sale_action(order)
            sale_action._review('shopify_pricelist_currency_mismatch')
            self.assertFalse(order.line_ids.product_id)
            self.store.shopify_import_unmatched_products = True
            self.store._shopify_recover_product_mappings()
            self.store._shopify_recover_product_mappings()

        binding = self.env['qing.channel.binding'].search([
            ('store_id', '=', self.store.id), ('kind', '=', 'variant'), ('external_id', '=', variant_id),
        ])
        self.assertEqual(len(binding), 1)
        imported = binding.product_id
        self.assertEqual(imported.company_id, self.store.company_id)
        self.assertEqual(imported.name, 'Imported Shopify Product')
        self.assertTrue(imported.is_storable)
        self.assertEqual(client.query.call_count, 2)
        Delivery = self.env['qing.channel.delivery']
        delivery = Delivery.search([('store_id', '=', self.store.id), ('external_id', '=', order.external_id)])
        self.assertEqual(len(delivery), 1)
        with (patch.object(shopify_store, 'fetch_order', return_value=deepcopy(snapshot)),
              patch.object(type(self.store), '_shopify_client', return_value=client),
              patch.object(self.env.cr, 'commit')):
            delivery._process_one()
            self.assertEqual(delivery.state, 'done')
            self.assertEqual(order.line_ids.product_id, imported)
            self.assertEqual(sale_action.state, 'pending')
            sale_action._process_one()
        self.assertEqual(sale_action.state, 'done')
        self.assertEqual(order.sale_id.state, 'draft')
        self.assertEqual(order.sale_id.amount_total, 24)
        self.assertEqual(order.sale_id.order_line.product_id, imported)
        self.assertEqual(client.query.call_count, 2)
        self.assertFalse(self.env['qing.channel.exception'].search_count([
            ('store_id', '=', self.store.id), ('resolved', '=', False),
            ('code', 'in', ['shopify_sku_missing', 'shopify_pricelist_currency_mismatch']),
        ]))

    def test_shared_secondary_barcode_on_two_shopify_variants_requires_review(self):
        self.test_binding.unlink()
        self.product.barcode = '123456789'
        line = self._snapshot()['lineItems']['nodes'][0]
        line['sku'] = 'Shop-SKU'
        barcodes = {'nodes': [{'value': 'primary'}, {'value': self.product.barcode}],
                    'pageInfo': {'hasNextPage': False}}
        variant = {'id': line['variant']['id'], 'barcodes': barcodes}
        other_variant = dict(variant, id='gid://shopify/ProductVariant/2')
        client = MagicMock()
        client.query.side_effect = [
            {'productVariant': variant, 'skuMatches': {
                'nodes': [{'id': variant['id'], 'sku': line['sku']}], 'pageInfo': {'hasNextPage': False},
            }},
            {'productVariants': {'nodes': [variant, other_variant], 'pageInfo': {'hasNextPage': False}}},
        ]
        with (patch.object(type(self.store), '_shopify_client', return_value=client),
              self.assertRaisesRegex(ChannelReviewRequired, 'shopify_catalog_codes_ambiguous')):
            self.store._find_shopify_product(line)
        self.assertFalse(self.env['qing.channel.binding'].search([('store_id', '=', self.store.id)]))

    def test_catalog_duplicate_shopify_sku_stays_in_channel_until_manually_mapped(self):
        self.test_binding.unlink()
        self.store.shopify_import_unmatched_products = True
        snapshot = self._snapshot()
        data = snapshot['lineItems']['nodes'][0]
        candidates = [{'id': item, 'sku': data['sku']} for item in
                      (data['variant']['id'], 'gid://shopify/ProductVariant/2')]
        result = {
            'productVariant': {'id': data['variant']['id'], 'barcodes': {'nodes': [], 'pageInfo': {'hasNextPage': False}}},
            'skuMatches': {'nodes': candidates, 'pageInfo': {'hasNextPage': False}},
        }
        client = type('CatalogClient', (), {})()
        with (patch.object(type(self.store), '_shopify_client', return_value=client),
              patch.object(type(client), 'query', create=True, return_value=result)):
            order = self.store._apply_shopify_order(snapshot)
        self.assertFalse(order.line_ids.product_id)
        with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_product_mapping_required'):
            self.store._execute_channel_action(self._sale_action(order))
        self.assertFalse(order.sale_id)
        problems = self.env['qing.channel.exception'].search([
            ('store_id', '=', self.store.id), ('object_model', '=', order.line_ids._name),
            ('object_id', '=', order.line_ids.id),
        ])
        self.assertIn('shopify_catalog_codes_ambiguous', problems.mapped('code'))
        sale_action = self._sale_action(order)
        sale_action._review('shopify_product_mapping_required')
        manager = new_test_user(self.env, login='shopify-mapping-manager-2635',
                                groups='qing_channel.group_channel_manager', company_id=self.env.company.id)
        problems.with_user(manager).action_recheck()
        deliveries = self.env['qing.channel.delivery'].search([('store_id', '=', self.store.id)])
        self.assertEqual(deliveries.external_id, order.external_id)
        self.env['qing.channel.binding'].create({
            'store_id': self.store.id, 'kind': 'variant',
            'external_id': data['variant']['id'], 'product_id': self.product.id,
        })
        self.store._apply_shopify_order(snapshot)
        self.assertEqual(order.snapshot_version, 1)
        self.assertEqual(sale_action.state, 'pending')
        self.store._execute_channel_action(sale_action)
        self.assertEqual(order.sale_id.state, 'draft')

    def test_shopify_bundle_is_flagged_even_if_the_component_has_a_binding(self):
        snapshot = self._snapshot()
        snapshot['lineItems']['nodes'][0]['lineItemGroup'] = {'id': 'gid://shopify/LineItemGroup/1'}
        order = self.store._apply_shopify_order(snapshot)
        self.assertFalse(order.line_ids.product_id)
        self.assertFalse(order.sale_id)
        self.assertTrue(self.env['qing.channel.exception'].search_count([
            ('store_id', '=', self.store.id), ('code', '=', 'shopify_product_outside_pilot'),
        ]))

    def test_stale_order_job_cannot_create_cancelled_order(self):
        snapshot = self._snapshot()
        order = self.store._apply_shopify_order(snapshot)
        old = self._sale_action(order)
        snapshot['cancelledAt'] = snapshot['updatedAt'] = '2026-11-18T12:01:00Z'
        self.store._apply_shopify_order(snapshot)
        self.assertEqual(self.store._execute_channel_action(old), 'obsolete')
        self.store._execute_channel_action(self._sale_action(order))
        self.assertFalse(order.sale_id)

    def test_historical_fulfilled_quantity_does_not_reduce_opening_inventory(self):
        snapshot = self._snapshot()
        snapshot['createdAt'] = '2026-08-01T12:00:00Z'
        snapshot['fulfillments'] = [{'status': 'SUCCESS', 'createdAt': '2026-08-04T12:00:00Z',
                                    'fulfillmentLineItems': {'nodes': [
                                        {'quantity': 1, 'lineItem': {'id': snapshot['lineItems']['nodes'][0]['id']}}
                                    ]}}]
        order = self.store._apply_shopify_order(snapshot)
        self.assertTrue(order.historical)
        self.assertFalse(self._sale_action(order))
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 10)
        self.assertEqual(self.store._shopify_stock_values(self._inventory_binding()), (9, 1))

    def test_changed_inventory_gets_an_action_slot_alongside_older_work(self):
        Action = self.env['qing.channel.action']
        self.env['qing.channel.store'].search([('id', '!=', self.store.id)]).active = False
        backlog = Action.create([
            {'store_id': self.store.id, 'kind': 'payment', 'key': f'queue-payment:{number}'}
            for number in range(25)
        ])
        self._inventory_binding()
        self.store.onboarded = True
        self.store._cron_shopify_inventory()
        stock = Action.search([('store_id', '=', self.store.id), ('kind', '=', 'stock')])
        self.assertEqual(len(stock), 1)
        expedited = Action.create([
            {'store_id': self.store.id, 'kind': 'stock', 'key': f'queue-stock:{number}', 'expedited': True}
            for number in range(3)
        ])
        picked = []

        @contextmanager
        def same_transaction(_registry, _key):
            yield self.env

        with (patch.object(action_queue, 'locked_job_environment', same_transaction),
              patch.object(type(Action), '_process_one', new=lambda record: picked.append(record.id))):
            Action._cron_process(limit=4)
        self.assertEqual(picked, [stock.id, expedited[0].id, *backlog[:2].ids])

    def test_inventory_writes_get_an_outbound_slot_alongside_older_requests(self):
        Action = self.env['qing.channel.action']
        Request = self.env['qing.channel.request']
        self.env['qing.channel.store'].search([('id', '!=', self.store.id)]).active = False
        action = Action._ensure_action(self.store, 'queue-outbound', 'shipment')
        backlog = Request.browse()
        for number in range(35):
            backlog |= Request._prepare_request(action, 'tracking_update', f'qa-request:{number}', {'shipment': number + 1})
        stock = Request.browse()
        for number in range(4):
            stock |= Request._prepare_request(action, 'inventory_set', f'stock:{123 + number}', {'target': 9})
        picked = []

        @contextmanager
        def same_transaction(_registry, _key):
            yield self.env

        with (patch.object(request_queue, 'locked_job_environment', same_transaction),
              patch.object(type(Request), '_process_one', new=lambda record: picked.append(record.id))):
            Request._cron_process(limit=4)
        self.assertEqual(picked, [*stock[:2].ids, *backlog[:2].ids])

    def test_daily_inventory_read_can_promote_a_new_local_change(self):
        binding = self._inventory_binding()
        self.store.onboarded = True
        Action = self.env['qing.channel.action']
        action = Action._ensure_action(self.store, f'stock:{binding.id}:1:10:0', 'stock', data={'binding': binding.id})
        self.store._prepare_inventory_request(action, binding, 10, 0)
        request = action.request_ids
        action.state = 'external'
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 9)
        with (patch.object(type(self.store), '_shopify_inventory', return_value=10),
              patch.object(self.env.cr, 'commit')):
            self.assertEqual(self.store._send_inventory_request(request, recovering=False), 'superseded')
        next_request = action.request_ids - request
        self.assertEqual(next_request.operation, 'inventory_set')
        self.assertEqual(next_request.payload['baseline'], 10)
        self.assertEqual(next_request.payload['target'], 9)
        self.assertFalse(request.reconcile_after)

    def test_daily_inventory_read_still_reconciles_a_difference_in_shopify(self):
        binding = self._inventory_binding()
        self.store.onboarded = True
        action = self.env['qing.channel.action']._ensure_action(
            self.store, f'stock:{binding.id}:1:10:0', 'stock', data={'binding': binding.id})
        self.store._prepare_inventory_request(action, binding, 10, 0)
        request = action.request_ids
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 9)
        with (patch.object(type(self.store), '_shopify_inventory', return_value=8),
              patch.object(self.env.cr, 'commit'),
              TestCase().assertRaisesRegex(DeferredChannelError, 'shopify_inventory_waiting_for_order_scan')):
            self.store._send_inventory_request(request, recovering=False)
        self.assertEqual(request.reconcile_quantity, 8)
        self.assertEqual(action.request_ids, request)
        self.assertEqual(binding.last_quantity, 10)

    def _inventory_dispatch_fixture(self):
        binding = self._inventory_binding()
        self.store.onboarded = True
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 9)
        action = self.env['qing.channel.action']._ensure_action(
            self.store, 'test:gate-inventory', 'stock', data={'binding': binding.id},
        )
        self.store._prepare_inventory_request(action, binding, 9, 0)
        return binding, action.request_ids

    def test_pause_after_send_record_prevents_a_new_stock_write(self):
        _binding, request = self._inventory_dispatch_fixture()
        mark = type(request)._mark_before_send

        def pause_after_recording_started(item):
            mark(item)
            self.store.action_shopify_pause_inventory()

        with (patch.object(self.env.cr, 'commit'),
              patch.object(type(request), '_mark_before_send', pause_after_recording_started),
              patch.object(type(self.store), '_shopify_client') as client):
            request._process_one()
            client.assert_not_called()
        self.assertFalse(self.store.onboarded)
        self.assertEqual(request.state, 'obsolete')
        self.assertEqual(request.action_id.state, 'obsolete')

    def test_stock_change_after_send_record_replaces_the_unsent_target(self):
        binding, request = self._inventory_dispatch_fixture()
        action = request.action_id
        action.state = 'external'
        mark = type(request)._mark_before_send

        def reduce_stock_after_recording_started(item):
            mark(item)
            self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, -2)
            # The test mocks commits, so simulate clearing the stock cache at the transaction boundary.
            self.env.invalidate_all()

        with (patch.object(self.env.cr, 'commit'),
              patch.object(type(request), '_mark_before_send', reduce_stock_after_recording_started),
              patch.object(type(self.store), '_shopify_client') as client):
            request._process_one()
            client.assert_not_called()

        self.assertEqual(request.state, 'superseded')
        replacement = action.request_ids - request
        self.assertEqual(len(replacement), 1)
        self.assertEqual(replacement.state, 'ready')
        self.assertEqual(request.payload['target'], 9)
        self.assertEqual(replacement.payload['target'], 7)
        self.assertEqual(replacement.payload['baseline'], 10)
        self.assertNotEqual(replacement.idempotency_key, request.idempotency_key)
        self.assertEqual(binding.last_quantity, 10)
        self.assertTrue(self.env['qing.channel.request.attempt'].search_count([
            ('request_id', '=', request.id), ('result', '=', 'shopify_inventory_target_changed_before_transport'),
        ]))

        response = {'inventorySetQuantities': {'userErrors': [],
                                               'inventoryAdjustmentGroup': {'referenceDocumentUri': 'fixture'}}}
        with (patch.object(self.env.cr, 'commit'),
              patch.object(type(self.store), '_shopify_client') as client):
            client.return_value.query.return_value = response
            replacement._process_one()
            client.return_value.query.assert_called_once()
            sent = client.return_value.query.call_args.args[1]
            self.assertEqual(sent['key'], replacement.idempotency_key)
            self.assertEqual(sent['input']['quantities'][0]['quantity'], 7)
            self.assertEqual(sent['input']['quantities'][0]['changeFromQuantity'], 10)
        self.assertEqual(binding.last_quantity, 7)
        self.assertEqual(action.state, 'done')

    def test_unmapped_commitment_after_send_record_stops_inventory_write(self):
        binding, request = self._inventory_dispatch_fixture()
        mark = type(request)._mark_before_send

        def import_unmapped_order_after_recording_started(item):
            mark(item)
            snapshot = self._snapshot()
            detail = snapshot['lineItems']['nodes'][0]
            detail.update(sku='', variant={'id': 'gid://shopify/ProductVariant/880'})
            self.store._apply_shopify_order(snapshot)

        with (patch.object(self.env.cr, 'commit'), patch.object(self.env.cr, 'rollback'),
              patch.object(type(request), '_mark_before_send', import_unmapped_order_after_recording_started),
              patch.object(type(self.store), '_shopify_client') as client):
            variant_id = 'gid://shopify/ProductVariant/880'
            client.return_value.query.return_value = {'productVariant': {
                'id': variant_id, 'sku': None, 'barcodes': {'nodes': [], 'pageInfo': {'hasNextPage': False}},
            }}
            request._process_one()
            client.return_value.query.assert_called_once_with(store_catalog.LOOKUP_VARIANT, {'id': variant_id})
        self.assertTrue(request.confirmed_rejection)
        self.assertEqual(request.state, 'review')
        self.assertEqual(binding.last_quantity, 10)
        self.assertTrue(self.env['qing.channel.exception'].search_count([
            ('store_id', '=', self.store.id), ('object_model', '=', request._name), ('object_id', '=', request.id),
            ('code', '=', 'shopify_stock_unmapped_commitment'), ('resolved', '=', False),
        ]))

    def test_pause_after_retry_record_reads_the_outcome_and_never_resends_stock(self):
        binding, request = self._inventory_dispatch_fixture()
        request.write({'state': 'uncertain', 'first_sent_at': fields.Datetime.now()})
        mark = type(request)._mark_before_send

        def pause_after_recording_started(item):
            mark(item)
            self.store.action_shopify_pause_inventory()

        with (patch.object(self.env.cr, 'commit'),
              patch.object(type(request), '_mark_before_send', pause_after_recording_started),
              patch.object(type(self.store), '_shopify_client') as client,
              patch.object(type(self.store), '_shopify_inventory', return_value=9)):
            with TestCase().assertRaisesRegex(DeferredChannelError, 'shopify_inventory_waiting_for_order_scan'):
                self.store._execute_channel_request(request, recovering=True)
            self.assertEqual(request.reconcile_quantity, 9)
            self._advance_inventory_scan(request.reconcile_after)
            self.assertEqual(self.store._execute_channel_request(request, recovering=True), 'satisfied')
            client.assert_not_called()
        self.assertEqual(binding.last_quantity, 9)

    def test_disconnect_after_activation_send_record_prevents_the_mutation(self):
        binding = self._inventory_binding()
        action = self.env['qing.channel.action']._ensure_action(
            self.store, 'test:gate-activation', 'inventory_activation', data={'binding': binding.id},
        )
        self.store._prepare_activation_request(action, binding)
        request = action.request_ids
        mark = type(request)._mark_before_send

        def disconnect_after_recording_started(item):
            mark(item)
            self.store._record_shopify_failure('shopify_app_uninstalled')

        with (patch.object(self.env.cr, 'commit'),
              patch.object(type(request), '_mark_before_send', disconnect_after_recording_started),
              patch.object(type(self.store), '_shopify_inventory_level', return_value=None),
              patch.object(type(self.store), '_shopify_client') as client):
            client.return_value.query.return_value = {
                'inventoryActivate': {'inventoryLevel': {'id': 'gid://shopify/InventoryLevel/3'}, 'userErrors': []},
            }
            request._process_one()
            client.assert_not_called()
        self.assertEqual(self.store.shopify_health, 'disconnected')
        self.assertEqual(request.state, 'obsolete')
        self.assertEqual(request.action_id.state, 'obsolete')

    def test_late_inventory_response_cannot_reset_new_baseline(self):
        binding = self._inventory_binding()
        self.store.onboarded = True
        action = self.env['qing.channel.action']._ensure_action(
            self.store, 'test:inventory', 'stock', data={'binding': binding.id})
        self.store._prepare_inventory_request(action, binding, 9, 0)
        request = action.request_ids
        request.first_sent_at = fields.Datetime.now()

        def query(_document, _variables):
            binding._confirm_shopify_quantity(8, 1)
            return {'inventorySetQuantities': {'inventoryAdjustmentGroup': {'referenceDocumentUri': 'test'},
                                               'userErrors': []}}

        class Client:
            pass

        client = Client()
        client.query = query
        with patch.object(type(self.store), '_shopify_client', return_value=client), patch.object(
            type(request), '_mark_before_send'
        ):
            self.assertEqual(self.store._send_inventory_request(request, recovering=True), 'success')
        self.assertEqual(binding.last_quantity, 8)
        self.assertEqual(binding.quantity_version, 2)

    def test_opening_sale_only_creates_delivery_for_the_unfulfilled_quantity(self):
        snapshot = self._snapshot()
        snapshot['createdAt'] = '2026-08-01T12:00:00Z'
        detail = snapshot['lineItems']['nodes'][0]
        snapshot['fulfillments'] = [{
            'status': 'SUCCESS', 'createdAt': '2026-08-04T12:00:00Z',
            'fulfillmentLineItems': {'nodes': [{'quantity': 1, 'lineItem': {'id': detail['id']}}]},
        }]
        assignment = snapshot['fulfillmentOrders']['nodes'][0]
        assignment['lineItems'] = {'nodes': [{'remainingQuantity': 1, 'lineItem': {'id': detail['id']}}]}
        order = self.store._apply_shopify_order(snapshot)
        self.store.shopify_open_orders_verified = True
        order.action_shopify_prepare_open_delivery()
        order.action_shopify_prepare_open_delivery()
        actions = self.env['qing.channel.action'].search([('order_id', '=', order.id)])
        self.assertEqual(actions.kind, 'opening_sale')
        self.store._execute_channel_action(actions)
        sale = order.sale_id
        self.assertEqual(sale.state, 'draft')
        self.assertTrue(sale.shopify_historical_takeover)
        self.assertEqual(sale.order_line.product_uom_qty, 1)
        self.assertEqual(sale.amount_total, 12)
        self.assertEqual(sale.order_line, order.line_ids.sale_line_id)
        self.assertEqual(self.store._execute_channel_action(actions), 'obsolete')
        sale.action_confirm()
        self.assertEqual(len(sale.picking_ids), 1)
        self.assertEqual(sale.picking_ids.move_ids.product_uom_qty, 1)
        self.assertFalse(sale.invoice_ids)

    def test_cutover_counts_older_orders_from_merchant_schedule(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 10)
        binding = self._inventory_binding()
        snapshot = self._snapshot()
        snapshot['createdAt'] = '2026-09-04T12:00:00Z'
        self.env['qing.shopify.opening.commitment'].create({
            'store_id': self.store.id, 'external_order': snapshot['id'],
            'external_line_id': snapshot['lineItems']['nodes'][0]['id'],
            'product_id': self.product.id, 'quantity': 2,
        })
        self.assertEqual(self.store._shopify_stock_values(binding), (8, 2))
        self.store._apply_shopify_order(snapshot)
        self.assertEqual(self.store._shopify_stock_values(binding), (8, 2))

    def test_cutover_never_counts_one_erp_reservation_for_two_shopify_orders(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 10)
        binding = self._inventory_binding()
        order = self.store._apply_shopify_order(self._snapshot())
        self.store._execute_channel_action(self._sale_action(order))
        sale = order.sale_id
        sale.action_confirm()
        sale.picking_ids.action_assign()
        self.assertEqual(sale.picking_ids.move_ids.quantity, 2)
        older = self.env['qing.shopify.opening.commitment'].create({
            'store_id': self.store.id, 'external_order': 'gid://shopify/Order/90',
            'external_line_id': 'gid://shopify/LineItem/91', 'product_id': self.product.id,
            'quantity': 2, 'sale_line_id': sale.order_line.id,
        })
        self.store.shopify_open_orders_verified = True
        self._advance_inventory_scan(fields.Datetime.now())
        with patch.object(type(self.store), '_shopify_inventory', return_value=6):
            with self.assertRaisesRegex(UserError, 'shopify_stock_handover_reused'):
                self.store.action_shopify_prepare_inventory()
            self.assertFalse(binding.shopify_preview_at)
            older.sale_line_id = False
            self.store.shopify_open_orders_verified = True
            self.store.action_shopify_prepare_inventory()
            self.assertEqual(binding.shopify_preview_target, 6)
            self.assertEqual(binding.shopify_preview_book, 4)
            self.store.action_shopify_enable_inventory()
        self.assertEqual(binding.last_quantity, 6)

    def test_merchant_commitment_changes_invalidate_approval_and_are_blocked_while_publishing(self):
        binding = self._inventory_binding()
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 10)
        self._advance_inventory_scan(fields.Datetime.now())
        older = self.env['qing.shopify.opening.commitment'].create({
            'store_id': self.store.id, 'external_order': 'gid://shopify/Order/90',
            'external_line_id': 'gid://shopify/LineItem/91', 'product_id': self.product.id, 'quantity': 2,
        })
        with patch.object(type(self.store), '_shopify_inventory', return_value=8):
            self.store.shopify_open_orders_verified = True
            self.store.action_shopify_prepare_inventory()
            older.external_order = 'gid://shopify/Order/92'
            self.assertFalse(self.store.shopify_open_orders_verified)
            self.assertFalse(binding.shopify_preview_at)
            with self.assertRaisesRegex(UserError, 'Verify the complete list of open orders'):
                self.store.action_shopify_enable_inventory()
            self.store.shopify_open_orders_verified = True
            self.store.action_shopify_prepare_inventory()
            self.store.action_shopify_enable_inventory()

        with self.assertRaisesRegex(ValidationError, 'Pause Shopify inventory publishing'):
            older.external_line_id = 'gid://shopify/LineItem/93'
        self.assertEqual(older.external_line_id, 'gid://shopify/LineItem/91')

    def test_opening_match_recorded_after_import_uses_the_existing_erp_sale(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 10)
        self.warehouse.out_type_id.reservation_method = 'manual'
        binding = self._inventory_binding()
        snapshot = self._snapshot()
        snapshot['createdAt'] = '2026-08-01T12:00:00Z'
        detail = snapshot['lineItems']['nodes'][0]
        assignment = snapshot['fulfillmentOrders']['nodes'][0]
        assignment['lineItems'] = {'nodes': [{'remainingQuantity': 2, 'lineItem': {'id': detail['id']}}]}
        order = self.store._apply_shopify_order(snapshot)
        customer = self.env['res.partner'].create({'name': 'Opening commitment customer'})
        sale = self.env['sale.order'].create({
            'partner_id': customer.id, 'warehouse_id': self.warehouse.id,
            'order_line': [Command.create({'product_id': self.product.id, 'product_uom_qty': 2})],
        })
        sale.action_confirm()
        sale.picking_ids.action_assign()
        self.assertEqual(sale.picking_ids.move_ids.quantity, 2)
        self.assertEqual(self.store._shopify_stock_values(binding), (6, 2))

        commitment_values = {
            'store_id': self.store.id, 'external_order': order.external_id, 'external_line_id': detail['id'],
            'product_id': self.product.id, 'quantity': 2, 'sale_line_id': sale.order_line.id,
        }
        manager = new_test_user(self.env, login='shopify-opening-match-manager',
                                groups='qing_channel.group_channel_manager,sales_team.group_sale_manager',
                                company_id=self.env.company.id)
        Opening = self.env['qing.shopify.opening.commitment'].with_user(manager)
        opening = Opening.create(commitment_values)
        self.assertEqual(order.line_ids.sale_line_id, sale.order_line)
        self.assertEqual(self.store._shopify_stock_values(binding), (8, 2))

        opening.sale_line_id = False
        self.assertFalse(order.line_ids.sale_line_id)
        self.assertEqual(self.store._shopify_stock_values(binding), (6, 2))
        opening.sale_line_id = sale.order_line
        opening.unlink()
        self.assertFalse(order.line_ids.sale_line_id)
        with self.assertRaisesRegex(ValidationError, 'does not identify the imported Shopify order'):
            Opening.create(dict(commitment_values, external_order='gid://shopify/Order/99'))
        Opening.create(dict(commitment_values, external_order=order.external_name))

        self.store.shopify_open_orders_verified = True
        order.action_shopify_prepare_open_delivery()
        action = self.env['qing.channel.action'].search([('order_id', '=', order.id), ('kind', '=', 'opening_sale')])
        self.store._execute_channel_action(action)
        self.assertEqual(order.sale_id, sale)
        self.assertEqual(order.line_ids.sale_line_id, sale.order_line)
        self.assertEqual(len(sale.picking_ids), 1)
        self.assertEqual(self.store._shopify_stock_values(binding), (8, 2))

    def test_opening_commitment_does_not_reuse_deliveries_before_cutover(self):
        cutoff = self.store.cutover_at
        self.warehouse.out_type_id.reservation_method = 'manual'
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 11)
        customer = self.env['res.partner'].create({'name': 'Opening shipment customer'})
        sale = self.env['sale.order'].create({
            'partner_id': customer.id, 'warehouse_id': self.warehouse.id,
            'order_line': [Command.create({'product_id': self.product.id, 'product_uom_qty': 3})],
        })
        sale.action_confirm()
        first = sale.picking_ids
        first.action_assign()
        first.move_ids.quantity = 1
        first.move_ids.picked = True
        first.with_context(skip_backorder=True).button_validate()
        self.assertEqual(first.state, 'done')
        old_shipment = cutoff - timedelta(days=1)
        first.move_ids.date = old_shipment
        backorder = sale.picking_ids - first
        backorder.do_unreserve()
        self.assertEqual(backorder.move_ids.product_uom_qty, 2)
        self.assertEqual(backorder.move_ids.quantity, 0)

        snapshot = self._snapshot()
        snapshot['createdAt'] = (cutoff - timedelta(days=75)).replace(tzinfo=timezone.utc).isoformat()
        detail = snapshot['lineItems']['nodes'][0]
        detail.update(quantity=3, currentQuantity=3)
        line_id = detail['id']
        snapshot['fulfillments'] = [{
            'status': 'SUCCESS', 'createdAt': old_shipment.replace(tzinfo=timezone.utc).isoformat(),
            'fulfillmentLineItems': {'nodes': [{'quantity': 1, 'lineItem': {'id': line_id}}]},
        }]
        assignment = snapshot['fulfillmentOrders']['nodes'][0]
        assignment['lineItems'] = {'nodes': [{'remainingQuantity': 2, 'lineItem': {'id': line_id}}]}

        self.env['qing.shopify.opening.commitment'].create({
            'store_id': self.store.id, 'external_order': snapshot['id'], 'external_line_id': line_id,
            'product_id': self.product.id, 'quantity': 2, 'sale_line_id': sale.order_line.id,
        })
        binding = self._inventory_binding()
        self.assertEqual(self.store._shopify_stock_values(binding), (8, 2))
        order = self.store._apply_shopify_order(snapshot)
        self.assertEqual(order.line_ids.sale_line_id, sale.order_line)
        self.assertEqual(self.store._shopify_stock_values(binding), (8, 2))

        backorder.action_assign()
        backorder.move_ids.quantity = 1
        self.assertEqual(self.store._shopify_stock_values(binding), (8, 2))
        backorder.do_unreserve()
        self.assertEqual(self.store._shopify_stock_values(binding), (8, 2))
        backorder.action_assign()
        backorder.move_ids.quantity = 1
        backorder.move_ids.picked = True
        backorder.with_context(skip_backorder=True).button_validate()
        self.assertEqual(backorder.state, 'done')
        self.assertGreaterEqual(backorder.move_ids.date, cutoff)
        self.assertEqual(self.store._shopify_stock_values(binding), (8, 2))

        snapshot['fulfillments'].append({
            'status': 'SUCCESS', 'createdAt': backorder.move_ids.date.replace(tzinfo=timezone.utc).isoformat(),
            'fulfillmentLineItems': {'nodes': [{'quantity': 1, 'lineItem': {'id': line_id}}]},
        })
        assignment['lineItems']['nodes'][0]['remainingQuantity'] = 1
        self.store._apply_shopify_order(snapshot)
        self.assertEqual(self.store._shopify_stock_values(binding), (8, 2))

    def test_opening_commitment_requires_sale_from_the_cutover_warehouse(self):
        other_warehouse = self.env['stock.warehouse'].create({
            'name': 'Shopify other warehouse', 'code': 'SOTHR', 'company_id': self.env.company.id,
        })
        sale = self.env['sale.order'].create({
            'partner_id': self.env.company.partner_id.id, 'warehouse_id': other_warehouse.id,
            'order_line': [Command.create({'product_id': self.product.id, 'product_uom_qty': 2})],
        })
        with self.assertRaisesRegex(ValidationError, 'must use the Shopify cutover warehouse'):
            self.env['qing.shopify.opening.commitment'].create({
                'store_id': self.store.id, 'external_order': 'gid://shopify/Order/2',
                'external_line_id': 'gid://shopify/LineItem/3', 'product_id': self.product.id,
                'quantity': 2, 'sale_line_id': sale.order_line.id,
            })

    def _fully_delivered_shopify_opening_snapshot(self):
        snapshot = self._snapshot()
        cutoff = self.store.cutover_at.replace(tzinfo=timezone.utc)
        snapshot['createdAt'] = (cutoff - timedelta(days=75)).isoformat()
        detail = snapshot['lineItems']['nodes'][0]
        snapshot['fulfillments'] = [{
            'status': 'SUCCESS', 'createdAt': (cutoff - timedelta(days=70)).isoformat(),
            'fulfillmentLineItems': {'nodes': [{'quantity': 2, 'lineItem': {'id': detail['id']}}]},
        }]
        assignment = snapshot['fulfillmentOrders']['nodes'][0]
        assignment.update(status='CLOSED', lineItems={'nodes': [
            {'remainingQuantity': 0, 'lineItem': {'id': detail['id']}},
        ]})
        assignment['assignedLocation']['location']['id'] = 'gid://shopify/Location/2'
        return snapshot

    def test_fulfilled_deleted_product_does_not_block_cutover_until_a_later_restock(self):
        binding = self._inventory_binding()
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 10)
        snapshot = self._fully_delivered_shopify_opening_snapshot()
        snapshot['lineItems']['nodes'][0]['variant'] = None
        order = self.store._apply_shopify_order(snapshot)
        claim = self.env['qing.channel.stock.claim'].search([('line_id', '=', order.line_ids.id)])
        self.assertEqual(self.store._shopify_stock_values(binding), (10, 0))
        self.assertTrue(claim.shopify_opening_fulfilled_only)
        self.assertFalse(claim.product_id)
        self.assertTrue(self.env['qing.channel.exception'].search_count([
            ('object_model', '=', order.line_ids._name), ('object_id', '=', order.line_ids.id),
            ('code', '=', 'shopify_variant_deleted_or_unavailable'), ('resolved', '=', False),
        ]))

        self.store.shopify_open_orders_verified = True
        self._advance_inventory_scan(fields.Datetime.now())
        with patch.object(type(self.store), '_shopify_inventory', return_value=10):
            self.store.action_shopify_prepare_inventory()
            self.store.action_shopify_enable_inventory()
        self.assertTrue(self.store.onboarded)
        self.assertEqual(binding.last_quantity, 10)

        returned = self._refund_snapshot(snapshot, None, [], restock='RETURN')
        refund = returned['refunds'][0]
        refund['refundLineItems']['nodes'][0].update(restocked=True, location=None)
        refund['processedAt'] = (self.store.cutover_at - timedelta(days=1)).replace(tzinfo=timezone.utc).isoformat()
        self.store._apply_shopify_order(returned)
        self.assertEqual(self.store._shopify_stock_values(binding), (10, 0))
        self.assertTrue(claim.shopify_opening_fulfilled_only)
        refund['processedAt'] = returned['updatedAt']
        self.store._apply_shopify_order(returned)
        self.store._cron_shopify_inventory()
        self.assertFalse(claim.shopify_opening_fulfilled_only)
        self.assertEqual(binding.stock_hold_reason, 'shopify_stock_unmapped_commitment')

    def test_fulfilled_opening_order_ignores_old_location_but_checks_new_restock_location(self):
        binding = self._inventory_binding()
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 10)
        snapshot = self._fully_delivered_shopify_opening_snapshot()
        order = self.store._apply_shopify_order(snapshot)
        claim = self.env['qing.channel.stock.claim'].search([('line_id', '=', order.line_ids.id)])
        self.assertEqual(claim.product_id, self.product)
        self.assertTrue(claim.location_uncertain)
        self.assertEqual(self.store._shopify_stock_values(binding), (10, 0))

        returned = self._refund_snapshot(snapshot, None, [], restock='RETURN')
        returned['fulfillmentOrders']['nodes'][0]['assignedLocation']['location']['id'] = self.store.shopify_location
        detail = returned['refunds'][0]['refundLineItems']['nodes'][0]
        detail.update(restocked=True, location=None)
        self.store._apply_shopify_order(returned)
        self.assertFalse(claim.shopify_opening_fulfilled_only)
        self.assertTrue(claim.location_uncertain)
        with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_stock_commitment_location_uncertain'):
            self.store._shopify_stock_values(binding)

    def _advance_inventory_scan(self, through):
        return self.env['qing.channel.cursor'].create({
            'store_id': self.store.id, 'stream': 'orders', 'through': through,
        })

    def _one_ordered_unit(self, index=1):
        snapshot = self._snapshot()
        snapshot['id'] = f'gid://shopify/Order/{index}'
        detail = snapshot['lineItems']['nodes'][0]
        detail.update(id=f'gid://shopify/LineItem/{index}', quantity=1, currentQuantity=1)
        return self.store._apply_shopify_order(snapshot)

    def test_cutover_requires_current_report_and_confirms_observed_quantity(self):
        binding = self._inventory_binding()
        self.store.shopify_open_orders_verified = True
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 10)
        with self.assertRaisesRegex(UserError, 'initial Shopify order scan'):
            self.store.action_shopify_prepare_inventory()
        self._advance_inventory_scan(fields.Datetime.now())
        with self.assertRaisesRegex(UserError, 'Prepare and review'):
            self.store.action_shopify_enable_inventory()
        with patch.object(type(self.store), '_shopify_inventory', return_value=9):
            report = self.store.action_shopify_prepare_inventory()
        self.assertEqual(report['domain'], [('id', 'in', [binding.id])])
        self.assertEqual(binding.shopify_preview_difference, 1)
        self.assertFalse(self.store.onboarded)
        with patch.object(type(self.store), '_shopify_inventory', return_value=8):
            with self.assertRaisesRegex(UserError, 'Inventory changed'):
                self.store.action_shopify_enable_inventory()
            self.assertEqual(binding.last_quantity, 10)
            self.store.action_shopify_prepare_inventory()
            self.store.action_shopify_enable_inventory()
        self.assertTrue(self.store.onboarded)
        self.assertEqual(binding.last_quantity, 8)
        self.assertEqual(binding.quantity_version, 2)

    def test_stale_inventory_write_waits_for_new_order_then_uses_new_key(self):
        binding = self._inventory_binding()
        self.store.onboarded = True
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 11)
        action = self.env['qing.channel.action']._ensure_action(
            self.store, 'test:stale-inventory', 'stock', data={'binding': binding.id})
        self.store._prepare_inventory_request(action, binding, 11, 0)
        request = action.request_ids
        client = type('InventoryClient', (), {})()
        with (patch.object(type(self.store), '_shopify_client', return_value=client),
              patch.object(type(self.store), '_shopify_inventory', return_value=9),
              patch.object(self.env.cr, 'commit'),
              patch.object(type(client), 'query', create=True, return_value={
                  'inventorySetQuantities': {'userErrors': [{'code': 'CHANGE_FROM_QUANTITY_STALE'}]},
              }) as send):
            with TestCase().assertRaisesRegex(DeferredChannelError, 'waiting_for_order_scan'):
                self.store._send_inventory_request(request, recovering=False)
            variables = send.call_args.args[1]
            self.assertEqual(variables['input']['quantities'][0]['changeFromQuantity'], 10)
            self.assertTrue(request.confirmed_rejection)
            self.assertEqual(request.reconcile_quantity, 9)
            self.assertEqual(binding.last_quantity, 10)
            self._one_ordered_unit()
            self._advance_inventory_scan(request.reconcile_after)
            self.assertEqual(self.store._send_inventory_request(request, recovering=True), 'superseded')
            send.assert_called_once()
        next_request = action.request_ids - request
        self.assertNotEqual(next_request.idempotency_key, request.idempotency_key)
        self.assertEqual((next_request.payload['baseline'], next_request.payload['target']), (9, 10))
        self.assertEqual(binding.shopify_confirmed_book, 1)

    def test_third_stock_conflict_preserves_baseline_and_recheck_prepares_a_new_request(self):
        binding = self._inventory_binding()
        self.store.onboarded = True
        Quant = self.env['stock.quant']
        Quant._update_available_quantity(self.product, self.warehouse.lot_stock_id, 12)
        cursor = self._advance_inventory_scan(self.store.cutover_at)
        self.store._cron_shopify_inventory()
        actions = self.env['qing.channel.action']
        stock_actions = [('store_id', '=', self.store.id), ('kind', '=', 'stock')]
        action = actions.search(stock_actions)
        self.assertEqual(self.store._execute_channel_action(action), 'external')
        action.write({'state': 'external', 'tries': 2})
        client = MagicMock()
        client.query.return_value = {'inventorySetQuantities': {'userErrors': [{'code': 'CHANGE_FROM_QUANTITY_STALE'}]}}
        with (patch.object(self.env.cr, 'commit'),
              patch.object(type(self.store), '_shopify_client', return_value=client),
              patch.object(type(self.store), '_shopify_inventory') as inventory):
            for index in range(1, 4):
                request = action.request_ids.filtered(lambda row: row.state == 'ready')
                self.assertEqual(request.payload['baseline'], 11 - index)
                inventory.return_value = 10 - index
                with TestCase().assertRaisesRegex(DeferredChannelError, 'waiting_for_order_scan'):
                    self.store._send_inventory_request(request, recovering=False)
                self.assertTrue(request.confirmed_rejection)
                self._one_ordered_unit(index)
                cursor.through = request.reconcile_after
                request._process_one()
                self.assertEqual(binding.last_quantity, 10 - index)
                self.assertEqual(binding.shopify_confirmed_book, index)
                self.assertEqual(request.state, 'review' if index == 3 else 'superseded')

            self.assertEqual(action.state, 'review')
            self.assertEqual(action.tries, 2)
            self.assertEqual(len(action.request_ids), 3)
            self.assertEqual(client.query.call_count, 3)
            self.store._cron_shopify_inventory()
            self.assertEqual(actions.search(stock_actions), action)
            self.assertFalse(action.request_ids.filtered(lambda row: row.state == 'ready'))

            ExceptionModel = self.env['qing.channel.exception']
            blocked = ExceptionModel.search([
                ('store_id', '=', self.store.id), ('code', '=', 'shopify_inventory_conflict_limit'),
                ('resolved', '=', False),
            ])
            request_issue = blocked.filtered(lambda item: item.object_model == request._name)
            self.assertEqual(len(blocked), 2)
            self.assertEqual(request_issue.object_id, request.id)
            user = new_test_user(self.env, login='shopify-conflict-user', groups='base.group_user',
                                 company_id=self.env.company.id)
            with self.assertRaises(AccessError):
                request_issue.with_user(user).action_recheck()
            self.assertEqual(action.state, 'review')
            manager = new_test_user(self.env, login='shopify-conflict-manager',
                                    groups='qing_channel.group_channel_manager', company_id=self.env.company.id)
            Quant._update_available_quantity(self.product, self.warehouse.lot_stock_id, 1)
            self.assertTrue(request_issue.with_user(manager).action_recheck())
            self.assertTrue(all(blocked.mapped('resolved')))
            self.assertEqual(request.state, 'superseded')
            self.assertEqual(action.state, 'pending')
            self.assertEqual(action.tries, 0)
            self.assertEqual(client.query.call_count, 3)

            action._process_one()
            replacement = action.request_ids.filtered(lambda row: row.state == 'ready')
            self.assertEqual((replacement.payload['baseline'], replacement.payload['target']), (7, 10))
            self.assertNotIn(replacement.idempotency_key, (action.request_ids - replacement).mapped('idempotency_key'))

            inventory.return_value = 6
            with TestCase().assertRaisesRegex(DeferredChannelError, 'waiting_for_order_scan'):
                self.store._send_inventory_request(replacement, recovering=False)
            self._one_ordered_unit(4)
            cursor.through = replacement.reconcile_after
            replacement._process_one()
            self.assertEqual(replacement.state, 'superseded')
            self.assertEqual(binding.last_quantity, 6)

            final_request = action.request_ids.filtered(lambda row: row.state == 'ready')
            self.assertEqual((final_request.payload['baseline'], final_request.payload['target']), (6, 9))
            client.query.return_value = {'inventorySetQuantities': {
                'inventoryAdjustmentGroup': {'referenceDocumentUri': 'qa'}, 'userErrors': [],
            }}
            final_request._process_one()
            self.assertEqual(client.query.call_count, 5)
            self.assertEqual(final_request.state, 'success')
            self.assertEqual(action.state, 'done')
            self.assertEqual(binding.last_quantity, 9)

    def test_expired_unknown_inventory_write_may_have_applied_before_next_order(self):
        binding = self._inventory_binding()
        self.store.onboarded = True
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 9)
        action = self.env['qing.channel.action']._ensure_action(
            self.store, 'test:unknown-inventory', 'stock', data={'binding': binding.id})
        self.store._prepare_inventory_request(action, binding, 9, 0)
        request = action.request_ids
        request.first_sent_at = fields.Datetime.now() - timedelta(hours=25)
        with (patch.object(type(self.store), '_shopify_inventory', return_value=8),
              patch.object(type(self.store), '_shopify_client', side_effect=AssertionError('Unexpected write')),
              patch.object(self.env.cr, 'commit')):
            with TestCase().assertRaisesRegex(DeferredChannelError, 'waiting_for_order_scan'):
                self.store._send_inventory_request(request, recovering=True)
            self._one_ordered_unit()
            cursor = self._advance_inventory_scan(request.reconcile_after)
            request.confirmed_rejection = True
            with self.assertRaisesRegex(ChannelReviewRequired, 'inventory_difference'):
                self.store._send_inventory_request(request, recovering=True)
            request.confirmed_rejection = False
            self.assertEqual(self.store._send_inventory_request(request, recovering=True), 'satisfied')
        self.assertTrue(cursor.through)
        self.assertEqual((binding.last_quantity, binding.shopify_confirmed_book), (8, 1))
        self.assertEqual(action.request_ids, request)

    def test_recover_inventory_activation_from_remote_level(self):
        binding = self._inventory_binding()
        binding.action_shopify_activate_inventory()
        action = self.env['qing.channel.action'].search([('kind', '=', 'inventory_activation'),
                                                        ('store_id', '=', self.store.id)])
        self.assertEqual(self.store._execute_channel_action(action), 'external')
        request = action.request_ids
        request.first_sent_at = fields.Datetime.now()
        with (patch.object(type(self.store), '_shopify_inventory_level', return_value={'id': 'test'}),
              patch.object(type(self.store), '_shopify_client', side_effect=AssertionError('Unexpected write'))):
            self.assertEqual(self.store._send_activation_request(request, recovering=True), 'satisfied')
        self.assertEqual(binding.last_quantity, 10)
        self.assertFalse(self.store.onboarded)

    def test_compare_book_uses_actual_shopify_restock_at_the_mapped_location(self):
        binding = self._inventory_binding()
        snapshot = self._snapshot()
        detail = snapshot['lineItems']['nodes'][0]
        detail['currentQuantity'] = 1
        refund_detail = {'id': 'gid://shopify/RefundLineItem/1', 'lineItem': {'id': detail['id']}, 'quantity': 1,
                         'restocked': False, 'restockType': 'NO_RESTOCK', 'location': None}
        snapshot['refunds'] = [{'id': 'gid://shopify/Refund/1', 'processedAt': snapshot['updatedAt'],
                               'refundLineItems': {'nodes': [refund_detail]}, 'transactions': {'nodes': []}}]
        self.store._apply_shopify_order(snapshot)
        self.assertEqual(self.store._shopify_stock_values(binding)[1], 2)
        refund_detail.update(restocked=True, restockType='RETURN', location={'id': 'gid://shopify/Location/2'})
        self.store._apply_shopify_order(snapshot)
        self.assertEqual(self.store._shopify_stock_values(binding)[1], 2)
        refund_detail['location'] = {'id': self.store.shopify_location}
        self.store._apply_shopify_order(snapshot)
        self.assertEqual(self.store._shopify_stock_values(binding)[1], 1)

    def test_configured_rounding_preserves_tax_base_and_tax_line(self):
        self.store.shopify_apply_tax_rounding = True
        self.env.company.account_fiscal_country_id = self.env.ref('base.us')
        tax_group = self.env['account.tax.group'].create({
            'name': 'Pilot Tax', 'company_id': self.env.company.id,
        })
        tax = self.env['account.tax'].create({
            'name': 'Channel Sales Tax', 'amount': 10, 'amount_type': 'percent', 'tax_group_id': tax_group.id,
            'company_id': self.env.company.id, 'price_include_override': 'tax_excluded',
        })
        self.env['qing.channel.mapping'].create({
            'store_id': self.store.id, 'kind': 'tax',
            'external_key': 'US|CA|shopify|Sales Tax|excluded|merchant', 'tax_id': tax.id,
        })
        snapshot = self._snapshot()
        snapshot['lineItems']['nodes'][0]['taxLines'] = [{
            'title': 'Sales Tax', 'source': 'shopify', 'rate': 0.1, 'channelLiable': False,
            'priceSet': self._money('2.41'),
        }]
        snapshot['currentTotalPriceSet'] = self._money('26.41')
        snapshot['currentTotalTaxSet'] = self._money('2.41')
        order = self.store._apply_shopify_order(snapshot)
        self.store._execute_channel_action(self._sale_action(order))
        self.assertEqual(order.sale_id.amount_untaxed, 24)
        self.assertEqual(order.sale_id.amount_tax, 2.41)
        self.assertEqual(order.sale_id.order_line.tax_ids, tax)

    def _set_shopify_rounded_taxes(self, snapshot):
        self.store.shopify_apply_tax_rounding = True
        company = self.env.company
        company.account_fiscal_country_id = self.env.ref('base.us')
        snapshot['currentTotalPriceSet'] = self._money('27.60')
        snapshot['currentTotalTaxSet'] = self._money('3.60')
        tax_rows, tax_accounts = [], {}
        for number, (title, rate, tax_amount, channel_liable) in enumerate((
            ('Merchant sales tax', 10, '2.41', False), ('Marketplace tax', 5, '1.19', True),
        ), 1):
            role = 'channel' if channel_liable else 'merchant'
            mode = 'included' if snapshot['taxesIncluded'] else 'excluded'
            group = self.env['account.tax.group'].create({'name': title, 'company_id': company.id})
            account = self.env['account.account'].create({
                'name': title, 'code': f'Q2635T{number}', 'account_type': 'liability_current',
                'company_ids': [Command.set(company.ids)],
            })
            tax = self.env['account.tax'].create({
                'name': title, 'amount': rate, 'amount_type': 'percent', 'company_id': company.id,
                'tax_group_id': group.id, 'price_include_override': f'tax_{mode}',
            })
            (tax.invoice_repartition_line_ids | tax.refund_repartition_line_ids).filtered(
                lambda line: line.repartition_type == 'tax').account_id = account
            tax_accounts[tax.id] = account
            self.env['qing.channel.mapping'].create({
                'store_id': self.store.id, 'kind': 'tax', 'tax_id': tax.id,
                'external_key': f'US|CA|shopify|{title}|{mode}|{role}',
            })
            tax_rows.append({'title': title, 'source': 'shopify', 'rate': rate / 100,
                             'channelLiable': channel_liable, 'priceSet': self._money(tax_amount)})
        snapshot['lineItems']['nodes'][0]['taxLines'] = tax_rows
        return tax_accounts

    def test_split_invoices_preserve_each_shopify_tax_and_cumulative_rounding(self):
        self._configure_shopify_accounting('shipment', 'delivery')
        snapshot = self._snapshot()
        snapshot['transactions'] = [self._shopify_order_transaction(1, 'AUTHORIZATION')]
        tax_accounts = self._set_shopify_rounded_taxes(snapshot)
        order = self.store._apply_shopify_order(snapshot)
        self.store._execute_channel_action(self._sale_action(order))
        sale = order.sale_id
        self.assertEqual(sale.state, 'sale')
        self.assertEqual(sale.amount_untaxed, 24)
        self.assertEqual(sale.amount_tax, 3.60)
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 10)
        first = sale.picking_ids
        first.action_assign()
        first.move_ids.quantity = 1
        first.move_ids.picked = True
        first.with_context(skip_backorder=True).button_validate()
        second = sale.picking_ids - first

        self.store.shopify_apply_tax_rounding = False
        with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_tax_rounding_requires_review'):
            self.store._invoice_shopify_sale(order)
        self.assertFalse(sale.invoice_ids)
        self.store.shopify_apply_tax_rounding = True

        with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_invoice_tax_history_requires_review'):
            old_invoice = sale._create_invoices()
            old_invoice.action_post()
            second.action_assign()
            second.move_ids.picked = True
            second.with_context(skip_backorder=True).button_validate()
            self.store._invoice_shopify_sale(order)
        self.assertFalse(sale.invoice_ids)

        first_invoice = self.store._invoice_shopify_sale(order)
        second.action_assign()
        second.move_ids.picked = True
        second.with_context(skip_backorder=True).button_validate()
        second_invoice = self.store._invoice_shopify_sale(order)
        for invoice, expected in ((first_invoice, (1.21, 0.60)), (second_invoice, (1.20, 0.59))):
            self.assertEqual(invoice.state, 'posted')
            self.assertEqual(invoice.invoice_line_ids.price_subtotal, 12)
            tax_lines = invoice.line_ids.filtered('tax_line_id')
            by_tax = {line.tax_line_id.id: line for line in tax_lines}
            self.assertEqual(set(by_tax), set(tax_accounts))
            for tax_id, amount in zip(tax_accounts, expected):
                self.assertEqual(by_tax[tax_id].account_id, tax_accounts[tax_id])
                self.assertEqual(abs(by_tax[tax_id].amount_currency), amount)
                self.assertEqual(abs(by_tax[tax_id].tax_base_amount), 12)
        self.assertEqual(sum((first_invoice | second_invoice).mapped('amount_tax')), 3.60)

    def _configure_shopify_accounting(self, invoice_policy, product_invoice_policy):
        company = self.env.company
        self.env['account.chart.template'].try_loading('generic_coa', company)
        self.store.shopify_invoice_policy = invoice_policy
        self.product.write({'invoice_policy': product_invoice_policy, 'taxes_id': [(5,)]})
        bank = self.env['account.journal'].search([('company_id', '=', company.id), ('type', '=', 'bank')], limit=1)
        outstanding = self.env['account.account'].create({
            'name': 'Shopify Outstanding', 'code': 'Q2635', 'account_type': 'asset_current', 'reconcile': True,
            'company_ids': [(6, 0, company.ids)],
        })
        methods = bank.inbound_payment_method_line_ids | bank.outbound_payment_method_line_ids
        methods.filtered(lambda method: method.code == 'manual').payment_account_id = outstanding
        self.env['qing.channel.mapping'].create({
            'store_id': self.store.id, 'kind': 'gateway', 'external_key': 'pilot-gateway', 'journal_id': bank.id,
        })

    def _shopify_order_transaction(self, number, kind, *, parent=None, status='SUCCESS',
                                   gateway='pilot-gateway', amount=24):
        return {
            'id': f'gid://shopify/OrderTransaction/{number}', 'kind': kind, 'status': status,
            'processedAt': '2026-11-18T12:00:00Z', 'gateway': gateway, 'amountSet': self._money(str(amount)),
            'parentTransaction': {'id': parent['id']} if parent else None, 'settlementCurrency': self.CurrencyCode,
        }

    def test_voided_authorization_does_not_confirm_an_order_or_record_payment(self):
        self._configure_shopify_accounting('shipment', 'delivery')
        authorization = self._shopify_order_transaction(1, 'AUTHORIZATION')
        void = self._shopify_order_transaction(2, 'VOID', parent=authorization)
        snapshot = dict(self._snapshot(), transactions=[void, authorization])
        order = self.store._apply_shopify_order(snapshot)
        self.store._execute_channel_action(self._sale_action(order))
        self.assertEqual(order.sale_id.state, 'draft')
        self.assertFalse(order.sale_id.picking_ids)
        domain = [('order_id', '=', order.id), ('kind', '=', 'payment')]
        self.assertFalse(self.env['qing.channel.action'].search(domain))
        transactions = self.env['qing.channel.transaction'].search([('order_id', '=', order.id)])
        self.assertEqual(set(transactions.mapped('kind')), {'AUTHORIZATION', 'VOID'})
        self.assertFalse(transactions.payment_id)

        void['parentTransaction'] = None
        self.store._apply_shopify_order(snapshot)
        self.store._execute_channel_action(self._sale_action(order))
        self.assertEqual(order.sale_id.state, 'draft')
        self.assertEqual(self.env['qing.channel.exception'].search_count([
            ('store_id', '=', self.store.id), ('object_model', '=', order._name), ('object_id', '=', order.id),
            ('code', '=', 'shopify_authorization_void_requires_review'), ('resolved', '=', False),
        ]), 1)

    def test_void_of_one_authorization_preserves_another_and_a_successful_partial_capture(self):
        self._configure_shopify_accounting('shipment', 'delivery')
        old = self._shopify_order_transaction(1, 'AUTHORIZATION', gateway='old-gateway')
        old_void = self._shopify_order_transaction(2, 'VOID', parent=old, gateway='old-gateway')
        usable = self._shopify_order_transaction(3, 'AUTHORIZATION')
        snapshot = dict(self._snapshot(), transactions=[usable, old_void, old])
        order = self.store._apply_shopify_order(snapshot)
        self.store._execute_channel_action(self._sale_action(order))
        self.assertEqual(order.sale_id.state, 'sale')
        self.assertFalse(order.sale_id.invoice_ids)
        self.assertFalse(self.env['qing.channel.transaction'].search_count([
            ('order_id', '=', order.id), ('payment_id', '!=', False),
        ]))

        captured = self._shopify_order_transaction(4, 'CAPTURE', parent=usable, amount=6)
        remainder = self._shopify_order_transaction(5, 'VOID', parent=usable, amount=18)
        snapshot['transactions'] = [remainder, usable, captured, old_void, old]
        self.store._apply_shopify_order(snapshot)
        self.store._execute_channel_action(self._sale_action(order))
        payment_action = self.env['qing.channel.action'].search([
            ('order_id', '=', order.id), ('kind', '=', 'payment'), ('snapshot_version', '=', order.snapshot_version),
        ])
        self.store._execute_channel_action(payment_action)
        self.store._execute_channel_action(payment_action)
        posted = self.env['qing.channel.transaction'].search([
            ('order_id', '=', order.id), ('payment_id', '!=', False),
        ])
        self.assertEqual(posted.kind, 'CAPTURE')
        self.assertEqual(posted.payment_id.amount, 6)
        self.assertEqual(order.sale_id.state, 'sale')
        self.assertFalse(self.env['qing.channel.exception'].search_count([
            ('store_id', '=', self.store.id), ('object_model', '=', order._name), ('object_id', '=', order.id),
            ('code', '=', 'shopify_authorization_void_requires_review'), ('resolved', '=', False),
        ]))

    def test_later_void_stops_shipment_until_a_new_authorization_is_confirmed(self):
        self._configure_shopify_accounting('shipment', 'delivery')
        authorization = self._shopify_order_transaction(1, 'AUTHORIZATION')
        void = self._shopify_order_transaction(2, 'VOID', parent=authorization, status='PENDING')
        snapshot = dict(self._snapshot(), transactions=[authorization, void])
        order = self.store._apply_shopify_order(snapshot)
        self.store._execute_channel_action(self._sale_action(order))
        self.assertEqual(order.sale_id.state, 'sale')
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 10)
        picking = order.sale_id.picking_ids
        picking.action_assign()
        picking.move_ids.picked = True

        void['status'] = 'SUCCESS'
        with patch.object(type(self.store), '_sync_order_lines',
                          side_effect=ChannelReviewRequired('shopify_stock_line_quantities_inconsistent')):
            self.store._apply_shopify_order(snapshot)
        self.assertTrue(order.stock_unverified)
        observed_void = self.env['qing.channel.transaction'].search([
            ('order_id', '=', order.id), ('kind', '=', 'VOID'),
        ])
        self.assertEqual(observed_void.status, 'SUCCESS')
        issue = self.env['qing.channel.exception'].search([
            ('store_id', '=', self.store.id), ('object_model', '=', order._name), ('object_id', '=', order.id),
            ('code', '=', 'shopify_authorization_void_requires_review'), ('resolved', '=', False),
        ])
        self.assertEqual(len(issue), 1)
        self.assertEqual(order.sale_id.state, 'sale')
        with self.assertRaisesRegex(UserError, 'Shopify payment authorization was voided'):
            picking.with_context(skip_backorder=True).button_validate()
        self.assertNotEqual(picking.state, 'done')

        snapshot['transactions'].append(self._shopify_order_transaction(3, 'AUTHORIZATION'))
        self.store._apply_shopify_order(snapshot)
        self.store._execute_channel_action(self._sale_action(order))
        self.assertTrue(issue.resolved)
        picking.with_context(skip_backorder=True).button_validate()
        self.assertEqual(picking.state, 'done')
        self.assertFalse(self.env['qing.channel.transaction'].search_count([
            ('order_id', '=', order.id), ('payment_id', '!=', False),
        ]))

    def _foreign_currency_shop_order(self, invoice_date, payment_date):
        self._configure_shopify_accounting('shipment', 'order')
        currency = self.env['res.currency'].create({
            'name': 'XTS', 'symbol': 'XTS', 'rounding': 0.01,
        })
        self.store.shopify_currency_id = currency
        self.pricelist.currency_id = currency
        self.store._shopify_mapping('gateway', 'pilot-gateway').journal_id.currency_id = currency
        self.store.cutover_at = f'{invoice_date - timedelta(days=1)} 00:00:00'
        self.CurrencyCode = currency.name
        snapshot = self._snapshot()
        snapshot.update(createdAt=f'{invoice_date}T12:00:00Z', updatedAt=f'{payment_date}T12:00:00Z')
        snapshot['transactions'] = [{
            'id': 'gid://shopify/OrderTransaction/1', 'kind': 'CAPTURE', 'status': 'SUCCESS',
            'gateway': 'pilot-gateway', 'amountSet': self._money('24'),
            'processedAt': snapshot['updatedAt'], 'settlementCurrency': currency.name,
            'settlementCurrencyRate': 1,
        }]
        order = self.store._apply_shopify_order(snapshot)
        self.store._execute_channel_action(self._sale_action(order))
        action = self.env['qing.channel.action'].search([('order_id', '=', order.id), ('kind', '=', 'payment')])
        return order, snapshot, action, currency

    def test_shop_currency_documents_use_native_dated_rates_and_exchange_entries(self):
        today = fields.Date.context_today(self.store)
        invoice_date = today - timedelta(days=12)
        payment_date = today - timedelta(days=9)
        credit_date = today - timedelta(days=6)
        refund_date = today - timedelta(days=3)
        order, snapshot, action, currency = self._foreign_currency_shop_order(invoice_date, payment_date)
        company = self.env.company
        rates = self.env['res.currency.rate']
        for day, rate in ((invoice_date, 1), (payment_date, 0.5), (credit_date, 0.8), (refund_date, 1.2)):
            rates.create({
                'currency_id': currency.id, 'company_id': company.root_id.id,
                'name': day - timedelta(days=1), 'rate': rate,
            })
        exchange_moves = self.env['account.move']
        exchange_domain = [('company_id', '=', company.id), ('journal_id', '=', company.currency_exchange_journal_id.id)]
        before = exchange_moves.search(exchange_domain)
        self.assertTrue(company.currency_exchange_journal_id)
        invoice = self.store._invoice_shopify_sale(order, invoice_date=invoice_date)
        self.store._execute_channel_action(action)
        capture = self.env['qing.channel.transaction'].browse(action.data['transaction'])
        self.assertEqual(invoice.invoice_date, invoice_date)
        self.assertEqual(capture.payment_id.date, payment_date)
        self.assertEqual(invoice.currency_id, currency)
        self.assertEqual(invoice.amount_total, 24)
        self.assertEqual(invoice.amount_residual, 0)
        self.assertEqual(capture.payment_id.currency_id, currency)
        self.assertEqual(capture.payment_id.amount, 24)
        self.assertEqual(order.snapshot['currentTotalPriceSet']['presentmentMoney']['currencyCode'], 'CAD')
        self.assertEqual(capture.snapshot['settlementCurrencyRate'], 1)

        revision = self._refund_snapshot(snapshot, capture, [(12, 'SUCCESS')])
        refund_data = revision['refunds'][0]
        refund_data['processedAt'] = f'{invoice_date - timedelta(days=3)}T12:00:00Z'
        refund_data['transactions']['nodes'][0]['processedAt'] = f'{refund_date}T12:00:00Z'
        action = self._apply_refund_snapshot(revision, order)
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_accounting_currency_rate_missing'):
            self.store._execute_channel_action(action)
        self.assertFalse(refund.credit_note_id)

        refund_data['processedAt'] = f'{credit_date}T12:00:00Z'
        action = self._apply_refund_snapshot(revision, order)
        self.store._execute_channel_action(action)
        credit = refund.credit_note_id
        payout = refund.transaction_ids.payment_id
        self.assertEqual(credit.invoice_date, credit_date)
        self.assertEqual(payout.date, refund_date)
        self.assertEqual(credit.currency_id, currency)
        self.assertEqual(credit.amount_total, 12)
        self.assertEqual(credit.amount_residual, 0)
        self.assertEqual(payout.currency_id, currency)
        self.assertEqual(payout.amount, 12)
        documents = (invoice, capture.payment_id.move_id, credit, payout.move_id)
        company_receivables = [
            move.line_ids.filtered(lambda line: line.account_id.account_type == 'asset_receivable').balance
            for move in documents
        ]
        self.assertEqual(company_receivables, [24, -48, -15, 10])
        exchange_accounts = company.income_currency_exchange_account_id | company.expense_currency_exchange_account_id
        realised = (exchange_moves.search(exchange_domain) - before).line_ids.filtered(
            lambda line: line.account_id in exchange_accounts,
        )
        self.assertEqual(sorted(abs(line.balance) for line in realised), [5, 24])

    def test_currency_without_an_applicable_rate_holds_invoice_and_capture(self):
        document_date = fields.Date.context_today(self.store) - timedelta(days=4)
        order, _snapshot, action, currency = self._foreign_currency_shop_order(document_date, document_date)
        tx = self.env['qing.channel.transaction'].browse(action.data['transaction'])
        with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_accounting_currency_rate_missing'):
            self.store._execute_channel_action(action)
        future = document_date + timedelta(days=1)
        self.env['res.currency.rate'].create({
            'name': future, 'company_id': self.env.company.root_id.id, 'currency_id': currency.id, 'rate': 0.5,
        })
        self.assertEqual(currency._get_rates(self.env.company, document_date)[currency.id][1], future)
        with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_accounting_currency_rate_missing'):
            self.store._invoice_shopify_sale(order, invoice_date=document_date)
        with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_accounting_currency_rate_missing'):
            self.store._execute_channel_action(action)
        self.store.shopify_currency_id = self.env.company.currency_id
        with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_accounting_currency_rate_missing'):
            self.store._invoice_shopify_sale(order, invoice_date=document_date)
        self.assertFalse(order.sale_id.invoice_ids)
        self.assertFalse(tx.payment_id)

    def _capture_fixture(self, invoice_policy='capture', product_invoice_policy='order', shipping=False,
                         rounded_taxes=False, taxes_included=False):
        self._configure_shopify_accounting(invoice_policy, product_invoice_policy)
        snapshot = self._snapshot()
        tx_data = {'id': 'gid://shopify/OrderTransaction/1', 'kind': 'CAPTURE', 'status': 'SUCCESS',
                   'gateway': 'pilot-gateway', 'amountSet': self._money('24.00'),
                   'processedAt': '2026-11-18T12:00:00Z', 'settlementCurrency': self.CurrencyCode}
        if shipping:
            price = self._money('5')
            carriage = self.env['product.product'].create({
                'name': 'Shipping', 'type': 'service', 'invoice_policy': 'order', 'taxes_id': [(5,)],
            })
            self.env['qing.channel.mapping'].create({
                'store_id': self.store.id, 'kind': 'shipping', 'external_key': 'GROUND', 'product_id': carriage.id,
            })
            snapshot['shippingLines']['nodes'] = [{
                'id': 'gid://shopify/ShippingLine/1', 'title': 'Ground', 'code': 'GROUND',
                'currentDiscountedPriceSet': price, 'taxLines': [],
            }]
            snapshot['currentTotalPriceSet'] = tx_data['amountSet'] = self._money('29')
        if rounded_taxes:
            snapshot['taxesIncluded'] = taxes_included
            self._set_shopify_rounded_taxes(snapshot)
            if shipping:
                tax_rows = snapshot['lineItems']['nodes'][0]['taxLines']
                shipment = snapshot['shippingLines']['nodes'][0]
                shipment['taxLines'] = [dict(tax, priceSet=self._money(amount))
                                        for tax, amount in zip(tax_rows, ('0.51', '0.24'), strict=True)]
                snapshot['currentTotalTaxSet'] = self._money('4.35')
                snapshot['currentTotalPriceSet'] = self._money('33.35')
            tx_data['amountSet'] = snapshot['currentTotalPriceSet']
        snapshot['transactions'] = [tx_data]
        order = self.store._apply_shopify_order(snapshot)
        self.store._execute_channel_action(self._sale_action(order))
        payment_action = self.env['qing.channel.action'].search([('order_id', '=', order.id), ('kind', '=', 'payment')])
        self.store._execute_channel_action(payment_action)
        tx = self.env['qing.channel.transaction'].browse(payment_action.data['transaction'])
        return order, snapshot, tx, payment_action

    def _opening_financial_order(self, fulfilled, pending, returned=False):
        self._configure_shopify_accounting('shipment', 'delivery')
        snapshot = self._snapshot()
        snapshot.update(createdAt='2026-09-04T12:00:00Z', updatedAt='2026-09-09T12:00:00Z',
                        originalTotalPriceSet=self._money('24'), totalTaxSet=self._money('0'))
        line = snapshot['lineItems']['nodes'][0]
        line.update(unfulfilledQuantity=pending, originalTotalSet=self._money('30'),
                    discountAllocations=[{'allocatedAmountSet': self._money('6')}])
        snapshot['transactions'] = [{
            'id': 'gid://shopify/OrderTransaction/1', 'kind': 'CAPTURE', 'status': 'SUCCESS',
            'gateway': 'pilot-gateway', 'amountSet': self._money('24'),
            'processedAt': snapshot['createdAt'], 'settlementCurrency': self.CurrencyCode,
        }]
        assignment = snapshot['fulfillmentOrders']['nodes'][0]
        assignment.update(status='OPEN' if pending else 'CLOSED',
                          lineItems={'nodes': [{'remainingQuantity': pending, 'lineItem': {'id': line['id']}}]})
        snapshot['fulfillments'] = [{
            'status': 'SUCCESS', 'createdAt': '2026-09-05T12:00:00Z',
            'fulfillmentLineItems': {'nodes': [{'quantity': fulfilled, 'lineItem': {'id': line['id']}}]},
        }]
        if returned:
            order = self.store._apply_shopify_order(snapshot)
            parent = self.env['qing.channel.transaction'].search([('order_id', '=', order.id)])
            snapshot = self._refund_snapshot(snapshot, parent, [(12, 'SUCCESS')], restock='RETURN')
            snapshot['updatedAt'] = snapshot['refunds'][0]['processedAt'] = '2026-09-10T12:00:00Z'
            snapshot['refunds'][0]['transactions']['nodes'][0]['processedAt'] = snapshot['updatedAt']
            refund_line = snapshot['refunds'][0]['refundLineItems']['nodes'][0]
            refund_line.update(restocked=True, location={'id': self.store.shopify_location})
        return self.store._apply_shopify_order(snapshot)

    def _request_opening_finance(self, order):
        manager = new_test_user(self.env, login='shopify-opening-accountant-2635',
                                groups='account.group_account_manager,qing_channel.group_channel_user',
                                company_id=self.env.company.id)
        order.with_user(manager).action_shopify_backfill_finance()
        return self.env['qing.channel.action'].search([('order_id', '=', order.id)])

    def test_history_backfill_posts_original_sale_then_refund_without_moving_opening_stock(self):
        order = self._opening_financial_order(fulfilled=2, pending=0, returned=True)
        Quant = self.env['stock.quant']
        Quant._update_available_quantity(self.product, self.warehouse.lot_stock_id, 10)
        actions = self.env['qing.channel.action']
        self.assertFalse(actions.search([('order_id', '=', order.id)]))
        self.assertFalse(order.sale_id)
        actions = self._request_opening_finance(order)
        opening = actions.filtered(lambda action: action.kind == 'opening_finance')
        self.assertEqual(len(opening), 1)
        verification = actions.filtered(lambda action: action.kind == 'refund_verify')
        self.assertEqual(len(verification), 1)
        self.assertFalse(verification.dependency_ids)
        posting = actions.filtered(lambda action: action.kind == 'refund')
        self.assertEqual(posting.dependency_ids, opening | verification)
        self.assertEqual((actions - opening - verification - posting).dependency_ids, opening)
        self.assertNotIn('return', actions.mapped('kind'))
        with patch.object(self.env.cr, 'commit'):
            opening._process_one()
        self.assertEqual(opening.state, 'done')
        sale = order.sale_id
        bill = sale.invoice_ids
        self.assertEqual(sale.order_line.qty_delivered, 2)
        self.assertEqual(bill.state, 'posted')
        self.assertEqual(bill.amount_total, 24)
        self.assertEqual(fields.Date.to_string(bill.invoice_date), '2026-09-04')
        self.assertFalse(sale.order_line.move_ids)
        salesperson = new_test_user(self.env, login='shopify-opening-salesperson-2635',
                                    groups='sales_team.group_sale_manager', company_id=self.env.company.id)
        with self.assertRaises(AccessError):
            sale.with_user(salesperson).write({'shopify_historical_finance': False})
        with self.assertRaises(AccessError):
            sale.order_line.with_user(salesperson).write({'shopify_opening_stock_covered_qty': 0})
        with patch.object(self.env.cr, 'commit'):
            for kind in ('refund_verify', 'payment', 'refund_payment', 'refund'):
                actions.filtered(lambda action: action.kind == kind)._process_one()
        self.assertEqual(set(actions.mapped('state')), {'done'})
        refund = self.env['qing.channel.refund'].search([('order_id', '=', order.id)])
        self.assertEqual(refund.credit_note_id.amount_total, 12)
        self.assertEqual(refund.credit_note_id.amount_residual, 0)
        self.assertEqual(bill.amount_residual, 0)
        self.assertFalse(refund.return_picking_ids)
        self.assertEqual(self.product.with_context(warehouse_id=self.warehouse.id).qty_available, 10)
        self.assertEqual(self.store._execute_channel_action(opening), 'obsolete')
        self.assertEqual(order.sale_id, sale)

    def test_history_backfill_opens_only_the_pending_shipment_even_after_an_old_return(self):
        order = self._opening_financial_order(fulfilled=1, pending=1, returned=True)
        self.store.shopify_open_orders_verified = True
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 10)
        binding = self._inventory_binding()
        self.assertEqual(self.store._shopify_stock_values(binding), (9, 1))
        actions = self._request_opening_finance(order)
        opening = actions.filtered(lambda action: action.kind == 'opening_finance')
        self.store._execute_channel_action(opening)
        sale = order.sale_id
        self.assertEqual(sale.invoice_ids.invoice_line_ids.quantity, 2)
        self.assertEqual(sale.order_line.qty_delivered, 1)
        self.assertEqual(len(sale.picking_ids), 1)
        self.assertEqual(sale.picking_ids.move_ids.product_uom_qty, 1)
        self.assertEqual(self.product.with_context(warehouse_id=self.warehouse.id).qty_available, 10)
        self.assertEqual(self.store._shopify_stock_values(binding), (9, 1))

    def test_pending_capture_without_webhook_refreshes_unchanged_order_version_and_posts_once(self):
        self._configure_shopify_accounting('capture', 'order')
        snapshot = self._snapshot()
        pending = self._shopify_order_transaction(1, 'CAPTURE', status='PENDING')
        snapshot['transactions'] = [pending]
        order = self.store._apply_shopify_order(snapshot)
        self.store._execute_channel_action(self._sale_action(order))
        actions = self.env['qing.channel.action']
        scope = [('order_id', '=', order.id)]
        verification = actions.search([*scope, ('kind', '=', 'payment_verify')])
        self.assertEqual(len(verification), 1)
        self.assertGreater(verification.retry_at, fields.Datetime.now())
        self.assertEqual(order.sale_id.state, 'draft')
        self.assertFalse(order.sale_id.invoice_ids)

        current = {'updatedAt': snapshot['updatedAt'], 'transactions': [pending]}
        client = MagicMock()
        client.query.return_value = {'order': current}
        with (patch.object(type(self.store), '_shopify_client', return_value=client),
              patch.object(store_order, 'fetch_order') as read_order):
            with TestCase().assertRaisesRegex(DeferredChannelError, 'shopify_payment_pending') as delayed:
                self.store._execute_channel_action(verification)
            self.assertEqual(delayed.exception.retry_after, 300)
            read_order.assert_not_called()

            before = order.snapshot_version
            captured = dict(pending, status='SUCCESS')
            current['transactions'] = [captured]
            read_order.return_value = dict(snapshot, transactions=[captured])
            self.assertEqual(self.store._execute_channel_action(verification), 'obsolete')
            read_order.assert_called_once_with(client, order.external_id)
            self.assertEqual(order.snapshot_version, before + 1)
            self.assertEqual(order.external_updated_at, orders.parse_date(snapshot['updatedAt']))
            self.assertEqual(self.store._execute_channel_action(verification), 'obsolete')

        self.store._execute_channel_action(self._sale_action(order))
        payment = actions.search([*scope, ('kind', '=', 'payment')])
        self.assertEqual(len(payment), 1)
        self.store._execute_channel_action(payment)
        transaction = self.env['qing.channel.transaction'].browse(payment.data['transaction'])
        posted = transaction.payment_id
        self.assertEqual(posted.amount, 24)
        self.assertEqual(order.sale_id.invoice_ids.state, 'posted')
        self.assertEqual(order.sale_id.invoice_ids.amount_residual, 0)
        self.store._execute_channel_action(payment)
        self.assertEqual(transaction.payment_id, posted)
        self.assertEqual(len(order.sale_id.invoice_ids), 1)

    def test_capture_state_syncs_during_stock_review_and_posts_after_recheck(self):
        self._configure_shopify_accounting('capture', 'order')
        pending = self._shopify_order_transaction(1, 'CAPTURE', status='PENDING')
        snapshot = dict(self._snapshot(), transactions=[pending])
        stock_review = ChannelReviewRequired('shopify_stock_line_quantities_inconsistent')
        actions = self.env['qing.channel.action']
        with patch.object(type(self.store), '_update_stock_claim', side_effect=stock_review):
            order = self.store._apply_shopify_order(snapshot)
            scope = [('order_id', '=', order.id)]
            tx = self.env['qing.channel.transaction'].search(scope)
            self.assertEqual(tx.status, 'PENDING')
            self.assertTrue(order.stock_unverified)
            self.assertFalse(order.line_ids or order.sale_id)
            checks = actions.search(scope)
            self.assertEqual(checks.kind, 'payment_verify')
            captured = dict(snapshot, transactions=[dict(pending, status='SUCCESS')])
            client = MagicMock()
            client.query.return_value = {'order': {'updatedAt': snapshot['updatedAt'],
                                                   'transactions': captured['transactions']}}
            with (patch.object(self.env.cr, 'commit'),
                  patch.object(type(self.store), '_shopify_client', return_value=client),
                  patch.object(store_order, 'fetch_order', return_value=captured)):
                checks._process_one()
            self.assertEqual(checks.state, 'obsolete')
            self.assertEqual(tx.status, 'SUCCESS')
            self.assertEqual(actions.search(scope), checks)
            self.assertFalse(tx.payment_id or order.sale_id)

        exception = self.env['qing.channel.exception'].search([
            ('store_id', '=', self.store.id), ('object_model', '=', order._name), ('object_id', '=', order.id),
            ('code', '=', stock_review.code), ('resolved', '=', False),
        ])
        manager = new_test_user(
            self.env, login='shopify-stock-review-recheck-manager', groups='qing_channel.group_channel_manager',
            company_id=self.store.company_id.id,
        )
        self.assertTrue(exception.with_user(manager).action_recheck())
        delivery = self.env['qing.channel.delivery'].search([
            ('store_id', '=', self.store.id), ('identity', '=', f'manual:{exception.id}'),
        ])
        version = order.snapshot_version
        with (patch.object(self.env.cr, 'commit'), patch.object(type(self.store), '_shopify_client'),
              patch.object(shopify_store, 'fetch_order', return_value=captured)):
            delivery._process_one()
            self.assertEqual(delivery.state, 'done')
            self._sale_action(order)._process_one()
            actions.search([*scope, ('kind', '=', 'payment')])._process_one()

        self.assertEqual(order.snapshot_version, version)
        self.assertFalse(order.stock_unverified)
        self.assertTrue(order.line_ids)
        self.assertEqual(tx.payment_id.amount, 24)
        self.assertEqual(order.sale_id.invoice_ids.amount_residual, 0)

    def test_historical_refund_facts_are_preserved_during_stock_review(self):
        original = dict(self._snapshot(), transactions=[self._shopify_order_transaction(1, 'SALE')])
        self.store.cutover_at = orders.parse_date(original['createdAt']) + timedelta(minutes=1)
        order = self.store._apply_shopify_order(original)
        parent = self.env['qing.channel.transaction'].search([('order_id', '=', order.id)])
        snapshot = self._refund_snapshot(original, parent, [(12, 'FAILURE')])
        stock_review = ChannelReviewRequired('shopify_stock_line_quantities_inconsistent')
        with patch.object(type(self.store), '_update_stock_claim', side_effect=stock_review):
            self.store._apply_shopify_order(snapshot)
        refund = self.env['qing.channel.refund'].search([('order_id', '=', order.id)])
        self.assertEqual(refund.external_id, snapshot['refunds'][0]['id'])
        self.assertEqual(refund.transaction_ids.status, 'FAILURE')
        self.assertFalse(refund.transaction_ids.payment_id or refund.credit_note_id)
        self.assertTrue(order.stock_unverified)
        self.assertTrue(order.historical)
        self.assertFalse(order.sale_id or parent.payment_id)
        self.assertFalse(self.env['qing.channel.action'].search_count([('order_id', '=', order.id)]))

    def test_failed_capture_refresh_updates_the_transaction_without_posting_payment(self):
        self._configure_shopify_accounting('capture', 'order')
        pending = self._shopify_order_transaction(1, 'SALE', status='AWAITING_RESPONSE')
        snapshot = dict(self._snapshot(), transactions=[pending])
        order = self.store._apply_shopify_order(snapshot)
        self.store._execute_channel_action(self._sale_action(order))
        scope = [('order_id', '=', order.id)]
        actions = self.env['qing.channel.action']
        check = actions.search([*scope, ('kind', '=', 'payment_verify')])
        self.assertEqual(len(check), 1)
        failed = dict(pending, status='FAILURE')
        client = MagicMock()
        client.query.return_value = {'order': {'updatedAt': snapshot['updatedAt'], 'transactions': [failed]}}
        with (patch.object(type(self.store), '_shopify_client', return_value=client),
              patch.object(store_order, 'fetch_order', return_value=dict(snapshot, transactions=[failed]))):
            self.assertEqual(self.store._execute_channel_action(check), 'obsolete')
        self.store._execute_channel_action(self._sale_action(order))
        tx = self.env['qing.channel.transaction'].search(scope)
        self.assertEqual(tx.status, 'FAILURE')
        self.assertFalse(tx.payment_id)
        self.assertFalse(actions.search([*scope, ('kind', '=', 'payment')]))
        self.assertEqual(order.sale_id.state, 'draft')

    def test_successful_capture_posts_once_and_reconciles_the_invoice(self):
        order, _snapshot, tx, payment_action = self._capture_fixture()
        self.assertEqual(tx.payment_id.amount, 24)
        self.assertEqual(order.sale_id.invoice_ids.state, 'posted')
        self.assertEqual(order.sale_id.invoice_ids.amount_residual, 0)
        payment = tx.payment_id
        self.store._execute_channel_action(payment_action)
        self.assertEqual(tx.payment_id, payment)
        self.assertEqual(len(order.sale_id.invoice_ids), 1)

    def _refund_snapshot(self, order_snapshot, parent, portions, quantity=1, restock='NO_RESTOCK'):
        snapshot = deepcopy(order_snapshot)
        stamp = fields.Datetime.now().replace(tzinfo=timezone.utc).isoformat()
        snapshot['updatedAt'] = max(stamp, '2026-11-18T12:02:00Z')
        original = snapshot['lineItems']['nodes'][0]
        original['currentQuantity'] = 2 - quantity
        total = 12 * quantity
        snapshot['currentTotalPriceSet'] = self._money(str(24 - total))
        original['priceAfterAllDiscountsBeforeTaxesSet'] = snapshot['currentTotalPriceSet']
        refund = {
            'id': 'gid://shopify/Refund/1', 'processedAt': stamp, 'totalRefundedSet': self._money(str(total)),
            'refundLineItems': {'nodes': [
                {'id': 'gid://shopify/RefundLineItem/1', 'lineItem': {'id': original['id']},
                 'quantity': quantity, 'restockType': restock, 'subtotalSet': self._money(str(total)),
                 'totalTaxSet': self._money('0')}
            ]},
            'refundShippingLines': {'nodes': []}, 'orderAdjustments': {'nodes': []},
            'transactions': {'nodes': [
                {'id': f'gid://shopify/OrderTransaction/{position + 2}', 'kind': 'REFUND', 'status': status,
                 'gateway': parent.gateway, 'parentTransaction': {'id': parent.external_id},
                 'settlementCurrency': self.CurrencyCode, 'processedAt': stamp, 'amountSet': self._money(str(amount))}
                for position, (amount, status) in enumerate(portions)
            ]}
        }
        snapshot['refunds'] = [refund]
        return snapshot

    def _apply_refund_snapshot(self, snapshot, order):
        self.store._apply_shopify_order(snapshot)
        common = [('order_id', '=', order.id), ('snapshot_version', '=', order.snapshot_version)]
        actions = self.env['qing.channel.action']
        for action in actions.search([*common, ('kind', '=', 'refund_payment')]):
            self.store._execute_channel_action(action)
        return actions.search([*common, ('kind', '=', 'refund')], limit=1)

    def _rounded_shopify_refund_snapshot(self, original, parent, quantity, tax):
        amount = round(12 * quantity + tax, 2)
        snapshot = self._refund_snapshot(original, parent, [(amount, 'SUCCESS')], quantity=quantity)
        refund = snapshot['refunds'][0]
        refund['totalRefundedSet'] = self._money(str(amount))
        line = refund['refundLineItems']['nodes'][0]
        line['totalTaxSet'] = self._money(str(tax))
        if original['taxesIncluded']:
            line['subtotalSet'] = self._money(str(amount))
        snapshot['currentTotalTaxSet'] = self._money(str(round(3.60 - tax, 2)))
        snapshot['currentTotalPriceSet'] = self._money(str(round(27.60 - amount, 2)))
        return snapshot

    def _assert_shopify_credit_taxes(self, credit, base, amounts):
        self.assertEqual(credit.state, 'posted')
        self.assertEqual(credit.amount_untaxed, base)
        self.assertEqual(credit.amount_residual, 0)
        tax_lines = credit.line_ids.filtered('tax_line_id').sorted('tax_line_id')
        for tax_line, amount in zip(tax_lines, amounts, strict=True):
            self.assertEqual(tax_line.account_id.name, tax_line.tax_line_id.name)
            self.assertEqual(abs(tax_line.amount_currency), amount)
            self.assertEqual(abs(tax_line.tax_base_amount), base)

    def test_full_refund_preserves_each_original_tax_when_rounded_amounts_offset(self):
        order, original, parent, _action = self._capture_fixture(rounded_taxes=True)
        snapshot = self._rounded_shopify_refund_snapshot(original, parent, 2, 3.60)
        action = self._apply_refund_snapshot(snapshot, order)
        self.store._execute_channel_action(action)
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        self.assertEqual(refund.credit_note_id.reversed_entry_id, order.sale_id.invoice_ids)
        self._assert_shopify_credit_taxes(refund.credit_note_id, 24, (2.41, 1.19))

    def test_refund_spanning_rounded_invoices_preserves_each_source_share(self):
        order, original, parent, _action = self._capture_fixture(
            invoice_policy='shipment', product_invoice_policy='delivery', rounded_taxes=True)
        first = self._ship_one_unit(order)
        first_invoice = self.store._invoice_shopify_sale(order)
        second = order.sale_id.picking_ids - first
        second.action_assign()
        second.move_ids.picked = True
        second.with_context(skip_backorder=True).button_validate()
        second_invoice = self.store._invoice_shopify_sale(order)
        self.store._reconcile_shopify_receivable(order)
        snapshot = self._rounded_shopify_refund_snapshot(original, parent, 2, 3.60)
        action = self._apply_refund_snapshot(snapshot, order)
        self.store._execute_channel_action(action)
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        note = refund.credit_note_id
        self.assertEqual(note.shopify_refund_source_invoice_ids, first_invoice | second_invoice)
        self.assertEqual({line.shopify_refund_source_line_id.id: line.price_total for line in note.invoice_line_ids},
                         {first_invoice.invoice_line_ids.id: 13.81, second_invoice.invoice_line_ids.id: 13.79})
        self._assert_shopify_credit_taxes(note, 24, (2.41, 1.19))

    def test_rounded_refund_splits_between_billed_and_unbilled_units(self):
        order, original, parent, _action = self._capture_fixture(
            invoice_policy='shipment', product_invoice_policy='delivery', rounded_taxes=True)
        self._ship_one_unit(order)
        invoice = self.store._invoice_shopify_sale(order)
        self.store._reconcile_shopify_receivable(order)
        snapshot = self._rounded_shopify_refund_snapshot(original, parent, 2, 3.60)
        action = self._apply_refund_snapshot(snapshot, order)
        self.store._execute_channel_action(action)
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        self.assertEqual(refund.credit_note_id.reversed_entry_id, invoice)
        self.assertEqual(order.sale_id.order_line.shopify_unbilled_refund_qty, 1)
        self.assertEqual(refund.transaction_ids.payment_id.amount, 27.60)
        self._assert_shopify_credit_taxes(refund.credit_note_id, 12, (1.21, 0.60))

    def test_tax_included_partial_refunds_preserve_tax_base_and_cumulative_tax(self):
        order, original, parent, _action = self._capture_fixture(rounded_taxes=True, taxes_included=True)
        incompatible = self._rounded_shopify_refund_snapshot(original, parent, 1, 1.80)
        with (
            self.assertRaisesRegex(ChannelReviewRequired, 'shopify_refund_tax_allocation_requires_review'),
            self.env.cr.savepoint(),
        ):
            action = self._apply_refund_snapshot(incompatible, order)
            self.store._execute_channel_action(action)
        self.assertFalse(order.sale_id.invoice_ids.reversal_move_ids)

        snapshot = self._rounded_shopify_refund_snapshot(original, parent, 1, 1.81)
        action = self._apply_refund_snapshot(snapshot, order)
        self.store._execute_channel_action(action)
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        self._assert_shopify_credit_taxes(refund.credit_note_id, 12, (1.21, 0.60))

        remaining = self._rounded_shopify_refund_snapshot(original, parent, 1, 1.79)
        extra = remaining['refunds'][0]
        extra['id'] = 'gid://shopify/Refund/2'
        extra['refundLineItems']['nodes'][0]['id'] = 'gid://shopify/RefundLineItem/2'
        extra['transactions']['nodes'][0]['id'] = 'gid://shopify/OrderTransaction/3'
        remaining['refunds'].insert(0, snapshot['refunds'][0])
        remaining['updatedAt'] = fields.Datetime.to_string(fields.Datetime.now() + timedelta(minutes=3)).replace(' ', 'T') + 'Z'
        remaining['lineItems']['nodes'][0]['currentQuantity'] = 0
        remaining['lineItems']['nodes'][0]['priceAfterAllDiscountsBeforeTaxesSet'] = self._money('0')
        remaining['currentTotalPriceSet'] = remaining['currentTotalTaxSet'] = self._money('0')
        self._apply_refund_snapshot(remaining, order)
        last = self.env['qing.channel.refund'].search([('order_id', '=', order.id), ('external_id', '=', extra['id'])])
        action = self.env['qing.channel.action'].search([
            ('order_id', '=', order.id), ('snapshot_version', '=', order.snapshot_version),
            ('kind', '=', 'refund'),
        ]).filtered(lambda job: job.data['refund'] == last.id)
        self.store._execute_channel_action(action)
        self._assert_shopify_credit_taxes(last.credit_note_id, 12, (1.20, 0.59))

    def _already_refunded_before_first_import(self):
        self._configure_shopify_accounting('capture', 'order')
        original = self._snapshot()
        parent = MagicMock(gateway='pilot-gateway', external_id='gid://shopify/OrderTransaction/1')
        original.update(originalTotalPriceSet=self._money('24'), totalTaxSet=self._money('0'))
        original['lineItems']['nodes'][0].update(
            originalTotalSet=self._money('30'), discountAllocations=[{'allocatedAmountSet': self._money('6')}],
        )
        original['transactions'] = [{
            'id': parent.external_id, 'kind': 'CAPTURE', 'status': 'SUCCESS', 'gateway': parent.gateway,
            'amountSet': self._money('24'), 'processedAt': original['createdAt'], 'settlementCurrency': self.CurrencyCode,
        }]
        snapshot = self._refund_snapshot(original, parent, [(12, 'SUCCESS')], restock='CANCEL')
        item = snapshot['lineItems']['nodes'][0]
        item['unfulfilledQuantity'] = 1
        snapshot['fulfillmentOrders']['nodes'][0]['lineItems'] = {
            'nodes': [{'remainingQuantity': 1, 'lineItem': {'id': item['id']}}],
        }
        return snapshot

    def test_first_import_after_refund_invoices_and_reserves_the_remaining_quantity_once(self):
        snapshot = self._already_refunded_before_first_import()
        group = self.env['account.tax.group'].create({'name': 'Shopify First Import Tax'})
        tax = self.env['account.tax'].create({
            'name': 'Shopify First Import Tax', 'amount': 10, 'amount_type': 'percent',
            'tax_group_id': group.id, 'price_include_override': 'tax_excluded',
        })
        self.env['qing.channel.mapping'].create({
            'store_id': self.store.id, 'kind': 'tax', 'tax_id': tax.id,
            'external_key': 'US|CA|shopify|First Import Tax|excluded|merchant',
        })
        snapshot['lineItems']['nodes'][0]['taxLines'] = [{
            'title': 'First Import Tax', 'source': 'shopify', 'rate': 0.1,
            'channelLiable': False, 'priceSet': self._money('2.40'),
        }]
        snapshot['originalTotalPriceSet'] = snapshot['transactions'][0]['amountSet'] = self._money('26.40')
        snapshot['totalTaxSet'] = self._money('2.40')
        snapshot['currentTotalPriceSet'] = self._money('13.20')
        snapshot['currentTotalTaxSet'] = self._money('1.20')
        refund_payload = snapshot['refunds'][0]
        refund_payload['totalRefundedSet'] = refund_payload['transactions']['nodes'][0]['amountSet'] = self._money('13.20')
        refund_payload['refundLineItems']['nodes'][0]['totalTaxSet'] = self._money('1.20')
        order = self.store._apply_shopify_order(snapshot)
        actions = self.env['qing.channel.action'].search([('order_id', '=', order.id)], order='id')
        with patch.object(self.env.cr, 'commit'):
            for job in actions:
                job._process_one()
        sale = order.sale_id
        self.assertEqual(sale.order_line.product_uom_qty, 2)
        self.assertEqual(sale.order_line.shopify_unbilled_refund_qty, 1)
        self.assertEqual(sale.picking_ids.move_ids.product_uom_qty, 1)
        invoice = sale.invoice_ids
        self.assertEqual(invoice.amount_total, 13.2)
        self.assertEqual(invoice.amount_tax, 1.2)
        self.assertEqual(invoice.amount_residual, 0)
        self.assertEqual(invoice.state, 'posted')
        refund = self.env['qing.channel.refund'].search([('order_id', '=', order.id)])
        self.assertTrue(refund.accounted)
        self.assertFalse(refund.credit_note_id)
        self.assertEqual(refund.transaction_ids.payment_id.amount, 13.2)
        self.assertTrue(all(action.state == 'done' for action in actions))

        self.store._apply_shopify_order(deepcopy(snapshot))
        with patch.object(self.env.cr, 'commit'):
            for job in actions:
                job._process_one()
        self.assertEqual(sale.invoice_ids, invoice)
        self.assertEqual(sale.order_line.shopify_unbilled_refund_qty, 1)

    def test_first_import_refund_with_unexplained_amount_leaves_finance_in_review(self):
        snapshot = self._already_refunded_before_first_import()
        snapshot['currentTotalPriceSet'] = self._money('13')
        order = self.store._apply_shopify_order(snapshot)
        actions = self.env['qing.channel.action'].search([('order_id', '=', order.id)], order='id')
        with patch.object(self.env.cr, 'commit'):
            for job in actions:
                job._process_one()
        self.assertEqual(self._sale_action(order).state, 'review')
        self.assertFalse(order.sale_id)
        self.assertFalse(self.env['qing.channel.transaction'].search([('order_id', '=', order.id)]).payment_id)
        problems = self.env['qing.channel.exception'].search([('store_id', '=', self.store.id)])
        self.assertIn('shopify_first_import_refund_details_mismatch', problems.mapped('code'))

    def test_unpaid_cancellation_zero_refund_finishes_without_financial_entries(self):
        original = self._snapshot()
        parent = MagicMock()
        cancelled = self._refund_snapshot(original, parent, [], quantity=2)
        cancelled['cancelledAt'] = cancelled['updatedAt']
        cancelled['refunds'][0]['totalRefundedSet'] = self._money('0')
        Action = self.env['qing.channel.action']
        Refund = self.env['qing.channel.refund']
        for create_before_cancellation in (False, True):
            with self.subTest(imported_before_cancel=create_before_cancellation):
                sequence = 100 if create_before_cancellation else 101
                original['id'] = f'gid://shopify/Order/{sequence}'
                cancelled['id'] = original['id']
                cancelled['refunds'][0]['id'] = f'gid://shopify/Refund/{sequence}'
                if create_before_cancellation:
                    order = self.store._apply_shopify_order(original)
                    self.store._execute_channel_action(self._sale_action(order))
                order = self.store._apply_shopify_order(cancelled)
                actions = Action.search([('order_id', '=', order.id),
                                         ('snapshot_version', '=', order.snapshot_version)], order='id')
                with patch.object(self.env.cr, 'commit'):
                    for action in actions:
                        action._process_one()
                refund = Refund.search([('order_id', '=', order.id)])
                self.assertTrue(refund.accounted)
                self.assertFalse(refund.transaction_ids.payment_id)
                self.assertFalse(refund.credit_note_id)
                self.assertTrue(all(action.state == 'done' for action in actions))
                if create_before_cancellation:
                    self.assertEqual(order.sale_id.state, 'cancel')
                else:
                    self.assertFalse(order.sale_id)

    def test_first_import_of_funded_cancelled_order_keeps_finance_in_review(self):
        snapshot = self._already_refunded_before_first_import()
        snapshot['cancelledAt'] = snapshot['updatedAt']
        order = self.store._apply_shopify_order(snapshot)
        self.assertFalse(order.sale_id)
        self.assertTrue(self.env['qing.channel.transaction'].search_count([('order_id', '=', order.id)]))
        with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_first_import_funded_cancellation_requires_review'):
            self.store._execute_channel_action(self._sale_action(order))

    def test_first_import_of_an_externally_shipped_order_holds_stock_for_review(self):
        snapshot = self._snapshot()
        item = snapshot['lineItems']['nodes'][0]
        snapshot['fulfillments'] = [{
            'status': 'SUCCESS', 'createdAt': snapshot['createdAt'],
            'fulfillmentLineItems': {'nodes': [{'quantity': 1, 'lineItem': {'id': item['id']}}]},
        }]
        order = self.store._apply_shopify_order(snapshot)
        with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_first_import_shipment_requires_review'):
            self.store._execute_channel_action(self._sale_action(order))
        self.assertTrue(order.stock_unverified)
        self.assertFalse(order.sale_id)

    def test_legacy_sale_ignores_barcode_and_pagination_when_importing_a_refund(self):
        order, original, parent, _payment_action = self._capture_fixture()
        sale = order.sale_id
        invoices = sale.invoice_ids
        legacy = deepcopy(sale.shopify_commercial_data)
        legacy['lineItems']['pageInfo'] = {'hasNextPage': False, 'endCursor': 'legacy'}
        legacy_line = legacy['lineItems']['nodes'][0]
        legacy_line['variant']['barcode'] = 'LEGACY-BARCODE'
        legacy_line['unfulfilledQuantity'] = 2
        sale.shopify_commercial_data = legacy

        snapshot = self._refund_snapshot(original, parent, [(12, 'SUCCESS')])
        snapshot['lineItems']['nodes'][0]['unfulfilledQuantity'] = 1
        action = self._apply_refund_snapshot(snapshot, order)
        self.store._execute_channel_action(self._sale_action(order))
        self.store._execute_channel_action(action)
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        note = refund.credit_note_id
        self.assertEqual(note.reversed_entry_id, invoices)
        self.assertEqual(note.amount_total, 12)

        updated = snapshot['refunds'][0]['refundLineItems']
        updated['pageInfo'] = {'hasNextPage': False, 'endCursor': 'after-upgrade'}
        action = self._apply_refund_snapshot(snapshot, order)
        self.store._execute_channel_action(action)
        self.assertEqual(refund.credit_note_id, note)
        updated['nodes'][0]['quantity'] = 2
        action = self._apply_refund_snapshot(snapshot, order)
        with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_accounted_refund_changed'):
            self.store._execute_channel_action(action)

    def test_partial_gateway_refund_registers_funds_then_creates_one_credit(self):
        order, original, parent, _payment_action = self._capture_fixture()
        snapshot = self._refund_snapshot(original, parent, [(6, 'SUCCESS'), (6, 'PENDING')])
        action = self._apply_refund_snapshot(snapshot, order)
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        self.assertEqual(refund.transaction_ids.payment_id.amount, 6)
        self.assertFalse(refund.credit_note_id)
        with self.assertRaisesRegex(DeferredChannelError, 'shopify_refund_pending'):
            self.store._execute_channel_action(action)

        snapshot['refunds'][0]['transactions']['nodes'][1]['status'] = 'SUCCESS'
        action = self._apply_refund_snapshot(snapshot, order)
        self.store._execute_channel_action(action)
        self.assertTrue(refund.accounted)
        note = refund.credit_note_id
        self.assertEqual(note.reversed_entry_id, order.sale_id.invoice_ids)
        self.assertEqual(note.state, 'posted')
        self.assertEqual(note.amount_total, 12)
        self.assertEqual(note.amount_residual, 0)
        self.assertEqual(refund.transaction_ids.payment_id.mapped('amount'), [6, 6])
        repeated = self._apply_refund_snapshot(snapshot, order)
        self.store._execute_channel_action(repeated)
        self.assertEqual(refund.credit_note_id, note)

    def test_refund_status_refreshes_while_sale_and_stock_lines_are_blocked(self):
        original = dict(self._snapshot(), transactions=[self._shopify_order_transaction(1, 'SALE')])
        order = self.store._apply_shopify_order(original)
        parent = self.env['qing.channel.transaction'].search([('order_id', '=', order.id)])
        pending = self._refund_snapshot(original, parent, [(12, 'PENDING')])
        stock_review = ChannelReviewRequired('shopify_stock_line_quantities_inconsistent')
        with patch.object(type(self.store), '_update_stock_claim', side_effect=stock_review):
            self.store._apply_shopify_order(pending)
            actions = self.env['qing.channel.action']
            scope = [('order_id', '=', order.id), ('snapshot_version', '=', order.snapshot_version)]
            check = actions.search([*scope, ('kind', '=', 'refund_verify')])
            self.assertEqual(len(check), 1)
            self.assertFalse(check.dependency_ids)
            refund = self.env['qing.channel.refund'].search([('order_id', '=', order.id)])
            self.assertEqual(refund.transaction_ids.status, 'PENDING')
            settled = deepcopy(pending)
            settled['refunds'][0]['transactions']['nodes'][0]['status'] = 'SUCCESS'
            with (patch.object(self.env.cr, 'commit'),
                  patch.object(store_refund, 'fetch_refund', return_value=settled['refunds'][0]),
                  patch.object(store_refund, 'fetch_order', return_value=settled)):
                check._process_one()
                with patch.object(fields.Datetime, 'now', return_value=check.retry_at):
                    check._process_one()
            self.assertEqual(check.state, 'obsolete')
            self.assertEqual(refund.transaction_ids.status, 'SUCCESS')
            self.assertFalse(refund.credit_note_id or refund.transaction_ids.payment_id or order.sale_id)
            self.assertFalse(actions.search([('order_id', '=', order.id), ('snapshot_version', '=', order.snapshot_version),
                                             ('kind', 'in', ('sale', 'refund', 'refund_payment'))]))

    def test_refund_deadline_reaches_review_even_when_sale_cannot_be_created(self):
        original = dict(self._snapshot(), transactions=[self._shopify_order_transaction(1, 'SALE')])
        order = self.store._apply_shopify_order(original)
        parent = self.env['qing.channel.transaction'].search([('order_id', '=', order.id)])
        snapshot = self._refund_snapshot(original, parent, [(12, 'PENDING')])
        line = snapshot['lineItems']['nodes'][0]
        line.update(sku='', variant={'id': 'gid://shopify/ProductVariant/2'})
        self.store._apply_shopify_order(snapshot)
        sale = self._sale_action(order)
        refund = self.env['qing.channel.refund'].search([('order_id', '=', order.id)])
        scope = [('order_id', '=', order.id), ('snapshot_version', '=', order.snapshot_version)]
        check = self.env['qing.channel.action'].search([*scope, ('kind', '=', 'refund_verify')])
        with patch.object(self.env.cr, 'commit'):
            sale._process_one()
            self.assertEqual(sale.state, 'review')
            self.assertFalse(order.sale_id)
            self.assertEqual(len(check), 1)
            with (patch.object(fields.Datetime, 'now', return_value=refund.processed_at + timedelta(hours=24)),
                  patch.object(store_refund, 'fetch_refund', return_value=snapshot['refunds'][0])):
                check._process_one()
        self.assertEqual(check.state, 'review')
        self.assertTrue(self.env['qing.channel.exception'].search_count([
            ('store_id', '=', self.store.id), ('object_model', '=', check._name),
            ('object_id', '=', check.id), ('code', '=', 'shopify_refund_incomplete'), ('resolved', '=', False),
        ]))
        self.assertFalse(refund.credit_note_id or refund.transaction_ids.payment_id)

    def test_pending_refund_waits_for_the_full_deadline_without_using_retry_attempts(self):
        order, original, parent, _payment_action = self._capture_fixture()
        snapshot = self._refund_snapshot(original, parent, [(6, 'SUCCESS'), (6, 'PENDING')])
        posting = self._apply_refund_snapshot(snapshot, order)
        action = posting.dependency_ids.filtered(lambda item: item.kind == 'refund_verify')
        self.assertEqual(len(action), 1)
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        start = refund.processed_at
        deadline = start + timedelta(hours=24)
        with (patch.object(self.env.cr, 'commit'), patch.object(fields.Datetime, 'now') as clock,
              patch.object(store_refund, 'fetch_refund', return_value=snapshot['refunds'][0]) as refresh):
            for check in range(10):
                clock.return_value = start + timedelta(minutes=5 * check)
                action._process_one()
                self.assertEqual(action.state, 'pending')
                self.assertEqual(action.tries, 0)
                self.assertEqual(action.retry_at, clock.return_value + timedelta(minutes=5))
            clock.return_value = deadline - timedelta(seconds=1)
            action._process_one()
            self.assertEqual(action.state, 'pending')
            self.assertEqual(action.retry_at, deadline)
            clock.return_value = deadline
            action._process_one()
        self.assertEqual(refresh.call_count, 11)
        self.assertEqual(action.state, 'review')
        self.assertTrue(self.env['qing.channel.exception'].search_count([
            ('store_id', '=', self.store.id), ('object_model', '=', action._name),
            ('object_id', '=', action.id), ('code', '=', 'shopify_refund_incomplete'),
        ]))
        self.assertEqual(refund.transaction_ids.payment_id.amount, 6)
        self.assertFalse(refund.credit_note_id)

    def test_unknown_refund_transaction_blocks_credit_when_success_total_already_matches(self):
        order, original, parent, _payment_action = self._capture_fixture()
        snapshot = self._refund_snapshot(original, parent, [(12, 'SUCCESS'), (1, 'UNKNOWN')])
        action = self._apply_refund_snapshot(snapshot, order)
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        self.assertEqual(refund.transaction_ids.payment_id.amount, 12)
        with self.assertRaisesRegex(DeferredChannelError, 'shopify_refund_pending'):
            self.store._execute_channel_action(action)
        self.assertFalse(refund.credit_note_id)
        self.assertFalse(refund.accounted)

        snapshot['refunds'][0]['transactions']['nodes'][1]['status'] = 'FAILURE'
        action = self._apply_refund_snapshot(snapshot, order)
        self.store._execute_channel_action(action)
        self.assertTrue(refund.accounted)
        self.assertEqual(refund.credit_note_id.amount_total, 12)
        self.assertEqual(refund.transaction_ids.payment_id.amount, 12)

    def test_zero_reported_refund_with_pending_transaction_uses_configured_deadline(self):
        order, original, parent, _payment_action = self._capture_fixture()
        snapshot = self._refund_snapshot(original, parent, [(12, 'AWAITING_RESPONSE')])
        snapshot['refunds'][0]['totalRefundedSet'] = self._money('0')
        posting = self._apply_refund_snapshot(snapshot, order)
        action = posting.dependency_ids.filtered(lambda item: item.kind == 'refund_verify')
        self.assertEqual(len(action), 1)
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        self.env['ir.config_parameter'].set_int('qing_shopify.refund_pending_hours', 2)
        deadline = refund.processed_at + timedelta(hours=2)
        with (patch.object(self.env.cr, 'commit'), patch.object(fields.Datetime, 'now') as clock,
              patch.object(store_refund, 'fetch_refund', return_value=snapshot['refunds'][0])):
            clock.return_value = deadline - timedelta(seconds=1)
            action._process_one()
            self.assertEqual(action.state, 'pending')
            self.assertEqual(action.retry_at, deadline)
            clock.return_value = deadline
            action._process_one()
        self.assertEqual(action.state, 'review')
        self.assertTrue(self.env['qing.channel.exception'].search_count([
            ('store_id', '=', self.store.id), ('object_model', '=', action._name),
            ('object_id', '=', action.id), ('code', '=', 'shopify_refund_incomplete'),
        ]))
        self.assertFalse(refund.transaction_ids.payment_id)
        self.assertFalse(refund.credit_note_id)

    def test_refund_retry_rechecks_shopify_without_an_order_timestamp_change(self):
        order, original, parent, _payment_action = self._capture_fixture()
        snapshot = self._refund_snapshot(original, parent, [(6, 'SUCCESS'), (6, 'PENDING')])
        posting = self._apply_refund_snapshot(snapshot, order)
        action = posting.dependency_ids.filtered(lambda item: item.kind == 'refund_verify')
        self.assertEqual(len(action), 1)
        version = order.snapshot_version
        external_stamp = order.external_updated_at
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        settled = deepcopy(snapshot)
        settled['refunds'][0]['transactions']['nodes'][1]['status'] = 'SUCCESS'
        client = MagicMock()
        client.query.return_value = {'node': deepcopy(settled['refunds'][0])}
        with (patch.object(self.env.cr, 'commit'),
              patch.object(type(self.store), '_shopify_client', return_value=client),
              patch.object(store_refund, 'fetch_order', return_value=settled) as fetch):
            action._process_one()
            self.assertEqual(action.state, 'pending')
            self.assertFalse(refund.credit_note_id)
            client.query.assert_not_called()
            with patch.object(fields.Datetime, 'now', return_value=action.retry_at):
                action._process_one()
            client.query.assert_called_once_with(orders.REFUND_QUERY, {'id': refund.external_id})
            fetch.assert_called_once_with(client, order.external_id)
            self.assertEqual(order.external_updated_at, external_stamp)
            self.assertEqual(order.snapshot_version, version + 1)
            self.assertEqual(action.state, 'obsolete')
            posting._process_one()
            self.assertEqual(posting.state, 'obsolete')

            jobs = self.env['qing.channel.action'].search([
                ('order_id', '=', order.id), ('snapshot_version', '=', order.snapshot_version),
                ('kind', '=', 'refund_payment'),
            ])
            for job in jobs:
                job._process_one()
            current = self.env['qing.channel.action'].search([
                ('order_id', '=', order.id), ('snapshot_version', '=', order.snapshot_version),
                ('kind', '=', 'refund'),
            ])
            current._process_one()
            self.assertEqual(current.state, 'waiting')
            current.dependency_ids._process_one()
            current._process_one()
            current._process_one()
        self.assertEqual(current.state, 'done')
        self.assertEqual(refund.transaction_ids.payment_id.mapped('amount'), [6, 6])
        self.assertEqual(refund.credit_note_id.amount_total, 12)
        self.assertEqual(refund.credit_note_id.amount_residual, 0)

    def test_refund_status_read_failure_is_retried_until_deadline(self):
        order, original, parent, _payment_action = self._capture_fixture()
        snapshot = self._refund_snapshot(original, parent, [(12, 'PENDING')])
        posting = self._apply_refund_snapshot(snapshot, order)
        action = posting.dependency_ids.filtered(lambda item: item.kind == 'refund_verify')
        self.assertEqual(len(action), 1)
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        unavailable = DeferredChannelError('shopify_transport_unknown')
        throttled = DeferredChannelError('shopify_throttled', retry_after=600)
        deadline = refund.processed_at + timedelta(hours=24)
        with (patch.object(self.env.cr, 'commit'), patch.object(fields.Datetime, 'now') as clock,
              patch.object(store_refund, 'fetch_refund', side_effect=[unavailable, throttled, throttled]) as refresh):
            clock.return_value = refund.processed_at
            action._process_one()
            clock.return_value = action.retry_at
            action._process_one()
            self.assertEqual(action.state, 'pending')
            self.assertEqual(action.tries, 0)
            self.assertEqual(action.retry_at, clock.return_value + timedelta(minutes=5))
            clock.return_value = deadline - timedelta(seconds=30)
            action._process_one()
            self.assertEqual(action.retry_at, deadline)
            clock.return_value = deadline
            action._process_one()
        self.assertEqual(refresh.call_count, 3)
        self.assertEqual(action.state, 'review')
        self.assertTrue(self.env['qing.channel.exception'].search_count([
            ('store_id', '=', self.store.id), ('object_model', '=', action._name),
            ('object_id', '=', action.id), ('code', '=', 'shopify_refund_status_unavailable'),
        ]))
        self.assertFalse(refund.credit_note_id)
        self.assertFalse(refund.transaction_ids.payment_id)

    def test_refund_before_invoice_withholds_quantity_and_matches_payment(self):
        order, original, parent, _payment_action = self._capture_fixture(invoice_policy='shipment')
        line = order.sale_id.order_line
        self.assertEqual(line.qty_to_invoice, 2)
        action = self._apply_refund_snapshot(self._refund_snapshot(original, parent, [(12, 'SUCCESS')]), order)
        self.store._execute_channel_action(action)
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        self.assertTrue(refund.accounted)
        self.assertFalse(refund.credit_note_id)
        self.assertEqual(line.shopify_unbilled_refund_qty, 1)
        self.assertEqual(line.qty_to_invoice, 1)
        self.store._invoice_shopify_sale(order)
        self.store._reconcile_shopify_receivable(order)
        self.assertEqual(order.sale_id.invoice_ids.amount_total, 12)
        self.assertEqual(order.sale_id.invoice_ids.amount_residual, 0)

    def _ship_one_unit(self, order):
        picking = order.sale_id.picking_ids
        self.assertEqual(len(picking), 1)
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 10)
        picking.action_assign()
        picking.move_ids.quantity = 1
        picking.move_ids.picked = True
        picking.with_context(skip_backorder=True).button_validate()
        self.assertEqual(picking.state, 'done')
        self.assertEqual(order.sale_id.order_line.qty_delivered, 1)
        return picking

    def test_refund_spanning_two_invoices_creates_one_credit_and_allows_cancellation(self):
        order, original, parent, _payment_action = self._capture_fixture(invoice_policy='shipment')
        first = order.sale_id._create_invoices()
        first.invoice_line_ids.quantity = 1
        first.action_post()
        self.store._invoice_shopify_sale(order)
        self.store._reconcile_shopify_receivable(order)
        invoices = order.sale_id.invoice_ids
        self.assertEqual(len(invoices), 2)
        self.assertEqual(invoices.mapped('amount_total'), [12, 12])
        self.assertEqual(invoices.mapped('amount_residual'), [0, 0])

        snapshot = self._refund_snapshot(original, parent, [(24, 'SUCCESS')], quantity=2, restock='CANCEL')
        snapshot['cancelledAt'] = snapshot['updatedAt']
        action = self._apply_refund_snapshot(snapshot, order)
        with self.assertRaisesRegex(DeferredChannelError, 'shopify_cancel_waiting_for_credit'):
            self.store._execute_channel_action(self._sale_action(order))
        self.store._execute_channel_action(action)
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        note = refund.credit_note_id
        self.assertEqual(note.state, 'posted')
        self.assertFalse(note.reversed_entry_id)
        self.assertEqual(note.shopify_refund_source_invoice_ids, invoices)
        self.assertEqual(note.invoice_line_ids.mapped('quantity'), [1, 1])
        self.assertEqual(note.invoice_line_ids.shopify_refund_source_line_id, invoices.invoice_line_ids)
        self.assertEqual(note.amount_total, 24)
        self.assertEqual(note.amount_residual, 0)
        self.store._execute_channel_action(self._sale_action(order))
        self.assertEqual(order.sale_id.state, 'cancel')
        self.assertEqual(order.sale_id.picking_ids.state, 'cancel')
        self.store._execute_channel_action(action)
        self.assertEqual(refund.credit_note_id, note)

    def test_refund_explains_snapshot_change_but_does_not_hide_an_order_edit(self):
        order, original, parent, _payment_action = self._capture_fixture()
        sale = order.sale_id
        snapshot = self._refund_snapshot(original, parent, [(12, 'SUCCESS')])
        self.store._apply_shopify_order(snapshot)
        self.store._execute_channel_action(self._sale_action(order))
        self.assertEqual(sale.amount_total, 24)
        self.assertEqual(sale.order_line.product_uom_qty, 2)
        edited = deepcopy(snapshot)
        edited['lineItems']['nodes'][0]['quantity'] = 3
        self.store._apply_shopify_order(edited)
        with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_edit_after_invoice_or_delivery'):
            self.store._execute_channel_action(self._sale_action(order))
        self.assertEqual(sale.order_line.product_uom_qty, 2)

    def test_existing_native_credit_must_be_matched_before_refunding_the_same_invoice(self):
        order, original, parent, _payment_action = self._capture_fixture()
        existing = order.sale_id.invoice_ids._reverse_moves()
        existing.action_post()
        action = self._apply_refund_snapshot(self._refund_snapshot(original, parent, [(12, 'SUCCESS')]), order)
        with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_refund_existing_manual_credit'):
            self.store._execute_channel_action(action)
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        self.assertFalse(refund.credit_note_id)
        self.assertEqual(refund.transaction_ids.payment_id.amount, 12)

    def test_one_refund_allocates_to_billed_and_unbilled_quantities(self):
        order, original, parent, _payment_action = self._capture_fixture(
            invoice_policy='shipment', product_invoice_policy='delivery')
        self._ship_one_unit(order)
        self.store._invoice_shopify_sale(order)
        self.store._reconcile_shopify_receivable(order)
        invoice = order.sale_id.invoice_ids
        self.assertEqual(invoice.amount_total, 12)

        snapshot = self._refund_snapshot(original, parent, [(24, 'SUCCESS')], quantity=2)
        action = self._apply_refund_snapshot(snapshot, order)
        self.store._execute_channel_action(action)
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        self.assertEqual(refund.credit_note_id.reversed_entry_id, invoice)
        self.assertEqual(refund.credit_note_id.amount_total, 12)
        self.assertEqual(refund.credit_note_id.amount_residual, 0)
        self.assertEqual(order.sale_id.order_line.shopify_unbilled_refund_qty, 1)
        self.assertEqual(refund.transaction_ids.payment_id.amount, 24)

    def _partial_shipment_refund(self, restock):
        order, original, parent, _action = self._capture_fixture(
            invoice_policy='shipment', product_invoice_policy='delivery')
        self._ship_one_unit(order)
        invoice = self.store._invoice_shopify_sale(order)
        self.store._reconcile_shopify_receivable(order)
        self.assertEqual(invoice.invoice_line_ids.quantity, 1)
        snapshot = self._refund_snapshot(original, parent, [(12, 'SUCCESS')], restock=restock)
        line = snapshot['lineItems']['nodes'][0]
        assignment = snapshot['fulfillmentOrders']['nodes'][0]
        assignment.update(status='CLOSED' if restock == 'CANCEL' else 'OPEN',
                          lineItems={'nodes': [{'remainingQuantity': 0 if restock == 'CANCEL' else 1,
                                                'lineItem': {'id': line['id']}}]})
        snapshot['fulfillments'] = [{
            'status': 'SUCCESS', 'createdAt': snapshot['updatedAt'],
            'fulfillmentLineItems': {'nodes': [{'quantity': 1, 'lineItem': {'id': line['id']}}]},
        }]
        action = self._apply_refund_snapshot(snapshot, order)
        return order, invoice, action

    def test_refunding_cancelled_unshipped_unit_preserves_the_shipped_invoice(self):
        order, invoice, action = self._partial_shipment_refund('CANCEL')
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        self.store._execute_channel_action(action)
        self.assertTrue(refund.accounted)
        self.assertFalse(refund.credit_note_id)
        self.assertEqual(invoice.amount_total, 12)
        self.assertEqual(invoice.amount_residual, 0)
        self.assertEqual(refund.transaction_ids.payment_id.amount, 12)
        self.assertEqual(order.sale_id.order_line.shopify_unbilled_refund_qty, 1)
        self.store._execute_channel_action(action)
        self.assertEqual(order.sale_id.order_line.shopify_unbilled_refund_qty, 1)
        with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_cancelled_outbound_quantity_requires_review'):
            self.store._execute_channel_action(self._sale_action(order))
        backorder = order.sale_id.picking_ids.filtered(lambda picking: picking.state not in ('done', 'cancel'))
        self.assertEqual(len(backorder), 1)
        backorder.action_assign()
        backorder.move_ids.quantity = 1
        backorder.move_ids.picked = True
        with self.assertRaisesRegex(UserError, 'Shopify canceled some items'):
            backorder.with_context(skip_backorder=True).button_validate()
        self.assertNotEqual(backorder.state, 'done')

        backorder.action_cancel()
        self.store._execute_channel_action(self._sale_action(order))
        self.assertEqual(invoice.amount_total, 12)

    def test_shopify_partial_cancellation_allows_the_remaining_quantity_to_ship(self):
        original = self._snapshot()
        original['currentTotalPriceSet'] = self._money('36')
        item = original['lineItems']['nodes'][0]
        item.update(quantity=3, currentQuantity=3, priceAfterAllDiscountsBeforeTaxesSet=self._money('36'))
        order = self.store._apply_shopify_order(original)
        self.store._execute_channel_action(self._sale_action(order))
        order.sale_id.action_confirm()

        snapshot = self._refund_snapshot(original, None, [], restock='CANCEL')
        snapshot['currentTotalPriceSet'] = self._money('24')
        line = snapshot['lineItems']['nodes'][0]
        line.update(currentQuantity=2, priceAfterAllDiscountsBeforeTaxesSet=self._money('24'))
        assignment = snapshot['fulfillmentOrders']['nodes'][0]
        assignment['lineItems'] = {'nodes': [{'remainingQuantity': 2, 'lineItem': {'id': line['id']}}]}
        self.store._apply_shopify_order(snapshot)

        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 10)
        picking = order.sale_id.picking_ids
        picking.action_assign()
        picking.move_ids.quantity = 2
        picking.move_ids.picked = True
        picking.with_context(skip_backorder=True).button_validate()
        self.assertEqual(picking.state, 'done')
        self.assertEqual(order.sale_id.order_line.qty_delivered, 2)
        self.assertFalse(order.sale_id.invoice_ids)

    def test_refund_without_fulfillment_evidence_leaves_mixed_quantities_in_review(self):
        order, _invoice, action = self._partial_shipment_refund('NO_RESTOCK')
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_refund_invoice_allocation_ambiguous'):
            self.store._execute_channel_action(action)
        self.assertFalse(refund.accounted)
        self.assertFalse(refund.credit_note_id)
        self.assertEqual(refund.transaction_ids.payment_id.amount, 12)
        self.assertFalse(order.sale_id.order_line.shopify_unbilled_refund_qty)

    def test_return_of_an_invoiced_shipment_leaves_the_other_unit_unbilled(self):
        order, invoice, action = self._partial_shipment_refund('RETURN')
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        self.store._execute_channel_action(action)
        self.assertTrue(refund.accounted)
        self.assertEqual(refund.credit_note_id.reversed_entry_id, invoice)
        self.assertEqual(refund.credit_note_id.amount_total, 12)
        self.assertFalse(order.sale_id.order_line.shopify_unbilled_refund_qty)

    def test_return_stays_draft_until_received_independently_of_payout(self):
        order, original, parent, _payment_action = self._capture_fixture()
        picking = self._ship_one_unit(order)
        snapshot = self._refund_snapshot(original, parent, [(12, 'PENDING')], restock='RETURN')
        action = self._apply_refund_snapshot(snapshot, order)
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        return_action = self.env['qing.channel.action'].search([
            ('order_id', '=', order.id), ('snapshot_version', '=', order.snapshot_version), ('kind', '=', 'return')])
        self.store._execute_channel_action(return_action)
        receipt = refund.return_picking_ids
        self.assertEqual(receipt.return_id, picking)
        self.assertEqual(receipt.state, 'draft')
        self.assertEqual(receipt.move_ids.product_uom_qty, 1)
        self.assertFalse(refund.credit_note_id)
        self.assertFalse(refund.transaction_ids.payment_id)
        self.store._execute_channel_action(return_action)
        self.assertEqual(refund.return_picking_ids, receipt)
        qty = self.product.with_context(warehouse_id=self.warehouse.id).qty_available
        receipt.action_confirm()
        receipt.move_ids.quantity = 1
        receipt.move_ids.picked = True
        receipt.with_context(skip_backorder=True).button_validate()
        self.assertEqual(receipt.state, 'done')
        self.assertEqual(self.product.with_context(warehouse_id=self.warehouse.id).qty_available, qty + 1)
        self.assertFalse(refund.transaction_ids.payment_id)

    def test_failed_refund_stays_unposted_and_requires_review(self):
        order, original, parent, _payment_action = self._capture_fixture()
        snapshot = self._refund_snapshot(original, parent, [(12, 'FAILURE')])
        action = self._apply_refund_snapshot(snapshot, order)
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_refund_failed'):
            self.store._execute_channel_action(action)
        self.assertFalse(refund.accounted)
        self.assertFalse(refund.credit_note_id)
        self.assertFalse(refund.transaction_ids.payment_id)

    def test_cancellation_after_shopify_shipped_needs_review_even_without_erp_delivery(self):
        order, original, parent, _payment_action = self._capture_fixture()
        snapshot = self._refund_snapshot(original, parent, [(12, 'SUCCESS')], restock='CANCEL')
        snapshot['cancelledAt'] = snapshot['updatedAt']
        line_id = snapshot['lineItems']['nodes'][0]['id']
        snapshot['fulfillments'] = [{
            'status': 'SUCCESS', 'createdAt': snapshot['updatedAt'],
            'fulfillmentLineItems': {'nodes': [{'quantity': 1, 'lineItem': {'id': line_id}}]},
        }]
        self._apply_refund_snapshot(snapshot, order)
        self.assertFalse(order.sale_id.picking_ids.filtered(lambda picking: picking.state == 'done'))
        with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_cancel_after_delivery'):
            self.store._execute_channel_action(self._sale_action(order))
        self.assertEqual(order.sale_id.state, 'sale')
        self.assertFalse(order.sale_id.picking_ids.filtered(lambda picking: picking.state == 'cancel'))

    def test_paid_invoiced_cancellation_waits_for_full_credit(self):
        order, original, parent, _payment_action = self._capture_fixture()
        snapshot = self._refund_snapshot(original, parent, [(24, 'SUCCESS')], quantity=2, restock='CANCEL')
        snapshot['cancelledAt'] = snapshot['updatedAt']
        action = self._apply_refund_snapshot(snapshot, order)
        with self.assertRaisesRegex(DeferredChannelError, 'shopify_cancel_waiting_for_credit'):
            self.store._execute_channel_action(self._sale_action(order))
        self.assertEqual(order.sale_id.state, 'sale')
        self.store._execute_channel_action(action)
        self.store._execute_channel_action(self._sale_action(order))
        self.assertEqual(order.sale_id.state, 'cancel')
        self.assertTrue(all(picking.state == 'cancel' for picking in order.sale_id.picking_ids))
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        self.assertEqual(refund.credit_note_id.amount_total, 24)

    def test_shipping_only_refund_leaves_stock_commitments_intact(self):
        order, original, parent, _payment_action = self._capture_fixture(shipping=True)
        snapshot = self._refund_snapshot(original, parent, [(5, 'SUCCESS')], quantity=0)
        refund_payload = snapshot['refunds'][0]
        refund_payload['totalRefundedSet'] = self._money('5')
        refund_payload['refundLineItems']['nodes'] = []
        refund_payload['refundShippingLines']['nodes'] = [{
            'id': 'gid://shopify/RefundShippingLine/1',
            'shippingLine': {'id': 'gid://shopify/ShippingLine/1'},
            'subtotalAmountSet': self._money('5'), 'taxAmountSet': self._money('0'),
        }]
        snapshot['shippingLines']['nodes'][0]['currentDiscountedPriceSet'] = self._money('0')
        action = self._apply_refund_snapshot(snapshot, order)
        self.store._execute_channel_action(action)
        refund = self.env['qing.channel.refund'].browse(action.data['refund'])
        self.assertEqual(refund.credit_note_id.amount_total, 5)
        self.assertEqual(len(refund.credit_note_id.invoice_line_ids), 1)
        self.assertNotEqual(refund.credit_note_id.invoice_line_ids.product_id, self.product)
        self.assertEqual(order.sale_id.picking_ids.move_ids.product_uom_qty, 2)

    def test_partial_shipping_refund_keeps_each_rounded_tax_on_its_original_account(self):
        order, original, parent, _action = self._capture_fixture(shipping=True, rounded_taxes=True)
        snapshot = self._refund_snapshot(original, parent, [(2.88, 'SUCCESS')], quantity=0)
        snapshot['currentTotalPriceSet'] = self._money('30.47')
        snapshot['currentTotalTaxSet'] = self._money('3.97')
        snapshot['shippingLines']['nodes'][0]['currentDiscountedPriceSet'] = self._money('2.50')
        refund = snapshot['refunds'][0]
        refund['totalRefundedSet'] = self._money('2.88')
        refund['refundLineItems']['nodes'] = []
        refund['refundShippingLines']['nodes'] = [{
            'id': 'gid://shopify/RefundShippingLine/1',
            'shippingLine': {'id': 'gid://shopify/ShippingLine/1'},
            'subtotalAmountSet': self._money('2.50'), 'taxAmountSet': self._money('0.38'),
        }]
        action = self._apply_refund_snapshot(snapshot, order)
        self.store._execute_channel_action(action)
        note = self.env['qing.channel.refund'].browse(action.data['refund']).credit_note_id
        self.assertNotEqual(note.invoice_line_ids.product_id, self.product)
        self._assert_shopify_credit_taxes(note, 2.50, (0.26, 0.12))

    def test_customer_retention_keeps_accounting_data_and_audits_authorised_reads(self):
        snapshot = self._snapshot()
        snapshot.update(email='sample@example.test', phone='555-0100')
        order = self.store._apply_shopify_order(snapshot)
        self.assertEqual(order.snapshot['shippingAddress'], {'countryCodeV2': 'US', 'provinceCode': 'CA'})
        self.assertNotIn('sample@example.test', repr(order.snapshot))
        self.assertNotIn('1 Example St', order.shopify_customer_blob)
        self.assertEqual(order.snapshot['currentTotalPriceSet'], snapshot['currentTotalPriceSet'])
        manager = new_test_user(self.env, login='channel-contact-2635',
                                groups='qing_channel.group_channel_manager', company_id=self.env.company.id)
        with self.assertRaises(AccessError):
            order.with_user(manager).read(['shopify_customer_blob'])
        audit = self.env['qing.audit.log'].sudo()
        self.assertEqual(order._read_shopify_customer_data()['email'], snapshot['email'])
        entry = audit.search([('model_name', '=', 'qing.channel.order'), ('record_id', '=', order.id),
                              ('operation', '=', 'read')])
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.changes, '{}')
        order.shopify_customer_expires_at = fields.Datetime.now() - timedelta(days=1)
        with patch.object(type(self.env['ir.cron']), '_commit_progress'):
            self.env['qing.channel.order']._cron_shopify_purge_customer_data()
        self.assertFalse(order.shopify_customer_blob)
        self.assertEqual(order.snapshot_version, 1)

        client = type('CustomerClient', (), {})()
        with (patch.object(type(self.store), '_shopify_client', return_value=client),
              patch.object(type(client), 'query', create=True) as query):
            query.return_value = {'order': dict(snapshot, email='corrected@example.test')}
            with self.assertRaisesRegex(ChannelReviewRequired, 'customer_snapshot_requires_refresh'):
                order._read_shopify_customer_data()
            query.return_value = {'order': snapshot}
            self.store._execute_channel_action(self._sale_action(order))
        self.assertEqual(order.sale_id.partner_id.email, snapshot['email'])
        self.assertEqual(order.sale_id.partner_shipping_id.street, '1 Example St')
        self.assertNotIn('address1', order.sale_id.shopify_commercial_data['shippingAddress'])
        self.assertFalse(order.shopify_customer_blob)

    def test_credential_audit_and_read_permissions(self):
        manager = new_test_user(self.env, login='channel-manager-2635',
                                groups='qing_channel.group_channel_manager', company_id=self.env.company.id)
        secret = token_urlsafe(24)
        with patch.dict(os.environ, {'QING_SHOPIFY_CREDENTIAL_KEY': Fernet.generate_key().decode()}):
            self.store.shopify_secret = secret
            self.assertNotEqual(self.store.shopify_secret, secret)
            self.assertTrue(self.store.shopify_secret.startswith('fernet:'))
            self.assertEqual(self.store._shopify_decrypt('shopify_secret'), secret)
        with self.assertRaises(AccessError):
            self.store.with_user(manager).read(['shopify_secret'])
        audit = self.env['qing.audit.log'].sudo().search([
            ('model_name', '=', 'qing.channel.store'), ('record_id', '=', self.store.id),
            ('operation', '=', 'write'), ('field_names', 'ilike', 'shopify_secret'),
        ])
        self.assertTrue(audit)
        self.assertTrue(all(secret not in str(item.changes) and 'fernet:' not in str(item.changes) for item in audit))

    def test_shopify_budget_learns_requested_cost_and_honors_cooldown(self):
        budget = self.env['qing.shopify.api.budget'].create({'store_key': self.store.id})
        clock = [1000.0]
        client = self.store._shopify_client()

        @contextmanager
        def bucket(_self, store_id):
            self.assertEqual(store_id, self.store.id)
            yield budget

        metadata = {'cost': {'requestedQueryCost': 100, 'actualQueryCost': 40,
                             'throttleStatus': {'maximumAvailable': 200, 'currentlyAvailable': 20, 'restoreRate': 10}}}
        responses = [
            {'data': {'test': 'ok'}, 'extensions': metadata},
            {'errors': [{'extensions': {'code': 'THROTTLED'}}], 'extensions': {'cost': {
                'requestedQueryCost': 100, 'actualQueryCost': None,
                'throttleStatus': {'maximumAvailable': 200, 'currentlyAvailable': 30, 'restoreRate': 10},
            }}},
        ]
        with (patch.object(type(budget), '_locked_budget', bucket),
              patch.object(api_budget, 'time', side_effect=lambda: clock[0]),
              patch.object(type(client), '_token', return_value='mock-token'),
              patch.object(type(client), '_post', side_effect=responses) as post):
            self.assertEqual(client.query('query Replay { test }'), {'test': 'ok'})
            self.assertEqual(budget.available, 20)
            with self.assertRaisesRegex(DeferredChannelError, 'shopify_throttled') as waiting:
                self.store._shopify_client().query('query Replay { test }')
            self.assertEqual(waiting.exception.retry_after, 8)
            self.assertEqual(post.call_count, 1)
            clock[0] += 8.1
            with TestCase().assertRaisesRegex(DeferredChannelError, 'shopify_throttled') as provider:
                client.query('query Replay { test }')
            self.assertEqual(provider.exception.retry_after, 7)
            self.assertEqual(budget.available, 30)

        delivery = self.env['qing.channel.delivery']._receive(
            self.store, 'rate-test', 'gid://shopify/Order/12', 'orders/updated',
        )
        before = fields.Datetime.now()
        for _ in range(9):
            delivery._retry('shopify_throttled', retry_after=7)
        self.assertEqual(delivery.state, 'retry')
        self.assertEqual(delivery.tries, 0)
        self.assertGreaterEqual(delivery.retry_at, before + timedelta(seconds=7))

    def test_bulk_history_recovers_unknown_start_and_advances_only_after_all_batches(self):
        result = self.store.action_shopify_start_bulk_history()
        self.assertEqual(result['domain'], [('store_id', '=', self.store.id)])
        run = self.store._shopify_active_bulk_import()
        with self.assertRaisesRegex(UserError, 'already active'):
            self.store.action_shopify_start_bulk_history()
        self.assertEqual(self.store._execute_channel_action(run.start_action_id), 'external')
        request = run.start_action_id.request_ids
        self.assertIn('query QingBulkOrders', request.payload['query'])
        cursor = self._advance_inventory_scan(run.window_from)
        remote_id = 'gid://shopify/BulkOperation/31'
        seen = []
        client = self.store._shopify_client()
        stamp = run.window_to.replace(tzinfo=timezone.utc).isoformat()
        batch = [(30, ('gid://shopify/Order/31', stamp)), (60, ('gid://shopify/Order/32', stamp)),
                 (90, ('gid://shopify/Order/33', stamp))]

        def shopify_reply(_client, document, variables):
            seen.append(document)
            if document == bulk.START_BULK:
                raise DeferredChannelError('shopify_transport_unknown')
            if document == bulk.RECENT_BULKS:
                return {'bulkOperations': {'nodes': [
                    {'id': remote_id, 'type': 'QUERY', 'query': request.payload['query']},
                ]}}
            self.assertEqual(document, bulk.BULK_STATUS)
            return {'bulkOperation': {'id': remote_id, 'status': 'COMPLETED', 'fileSize': '90',
                                      'rootObjectCount': '3', 'url': 'https://storage.googleapis.com/export.jsonl'}}

        def record_started(item):
            item.write({'first_sent_at': fields.Datetime.now(), 'state': 'uncertain'})

        def rows(_url, offset, size):
            self.assertEqual(size, 90)
            return (item for item in batch if item[0] > offset)

        Delivery = self.env['qing.channel.delivery']
        Delivery._receive(self.store, f'pull:{batch[0][1][0]}:{stamp}', batch[0][1][0], 'orders/reconcile')
        with (patch.object(type(self.store), '_shopify_client', return_value=client),
              patch.object(type(client), 'query', shopify_reply),
              patch.object(type(request), '_mark_before_send', record_started),
              patch.object(bulk_import, 'BATCH_SIZE', 2),
              patch.object(bulk_import, 'iter_bulk_rows', side_effect=rows) as downloads):
            with TestCase().assertRaisesRegex(DeferredChannelError, 'transport_unknown'):
                self.store._execute_channel_request(request, recovering=False)
            self.assertEqual(self.store._execute_channel_request(request, recovering=True), 'success')
            self.assertEqual(run.remote_id, remote_id)
            self.store._scan_shopify_orders()
            self.assertEqual(seen, [bulk.START_BULK, bulk.RECENT_BULKS])
            run._import_next_batch()
            self.assertEqual(run.state, 'running')
            self.assertEqual(run.queued_orders, 2)
            self.assertEqual(run.byte_offset, '60')
            self.assertEqual(cursor.through, run.window_from)
            run._import_next_batch()
            self.assertEqual([call.args[1] for call in downloads.call_args_list], [0, 60])
        self.assertEqual(run.state, 'done')
        self.assertEqual(run.queued_orders, 3)
        self.assertEqual(cursor.through, run.window_to)
        self.assertEqual(Delivery.search_count([('store_id', '=', self.store.id)]), 3)

    def test_bulk_jsonl_resumes_at_an_exact_byte_boundary(self):
        prefix = b'{"id":"gid://shopify/Order/31","updatedAt":"2026-11-17T12:00:00Z"}\n'
        last = b'{"id":"gid://shopify/Order/32","updatedAt":"2026-11-17T12:00:00Z"}'
        size = len(prefix) + len(last)
        response = type('BulkResponse', (), {})()
        url = 'https://storage.googleapis.com/shopify/export.jsonl?signature=test'
        response.status_code = 206
        response.headers = {'Content-Length': str(len(last)),
                            'Content-Range': f'bytes {len(prefix)}-{size - 1}/{size}'}
        response.iter_content = lambda chunk_size: iter((last[:23], last[23:]))
        context = MagicMock()
        context.__enter__.return_value = response
        with patch.object(bulk.requests, 'get', return_value=context) as download:
            result = list(bulk.iter_bulk_rows(url, len(prefix), size))
            self.assertEqual(result, [(size, ('gid://shopify/Order/32', '2026-11-17T12:00:00Z'))])
            self.assertEqual(download.call_args.kwargs['headers']['Range'], f'bytes={len(prefix)}-')
            response.iter_content = lambda chunk_size: iter((last[:23],))
            with self.assertRaisesRegex(DeferredChannelError, 'shopify_bulk_result_incomplete'):
                list(bulk.iter_bulk_rows(url, len(prefix), size))
            download.reset_mock()
            with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_bulk_result_url_invalid'):
                list(bulk.iter_bulk_rows('https://not-storage.example.com/export', 0, size))
            download.assert_not_called()

    def test_operations_show_store_queue_counts_and_open_their_filtered_records(self):
        delivery = self.env['qing.channel.delivery']._receive(
            self.store, 'ops-demo', 'gid://shopify/Order/15', 'orders/updated',
        )
        action = self.env['qing.channel.action']._ensure_action(self.store, 'ops-action', 'stock')
        request = self.env['qing.channel.request']._prepare_request(action, 'inventory_set', 'stock:15', {'target': 9})
        request.state = 'uncertain'
        self.env['qing.channel.exception']._flag(self.store, delivery, 'qa_example')
        binding = self._inventory_binding()
        binding.stock_hold_reason = 'shopify_stock_unverified_commitment'
        through = fields.Datetime.now()
        self._advance_inventory_scan(through)
        manager = new_test_user(self.env, login='shopify-operations-manager-2635',
                                groups='qing_channel.group_channel_manager', company_id=self.env.company.id)
        store = self.store.with_user(manager)
        self.assertEqual((store.ops_inbox_count, store.ops_action_count, store.ops_review_count,
                          store.ops_uncertain_count, store.ops_paused_count), (1, 1, 1, 1, 1))
        self.assertEqual(store.ops_orders_through, through)
        self.assertEqual(store.shopify_token_state, 'due')
        self.assertEqual(store.action_operations_inbox()['domain'],
                         [('store_id', '=', store.id), ('state', 'in', ('pending', 'retry'))])
        self.assertEqual(store.action_operations_paused_inventory()['domain'], [('id', 'in', [binding.id])])
        self.assertEqual(store.action_operations_jobs()['res_model'], action._name)

    def test_in_app_operations_alerts_are_company_scoped_and_sent_once_per_batch(self):
        manager = new_test_user(self.env, login='shopify-alert-manager-2635',
                                groups='qing_channel.group_channel_manager', company_id=self.env.company.id)
        user = new_test_user(self.env, login='shopify-alert-user-2635',
                             groups='qing_channel.group_channel_user', company_id=self.env.company.id)
        with self.assertRaises(ValidationError):
            self.store.ops_operator_id = user
        self.store.with_user(manager).ops_operator_id = manager
        with self.assertRaises(AccessError):
            self.store.with_user(manager).ops_alerted_exception_id = 900

        exceptions = self.env['qing.channel.exception']
        exceptions._flag(self.store, self.store, 'qa_first')
        exceptions._flag(self.store, self.store, 'qa_second')
        with patch.object(type(manager), '_bus_send', autospec=True) as notify:
            self.store._notify_operations_contact()
            self.store._notify_operations_contact()
            notify.assert_called_once()
            contact, channel, payload = notify.call_args.args
            self.assertEqual(contact, manager)
            self.assertEqual(channel, 'simple_notification')
            self.assertIn('(2)', payload['message'])
            exceptions._flag(self.store, self.store, 'qa_third')
            self.store._notify_operations_contact()
            self.assertEqual(notify.call_count, 2)
            self.assertIn('(1)', notify.call_args.args[2]['message'])

    def test_expired_token_is_refreshed_once_and_reused(self):
        client_id = token_urlsafe(18)
        secret = token_urlsafe(24)
        self.store.write({'shopify_client_id': client_id, 'shopify_secret': secret})
        now = fields.Datetime.now()
        self.store.write({'shopify_health': 'connected', 'shopify_token': token_urlsafe(24),
                          'shopify_token_until': now - timedelta(seconds=1)})
        client = self.store._shopify_client()
        replacement = token_urlsafe(24)
        with patch.object(type(client), '_post', return_value={'access_token': replacement, 'expires_in': 86400}) as post:
            self.assertEqual(client._token(), replacement)
            self.assertEqual(client._token(), replacement)
        post.assert_called_once_with('/admin/oauth/access_token', {
            'grant_type': 'client_credentials', 'client_id': client_id, 'client_secret': secret,
        })
        self.assertGreater(self.store.shopify_token_until, now + timedelta(hours=23))
        self.assertNotEqual(self.store.shopify_token, replacement)
        self.assertEqual(self.store.shopify_health, 'connected')

    def test_credential_job_renews_connected_store_before_expiry(self):
        @contextmanager
        def same_transaction(_registry, _lock):
            yield self.env

        now = fields.Datetime.now()
        self.store.write({'shopify_client_id': token_urlsafe(18), 'shopify_secret': token_urlsafe(24)})
        self.store.write({'shopify_health': 'connected', 'shopify_token': token_urlsafe(24),
                          'shopify_token_until': now + timedelta(minutes=10)})
        replacement = token_urlsafe(24)
        client = self.store._shopify_client()
        with (patch.object(shopify_store, 'locked_job_environment', same_transaction),
              patch.object(self.env.cr, 'commit'), patch.object(fields.Datetime, 'now', return_value=now),
              patch.object(type(client), '_post', return_value={'access_token': replacement, 'expires_in': 86399}) as post):
            self.store._cron_shopify_renew_credentials()
            self.store._cron_shopify_renew_credentials()
            post.assert_called_once()
        self.assertEqual(self.store._shopify_decrypt('shopify_token'), replacement)
        self.assertGreater(self.store.shopify_token_until, now + timedelta(hours=23))

    def _shopify_connection_response(self):
        return {'data': {
            'shop': {'myshopifyDomain': self.store.shopify_domain, 'currencyCode': self.CurrencyCode},
            'location': {'id': self.store.shopify_location, 'isActive': True},
            'currentAppInstallation': {
                'accessScopes': [{'handle': scope} for scope in sorted(shopify_store.REQUIRED_SCOPES)],
            },
            'customers': {'nodes': []},
            'orders': {'nodes': []},
        }}

    def _check_shopify_connection_with_response(self, response):
        Budget = type(self.env['qing.shopify.api.budget'])
        with (patch.object(Budget, '_reserve', return_value=10),
              patch.object(shopify_client.ShopifyClient, '_post', return_value=response)):
            return self.store.action_shopify_check_connection()

    def _arm_connected_shopify_store(self):
        self.store.write({
            'shopify_health': 'connected', 'onboarded': True, 'shopify_open_orders_verified': True,
            'shopify_scopes': ', '.join(sorted(shopify_store.REQUIRED_SCOPES)),
            'shopify_token': token_urlsafe(20), 'shopify_token_until': fields.Datetime.now() + timedelta(hours=1),
        })

    def test_protected_field_denial_without_error_code_disconnects_the_store(self):
        order = self.store._apply_shopify_order(self._snapshot())
        self._arm_connected_shopify_store()
        response = self._shopify_connection_response()
        response['errors'] = [{
            'message': 'This app is not approved to use the phone field. See private fixture details.',
            'path': ['orders', 'nodes', 0, 'phone'],
        }, {
            'message': 'This app is not approved to use the address1 field. See private fixture details.',
            'path': ['orders', 'nodes', 0, 'shippingAddress', 'address1'],
            'extensions': None,
        }]
        result = self._check_shopify_connection_with_response(response)
        message = result['params']['message']
        self.assertIn('phone', message)
        self.assertIn('address', message)
        self.assertNotIn('private fixture details', message)
        self.assertEqual(self.store.shopify_health, 'disconnected')
        self.assertFalse(self.store.shopify_token)
        self.assertFalse(self.store.shopify_scopes)
        self.assertFalse(self.store.onboarded)
        self.assertFalse(self.store.shopify_open_orders_verified)
        self.assertTrue(order.exists())
        denial = self.env['qing.channel.exception'].search([
            ('store_id', '=', self.store.id), ('code', '=', 'shopify_protected_customer_fields_denied'),
        ])
        self.assertFalse(denial.resolved)
        with self.assertRaisesRegex(DeferredChannelError, 'shopify_disconnected'):
            self.store._execute_channel_action(self._sale_action(order))

        self.store.write({
            'shopify_token': token_urlsafe(20), 'shopify_token_until': fields.Datetime.now() + timedelta(hours=1),
        })
        self._check_shopify_connection_with_response(self._shopify_connection_response())
        self.assertEqual(self.store.shopify_health, 'connected')
        self.assertTrue(denial.resolved)
        self.assertFalse(self.store.onboarded)
        self.assertFalse(self.store.shopify_open_orders_verified)

    def test_empty_shop_reports_denied_customer_access(self):
        self._arm_connected_shopify_store()
        response = self._shopify_connection_response()
        response['data']['customers'] = None
        response['errors'] = [{
            'message': 'This app is not approved to access the Customer object. See private fixture details.',
            'path': ['customers'],
            'extensions': {'code': 'ACCESS_DENIED'},
        }]

        result = self._check_shopify_connection_with_response(response)
        self.assertEqual(result['params']['type'], 'danger')
        self.assertIn('protected customer data', result['params']['message'])
        self.assertNotIn('private fixture details', result['params']['message'])
        self.assertEqual(self.store.shopify_health, 'disconnected')
        self.assertFalse(self.store.onboarded)

        self._arm_connected_shopify_store()
        response['errors'][0]['message'] = 'Access denied for customers field. Required access: read_customers.'
        result = self._check_shopify_connection_with_response(response)
        self.assertIn('shopify_scope_denied', result['params']['message'])
        self.assertNotIn('protected customer data', result['params']['message'])

    def test_incomplete_installation_or_changed_shop_details_pause_a_connected_store(self):
        mismatches = (
            ('currentAppInstallation', None, 'shopify_app_installation_unavailable'),
            ('location', {'id': 'gid://shopify/Location/12', 'isActive': True}, 'shopify_location_unavailable'),
            ('shop', {'myshopifyDomain': 'other.myshopify.com', 'currencyCode': self.CurrencyCode},
             'shopify_shop_identity_or_currency_mismatch'),
            ('customers', None, 'shopify_connection_response_incomplete'),
            ('orders', None, 'shopify_connection_response_incomplete'),
        )
        for field, value, code in mismatches:
            with self.subTest(field=field):
                self._arm_connected_shopify_store()
                response = self._shopify_connection_response()
                response['data'][field] = value
                result = self._check_shopify_connection_with_response(response)
                self.assertEqual(result['params']['type'], 'danger')
                self.assertEqual(self.store.shopify_health, 'disconnected')
                self.assertFalse(self.store.onboarded)
                self.assertFalse(self.store.shopify_scopes)
                self.assertTrue(self.env['qing.channel.exception'].search_count([
                    ('store_id', '=', self.store.id), ('code', '=', code), ('resolved', '=', False),
                ]))

    def test_connection_probe_reports_missing_sample_fields_as_unverified_without_revoking_valid_access(self):
        self._arm_connected_shopify_store()
        response = self._shopify_connection_response()
        example = {'id': 'gid://shopify/Order/1', 'email': None, 'phone': None,
                   'shippingAddress': {'firstName': 'QA', 'lastName': '', 'address1': '1 Fixture St', 'phone': None}}
        response['data']['orders']['nodes'] = [example]
        result = self._check_shopify_connection_with_response(response)
        message = result['params']['message']
        self.assertEqual(result['params']['type'], 'warning')
        self.assertIn('email, phone', message)
        self.assertNotIn('address', message)
        self.assertEqual(self.store.shopify_health, 'connected')
        self.assertTrue(self.store.onboarded)

        sample = dict(example, id='gid://shopify/Order/2', email='fixture@example.test', phone='+10000000000')
        response['data']['orders']['nodes'].append(sample)
        result = self._check_shopify_connection_with_response(response)
        self.assertNotEqual(result['params']['type'], 'warning')
        self.assertEqual(self.store.shopify_health, 'connected')

    def test_oauth_rejection_disconnects_and_keeps_order_history(self):
        @contextmanager
        def same_transaction(_registry, _lock):
            yield self.env

        order = self.store._apply_shopify_order(self._snapshot())
        self.store.write({'shopify_client_id': token_urlsafe(18), 'shopify_secret': token_urlsafe(24)})
        self.store.write({'shopify_health': 'connected', 'shopify_token': token_urlsafe(24),
                          'shopify_token_until': fields.Datetime.now() + timedelta(minutes=10),
                          'onboarded': True, 'shopify_open_orders_verified': True})
        with (patch.object(shopify_store, 'locked_job_environment', same_transaction),
              patch.object(self.env.cr, 'commit'),
              patch.object(shopify_client.requests, 'post', return_value=MagicMock(status_code=400)) as post):
            self.store._cron_shopify_renew_credentials()
            self.store._cron_shopify_renew_credentials()
            post.assert_called_once()
        self.assertEqual(self.store.shopify_health, 'disconnected')
        self.assertFalse(self.store.shopify_token)
        self.assertFalse(self.store.onboarded)
        self.assertFalse(self.store.shopify_open_orders_verified)
        self.assertTrue(order.exists())
        self.assertTrue(self.env['qing.channel.exception'].search_count([
            ('store_id', '=', self.store.id), ('code', '=', 'shopify_access_denied'), ('resolved', '=', False),
        ]))

    def test_token_refresh_distinguishes_malformed_and_temporary_responses(self):
        self.store.write({'shopify_client_id': token_urlsafe(18), 'shopify_secret': token_urlsafe(24)})
        client = self.store._shopify_client()
        with patch.object(type(client), '_post', return_value={'access_token': token_urlsafe(24), 'expires_in': 'invalid'}):
            with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_token_unavailable'):
                client._token()
        now = fields.Datetime.now()
        self.store.write({'shopify_token': token_urlsafe(24), 'shopify_token_until': now + timedelta(minutes=5)})
        with patch.object(type(client), '_post', side_effect=DeferredChannelError('shopify_transport_unknown')):
            with self.assertRaisesRegex(DeferredChannelError, 'shopify_transport_unknown'):
                client._token(renew_before=now + timedelta(minutes=15))
            self.store.shopify_token_until = now - timedelta(seconds=1)
            with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_token_refresh_failed'):
                client._token()

    def test_uninstall_after_last_verification_disconnects_but_older_event_does_not(self):
        now = fields.Datetime.now()
        order = self.store._apply_shopify_order(self._snapshot())
        self.store.write({'shopify_connected_at': now - timedelta(minutes=5),
                          'shopify_token': token_urlsafe(24), 'shopify_token_until': now + timedelta(hours=2),
                          'onboarded': True, 'shopify_open_orders_verified': True})
        delivery = self.env['qing.channel.delivery']
        old = delivery._receive(self.store, 'webhook:old', self.store.shopify_domain, 'app/uninstalled',
                                occurred_at=now - timedelta(minutes=6))
        self.store._import_delivery(old)
        self.assertEqual(self.store.shopify_health, 'connected')
        self.assertTrue(self.store.onboarded)
        current = delivery._receive(self.store, 'webhook:current', self.store.shopify_domain, 'app/uninstalled',
                                    occurred_at=now - timedelta(minutes=1))
        self.store._import_delivery(current)
        self.assertEqual(self.store.shopify_health, 'disconnected')
        self.assertFalse(self.store.shopify_token_until)
        self.assertFalse(self.store.onboarded)
        self.assertFalse(self.store.shopify_open_orders_verified)
        self.assertTrue(order.exists())

    def test_authorisation_failure_disables_inventory_and_financial_pushes(self):
        self.store.write({'onboarded': True, 'shopify_open_orders_verified': True})
        old_state = self.store.shopify_health
        self.assertEqual(old_state, 'connected')
        self.store._channel_operation_failed('shopify_scope_denied')
        self.assertEqual(self.store.shopify_health, 'disconnected')
        self.assertFalse(self.store.onboarded)
        self.assertFalse(self.store.shopify_open_orders_verified)
        order = self.store._apply_shopify_order(self._snapshot())
        with self.assertRaisesRegex(DeferredChannelError, 'shopify_disconnected'):
            self.store._execute_channel_action(self._sale_action(order))
        self.assertFalse(order.sale_id)

    def test_initial_order_scan_preserves_the_same_window_across_batches(self):
        now = fields.Datetime.now()
        start = now - timedelta(hours=1)
        self.store.shopify_history_from = start
        self.env['qing.channel.cursor'].search([('store_id', '=', self.store.id)]).unlink()
        end = now - timedelta(minutes=2)
        stamp = (start + timedelta(minutes=2)).replace(tzinfo=timezone.utc).isoformat()
        calls = []

        def order_page(_document, variables):
            calls.append(variables)
            offset = int(variables['after'] or '0')
            return {'orders': {'nodes': [{'id': f'gid://shopify/Order/{offset + 1}', 'updatedAt': stamp}],
                               'pageInfo': {'hasNextPage': offset < 4, 'endCursor': str(offset + 1)}}}

        client = MagicMock()
        client.query.side_effect = order_page
        with (patch.object(type(self.store), '_shopify_client', return_value=client),
              patch.object(self.env.cr, 'commit'), patch.object(fields.Datetime, 'now', return_value=now) as clock):
            self.store._scan_shopify_orders()
            cursor = self.env['qing.channel.cursor'].search([('store_id', '=', self.store.id)])
            self.assertEqual(cursor.page_cursor, '4')
            self.assertEqual(cursor.through, start)
            clock.return_value = now + timedelta(minutes=15)
            self.store._scan_shopify_orders()

        self.assertEqual(len(calls), 5)
        self.assertEqual({call['term'] for call in calls}, {
            f"updated_at:>='{start.isoformat()}Z' AND updated_at:<='{end.isoformat()}Z'",
        })
        self.assertEqual(cursor.through, end)
        self.assertFalse(cursor.page_cursor)
        self.assertEqual(self.env['qing.channel.delivery'].search_count([('store_id', '=', self.store.id)]), 5)

    def test_expired_order_cursor_restarts_the_same_window_once_without_losing_deliveries(self):
        now = fields.Datetime.now()
        start = now - timedelta(hours=1)
        end = now - timedelta(minutes=2)
        self.store.shopify_history_from = start
        self.env['qing.channel.cursor'].search([('store_id', '=', self.store.id)]).unlink()
        stamp = (start + timedelta(minutes=9)).replace(tzinfo=timezone.utc).isoformat()
        record = {'id': 'gid://shopify/Order/1', 'updatedAt': stamp}
        other = {'id': 'gid://shopify/Order/2', 'updatedAt': stamp}
        fake = MagicMock()
        failed_cursor = DeferredChannelError('shopify_page_cursor_invalid')
        fake.query.side_effect = [
            {'orders': {'nodes': [record], 'pageInfo': {'hasNextPage': True, 'endCursor': 'expired'}}},
            failed_cursor,
            {'orders': {'nodes': [record, other], 'pageInfo': {'hasNextPage': False, 'endCursor': 'done'}}},
        ]
        with (patch.object(type(self.store), '_shopify_client', return_value=fake),
              patch.object(self.env.cr, 'commit'), patch.object(fields.Datetime, 'now', return_value=now) as clock):
            self.store._scan_shopify_orders()
            cursor = self.env['qing.channel.cursor'].search([('store_id', '=', self.store.id)])
            self.assertEqual(cursor.through, start)
            self.assertFalse(cursor.page_cursor)
            self.assertEqual(cursor.window_end, end)
            clock.return_value = now + timedelta(minutes=15)
            self.store._scan_shopify_orders()
        terms = [call.args[1]['term'] for call in fake.query.call_args_list]
        self.assertEqual(len(set(terms)), 1)
        self.assertEqual(cursor.through, end)
        self.assertEqual(self.env['qing.channel.delivery'].search_count([('store_id', '=', self.store.id)]), 2)

    def test_legacy_order_cursor_replays_without_reusing_an_unbound_page_token(self):
        now = fields.Datetime.now()
        start = now - timedelta(minutes=45)
        end = now - timedelta(minutes=10)
        self.store.shopify_history_from = start
        cursor = self.env['qing.channel.cursor'].create({
            'store_id': self.store.id, 'stream': 'orders', 'through': start,
            'window_end': end, 'page_cursor': 'old-unbound-token', 'page_rows': 200,
        })
        stamp = (end - timedelta(minutes=1)).replace(tzinfo=timezone.utc).isoformat()
        external_id = 'gid://shopify/Order/3'
        Delivery = self.env['qing.channel.delivery']
        Delivery._receive(self.store, f'pull:{external_id}:{stamp}', external_id, 'orders/reconcile')
        client = MagicMock()
        client.query.return_value = {'orders': {
            'nodes': [{'id': external_id, 'updatedAt': stamp}],
            'pageInfo': {'hasNextPage': False, 'endCursor': 'last'},
        }}
        with patch.object(type(self.store), '_shopify_client', return_value=client):
            self.store._scan_shopify_orders()
        self.assertIsNone(client.query.call_args.args[1]['after'])
        self.assertEqual(cursor.through, end)
        self.assertFalse(cursor.window_start or cursor.window_end or cursor.page_cursor)
        self.assertEqual(Delivery.search_count([('store_id', '=', self.store.id)]), 1)

    def test_repeated_order_cursor_can_be_reset_by_a_channel_manager(self):
        now = fields.Datetime.now()
        start = now - timedelta(minutes=45)
        end = now - timedelta(minutes=10)
        self.store.shopify_history_from = start
        cursor = self.env['qing.channel.cursor'].create({
            'store_id': self.store.id, 'stream': 'orders', 'through': start,
            'window_start': start, 'window_end': end, 'page_cursor': 'same', 'page_rows': 50,
        })
        stamp = (end - timedelta(minutes=1)).replace(tzinfo=timezone.utc).isoformat()
        client = MagicMock()
        client.query.return_value = {'orders': {
            'nodes': [{'id': 'gid://shopify/Order/4', 'updatedAt': stamp}],
            'pageInfo': {'hasNextPage': True, 'endCursor': 'same'},
        }}
        with (patch.object(type(self.store), '_shopify_client', return_value=client),
              patch.object(self.env.cr, 'commit')):
            self.store._scan_shopify_orders()
            self.assertFalse(cursor.page_cursor)
            self.assertEqual(cursor.page_restarts, 1)
            with TestCase().assertRaisesRegex(ChannelReviewRequired, 'shopify_page_cursor_invalid_after_restart'):
                self.store._scan_shopify_orders()
        self.assertEqual(cursor.through, start)
        self.assertEqual(cursor.window_end, end)
        exception = self.env['qing.channel.exception']._flag(
            self.store, self.store, 'shopify_page_cursor_invalid_after_restart')
        operator = new_test_user(
            self.env, login='shopify-scan-operator', groups='qing_channel.group_channel_user',
            company_id=self.store.company_id.id)
        with self.assertRaises(AccessError):
            exception.with_user(operator).action_recheck()
        manager = new_test_user(
            self.env, login='shopify-scan-manager', groups='qing_channel.group_channel_manager',
            company_id=self.store.company_id.id)
        self.assertTrue(exception.with_user(manager).action_recheck())
        self.assertTrue(exception.resolved)
        self.assertEqual((cursor.window_start, cursor.window_end, cursor.through), (start, end, start))
        self.assertFalse(cursor.page_cursor or cursor.page_rows or cursor.page_restarts)

    def test_order_updated_after_the_cutoff_waits_for_the_next_scan_window(self):
        now = fields.Datetime.now()
        self.store.shopify_history_from = now - timedelta(minutes=25)
        end = now - timedelta(minutes=2)
        late = end + timedelta(seconds=1)
        client = MagicMock()
        client.query.return_value = {'orders': {
            'nodes': [
                {'id': 'gid://shopify/Order/1', 'updatedAt': end.replace(tzinfo=timezone.utc).isoformat()},
                {'id': 'gid://shopify/Order/2', 'updatedAt': late.replace(tzinfo=timezone.utc).isoformat()},
            ],
            'pageInfo': {'hasNextPage': False, 'endCursor': 'last'},
        }}
        domain = [('store_id', '=', self.store.id)]
        with (patch.object(type(self.store), '_shopify_client', return_value=client),
              patch.object(fields.Datetime, 'now', return_value=now) as clock):
            self.store._scan_shopify_orders()
            self.assertEqual(self.env['qing.channel.delivery'].search(domain).external_id, 'gid://shopify/Order/1')
            clock.return_value = now + timedelta(minutes=1)
            self.store._scan_shopify_orders()
        self.assertEqual(set(self.env['qing.channel.delivery'].search(domain).mapped('external_id')), {
            'gid://shopify/Order/1', 'gid://shopify/Order/2',
        })

    def test_shopify_client_classifies_an_invalid_cursor_without_hiding_other_errors(self):
        client = self.store._shopify_client()
        budget = self.env['qing.shopify.api.budget']
        with (patch.object(type(client), '_token', return_value='fixture-token'),
              patch.object(type(budget), '_reserve', return_value=10),
              patch.object(type(budget), '_observe'),
              patch.object(type(client), '_post') as post):
            post.return_value = {'errors': [{'message': 'Invalid pagination cursor.'}]}
            with self.assertRaisesRegex(DeferredChannelError, 'shopify_page_cursor_invalid'):
                client.query(orders.ORDER_PAGE, {'term': 'demo', 'after': 'cursor'})
            post.return_value = {'errors': [{'message': 'Temporary provider error.'}]}
            with self.assertRaisesRegex(DeferredChannelError, 'shopify_graphql_error'):
                client.query(orders.ORDER_PAGE, {'term': 'demo', 'after': 'cursor'})

    def test_older_order_window_requires_the_granted_history_scope(self):
        now = fields.Datetime.now()
        self.store.shopify_history_from = now - timedelta(days=75)
        with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_history_scope_required'):
            self.store._shopify_history_start(now)
        self.store.shopify_scopes = 'read_orders, read_all_orders'
        self.assertEqual(self.store._shopify_history_start(now), self.store.shopify_history_from)

    def test_channel_views_are_company_isolated(self):
        other = self.env['res.company'].create({'name': 'Another Store Company'})
        other_warehouse = self.env['stock.warehouse'].search([('company_id', '=', other.id)], limit=1)
        if not other_warehouse:
            other_warehouse = self.env['stock.warehouse'].create({
                'name': 'Other Warehouse', 'code': 'O2635', 'company_id': other.id,
            })
        other_store = self.env['qing.channel.store'].create({
            'name': 'Other Shop', 'platform': 'shopify', 'shopify_domain': 'other-2635.myshopify.com',
            'company_id': other.id, 'warehouse_id': other_warehouse.id,
        })
        reader = new_test_user(self.env, login='channel-user-2635', groups='qing_channel.group_channel_user',
                               company_id=self.env.company.id, company_ids=[Command.set(self.env.company.ids)])
        visible = self.env['qing.channel.store'].with_user(reader).search([])
        self.assertIn(self.store, visible)
        self.assertNotIn(other_store, visible)
        with self.assertRaises(AccessError):
            other_store.with_user(reader).read(['name'])
