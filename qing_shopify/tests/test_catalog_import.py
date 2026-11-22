from copy import deepcopy
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged
from odoo.addons.qing_channel.errors import DeferredChannelError

from ..services import catalog


@tagged('post_install', '-at_install')
class TestShopifyCatalog(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.company
        cls.store = cls.env['qing.channel.store'].create({
            'name': 'Catalog Store', 'platform': 'shopify', 'shopify_domain': 'catalog-test.myshopify.com',
            'company_id': company.id,
            'warehouse_id': cls.env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1).id,
            'shopify_currency_id': company.currency_id.id,
        })

    def _product(self, product, sku=None, status='DRAFT'):
        return {
            'id': f'gid://shopify/Product/{product}', 'title': f'Catalog Product {product}',
            'status': status, 'updatedAt': '2026-11-20T12:00:00Z',
            'variants': [{
                'id': f'gid://shopify/ProductVariant/{product}', 'sku': sku, 'requiresComponents': False,
                'barcodes': {'pageInfo': {'hasNextPage': False}, 'nodes': []},
                'inventoryItem': {'id': f'gid://shopify/InventoryItem/{product}', 'requiresShipping': False},
            }],
        }

    def _connection(self, nodes, cursor=False):
        return {'nodes': nodes, 'pageInfo': {'hasNextPage': bool(cursor), 'endCursor': cursor}}

    def test_catalog_shows_store_price_and_range_including_free_variants(self):
        snapshot = self._product(1)
        item = self.env['qing.shopify.catalog.item'].create({
            'name': snapshot['title'], 'store_id': self.store.id, 'remote_snapshot': snapshot,
        })
        currency = self.store.shopify_currency_id.name
        self.assertFalse(item.shop_price)
        item.remote_snapshot = dict(snapshot, variants=[{'price': '20.00'}, {'price': '20.0'}])
        self.assertEqual(item.shop_price, f'20.00 {currency}')
        item.remote_snapshot = dict(snapshot, variants=[{'price': '20.00'}, {'price': '0.00'}, {'price': 'NaN'}])
        self.assertEqual(item.shop_price, f'0.00–20.00 {currency}')

    def test_new_product_defaults_are_previewed_and_reconfirmed_when_changed(self):
        Product = self.env['product.product']
        Item = self.env['qing.shopify.catalog.item']
        existing_product = Product.create({
            'name': 'Previously configured service', 'type': 'service', 'invoice_policy': 'delivery',
            'default_code': 'CATALOG-EXISTING-CONFIG',
        })
        existing = self._product(1, existing_product.default_code)
        imported = self._product(2, 'CATALOG-NEW-CONFIG')
        run = self.env['qing.shopify.catalog.run'].create({'store_id': self.store.id})
        for snapshot in (existing, imported):
            Item._record_catalog_preview(run, snapshot['id'], snapshot)
        run._finish_catalog_scan()
        default_unit = self.env['product.template'].default_get(['uom_id'])['uom_id']
        self.assertEqual(run.product_defaults['uom_id'], default_unit)
        self.assertIn('Sales Taxes', run.product_defaults_preview)
        self.assertEqual(run.product_defaults['invoice_policy'], 'delivery')

        self.store.write({'shopify_invoice_policy': 'capture', 'shopify_catalog_purchase_ok': True})
        notification = run.action_catalog_confirm()
        self.assertEqual(notification['tag'], 'display_notification')
        self.assertEqual(run.state, 'preview')
        self.assertFalse(run.item_ids.filtered('apply_pending'))
        self.assertEqual(run.product_defaults['invoice_policy'], 'order')
        self.assertTrue(run.product_defaults['purchase_ok'])
        run.action_catalog_confirm()
        self.assertEqual(run.state, 'applying')

        snapshots = {snapshot['id']: snapshot for snapshot in (existing, imported)}
        with patch('odoo.addons.qing_shopify.models.catalog_run.fetch_product',
                   side_effect=lambda _client, gid: snapshots[gid]):
            while run.state == 'applying':
                run._step_catalog_job()
        self.assertEqual(run.state, 'done')
        new = self.store._shopify_variant_products([imported['variants'][0]['id']])[imported['variants'][0]['id']]
        self.assertEqual(new.invoice_policy, 'order')
        self.assertEqual(new.uom_id.id, default_unit)
        self.assertTrue(new.purchase_ok)
        self.assertEqual(new.categ_id, self.store.shopify_catalog_category_id)
        self.assertEqual(existing_product.invoice_policy, 'delivery')

    def test_complete_variant_fetch_restarts_on_product_change(self):
        product = self._product(1)
        variant = deepcopy(product['variants'][0])
        first = dict(product, variants=self._connection([variant], 'next'))
        variant2 = dict(variant, id='gid://shopify/ProductVariant/2')
        second = dict(product, variants=self._connection([variant2]))
        with patch.object(self.store._shopify_client().__class__, 'query', side_effect=[
            {'product': first}, {'product': second},
        ]) as query:
            result = catalog.fetch_product(self.store._shopify_client(), product['id'])
            self.assertEqual(len(result['variants']), 2)
            self.assertEqual(query.call_args.args[1]['after'], 'next')
        with patch.object(self.store._shopify_client().__class__, 'query', side_effect=[
            {'product': first}, {'product': dict(second, updatedAt='2026-11-20T13:00:00Z')},
        ]):
            with self.assertRaisesRegex(DeferredChannelError, 'shopify_catalog_product_changed'):
                catalog.fetch_product(self.store._shopify_client(), product['id'])

    def test_confirmed_catalog_import_reuses_order_binding_and_needs_no_write_scope(self):
        Product = self.env['product.product']
        Binding = self.env['qing.channel.binding']
        matched = Product.create({'name': 'Existing name', 'default_code': ' Catalog-SKU '})
        existing = self._product(1, 'cataLog-sku')
        missing_sku = self._product(2)
        archived = self._product(3, status='ARCHIVED')
        snapshots = {p['id']: p for p in (existing, missing_sku, archived)}

        def query(_client, doc, variables=None):
            if doc == catalog.PRODUCT_SCOPES:
                return {
                    'shop': {'myshopifyDomain': self.store.shopify_domain, 'currencyCode': self.env.company.currency_id.name},
                    'currentAppInstallation': {'accessScopes': [{'handle': 'read_products'}]},
                }
            if doc == catalog.CATALOG_PRODUCTS:
                return {'products': self._connection([{'id': gid} for gid in snapshots])}
            self.assertEqual(doc, catalog.CATALOG_PRODUCT)
            remote = snapshots[variables['id']]
            return {'product': dict(remote, variants=self._connection(remote['variants']))}

        action = self.store.action_shopify_preview_catalog()
        run = self.env['qing.shopify.catalog.run'].browse(action['res_id'])
        with patch.object(self.store._shopify_client().__class__, 'query', autospec=True, side_effect=query):
            while run.state == 'scanning':
                run._step_catalog_job()
            self.assertEqual(run.state, 'preview')
            self.assertEqual((run.link_count, run.new_count, run.archived_count), (1, 1, 1))
            self.assertFalse(Binding.search_count([('store_id', '=', self.store.id)]))
            run.action_catalog_confirm()
            while run.state == 'applying':
                run._step_catalog_job()
        self.assertEqual(run.state, 'done')
        mapping = self.store._shopify_variant_products([variant['id'] for p in snapshots.values() for variant in p['variants']])
        self.assertEqual(mapping[existing['variants'][0]['id']], matched)
        self.assertEqual(matched.name, 'Existing name')
        created = mapping[missing_sku['variants'][0]['id']]
        self.assertTrue(created.default_code.startswith('SHOPIFY-'))
        self.assertEqual(len(mapping), 2)
        new_action = self.store.action_shopify_preview_catalog()
        second_run = run.browse(new_action['res_id'])
        with patch.object(self.store._shopify_client().__class__, 'query', autospec=True, side_effect=query):
            while second_run.state == 'scanning':
                second_run._step_catalog_job()
            self.assertEqual((second_run.linked_count, second_run.new_count), (2, 0))
            second_run.action_catalog_confirm()
            while second_run.state == 'applying':
                second_run._step_catalog_job()
        self.assertEqual(Binding.search_count([('store_id', '=', self.store.id)]), 2)

    def test_duplicate_shopify_codes_across_catalog_pages_require_review(self):
        self.store.shopify_health = 'connected'
        erp_product = self.env['product.product'].create({'name': 'Existing', 'default_code': 'SAME-SKU'})
        sku_a = self._product(71, ' SAME-sku ')
        sku_b = self._product(72, 'same-SKU')
        barcode_a = self._product(73)
        barcode_b = self._product(74)
        archived = self._product(75, 'changed', status='ARCHIVED')
        for product, value in zip((barcode_a, barcode_b), (' Code-BC ', 'code-bc'), strict=True):
            product['variants'][0]['barcodes']['nodes'] = [{'value': value, 'type': 'OTHER'}]
        snapshots = {snap['id']: snap for snap in (sku_a, sku_b, barcode_a, barcode_b, archived)}
        page_one = [sku_a['id'], barcode_a['id']]
        page_two = [sku_b['id'], barcode_b['id'], archived['id']]

        def query(_client, document, variables=None):
            if document == catalog.PRODUCT_SCOPES:
                return {
                    'shop': {'myshopifyDomain': self.store.shopify_domain, 'currencyCode': self.env.company.currency_id.name},
                    'currentAppInstallation': {'accessScopes': [{'handle': 'read_products'}]},
                }
            if document == catalog.CATALOG_PRODUCTS:
                after = variables['after']
                ids, cursor = (page_two, False) if after else (page_one, 'next page')
                return {'products': self._connection([{'id': gid} for gid in ids], cursor)}
            self.assertEqual(document, catalog.CATALOG_PRODUCT)
            row = snapshots[variables['id']]
            return {'product': dict(row, variants=self._connection(row['variants']))}

        run = self.env['qing.shopify.catalog.run'].browse(self.store.action_shopify_preview_catalog()['res_id'])
        variant_ids = [snap['variants'][0]['id'] for snap in snapshots.values()]
        with patch.object(self.store._shopify_client().__class__, 'query', autospec=True, side_effect=query):
            while run.state == 'scanning':
                run._step_catalog_job()
            self.assertEqual(run.state, 'preview')
            self.assertEqual((run.review_count, run.new_count, run.link_count, run.archived_count), (4, 0, 0, 1))
            self.assertFalse(self.store._shopify_variant_products(variant_ids))
            self.assertTrue(all(item.issue == 'shopify_catalog_codes_ambiguous' for item in run.item_ids if item.state == 'review'))

            sku_b['variants'][0]['sku'] = 'CHANGED'
            first = run.item_ids.filtered(lambda item: item.external_id == sku_a['id'])
            second = run.item_ids.filtered(lambda item: item.external_id == sku_b['id'])
            second.action_catalog_recheck()
            first.action_catalog_recheck()
            self.assertEqual((first.state, second.state), ('link', 'new'))
            self.assertEqual(run.review_count, 2)
            run.action_catalog_confirm()
            while run.state == 'applying':
                run._step_catalog_job()

            later = self._product(76, 'same-sku')
            snapshots[later['id']] = later
            delivery = self.env['qing.channel.delivery']._receive(self.store, 'example-change-76', later['id'], 'products/update')
            self.store._import_delivery(delivery)

        mapping = self.store._shopify_variant_products(variant_ids)
        self.assertEqual(mapping[sku_a['variants'][0]['id']], erp_product)
        self.assertEqual(mapping[sku_b['variants'][0]['id']].default_code, 'CHANGED')
        self.assertEqual(len(mapping), 2)
        flagged = self.store._find_catalog_snapshot_item(later['id'], later)
        self.assertEqual((flagged.state, flagged.issue), ('review', 'shopify_catalog_codes_ambiguous'))
        self.assertFalse(self.store._shopify_variant_products([later['variants'][0]['id']]))

    def test_catalog_recheck_refreshes_preview_without_applying_it(self):
        self.store.shopify_health = 'connected'
        products = self.env['product.product'].create([
            {'name': 'First', 'default_code': 'DUPLICATE'},
            {'name': 'Second', 'default_code': 'DUPLICATE'},
        ])
        run = self.env['qing.shopify.catalog.run'].create({'store_id': self.store.id})
        product = self._product(40, 'DUPLICATE')
        item = self.env['qing.shopify.catalog.item']._record_catalog_preview(run, product['id'], product)
        run._finish_catalog_scan()
        self.assertEqual(item.state, 'review')

        updated = self._product(40, 'FIXED')
        products[0].default_code = 'FIXED'
        with patch.object(self.store._shopify_client().__class__, 'query', return_value={
            'product': dict(updated, variants=self._connection(updated['variants'])),
        }) as query:
            self.assertTrue(item.action_catalog_recheck())
            self.assertEqual(item.state, 'link')
            self.assertEqual(item.remote_snapshot, updated)
            self.assertFalse(self.store._shopify_variant_products([updated['variants'][0]['id']]))
            self.assertEqual(run.state, 'preview')
            query.assert_called_once()
            run.action_catalog_confirm()
            while run.state == 'applying':
                run._step_catalog_job()
        self.assertEqual(self.store._shopify_variant_products([updated['variants'][0]['id']])[updated['variants'][0]['id']], products[0])

    def test_catalog_recheck_queues_one_rematch_when_shopify_snapshot_is_unchanged(self):
        from odoo import fields

        self.store.write({'shopify_health': 'connected', 'shopify_catalog_imported_at': fields.Datetime.now()})
        products = self.env['product.product'].create([
            {'name': 'First', 'default_code': 'SAME'},
            {'name': 'Second', 'default_code': 'SAME'},
        ])
        snapshot = self._product(41, 'SAME')
        item = self.env['qing.shopify.catalog.item'].create({
            'name': snapshot['title'], 'store_id': self.store.id, 'external_id': snapshot['id'],
            'remote_snapshot': snapshot, 'remote_status': snapshot['status'],
        })
        item._refresh_catalog_plan()
        self.assertEqual(item.state, 'review')
        self.assertFalse(item.run_id)
        products[1].default_code = 'OTHER'

        first_reply = item.action_catalog_recheck()
        self.assertEqual(first_reply['tag'], 'display_notification')
        item.action_catalog_recheck()
        delivery = self.env['qing.channel.delivery'].search([
            ('store_id', '=', self.store.id), ('external_id', '=', snapshot['id']),
            ('topic', '=', catalog.CATALOG_RECHECK_TOPIC),
        ])
        self.assertEqual(len(delivery), 1)
        self.assertEqual(self.store._shopify_pending_imports(), 0)
        self.assertNotIn(catalog.CATALOG_RECHECK_TOPIC, catalog.WEBHOOK_PRODUCT_TOPICS)
        with patch.object(self.store._shopify_client().__class__, 'query', return_value={
            'product': dict(snapshot, variants=self._connection(snapshot['variants'])),
        }) as query:
            self.store._import_delivery(delivery)
            query.assert_called_once()
        self.assertEqual(item.state, 'linked')
        gid = snapshot['variants'][0]['id']
        self.assertEqual(self.store._shopify_variant_products([gid])[gid], products[0])

    def test_sparse_combinations_create_only_shopify_variants(self):
        snap = self._product(20)
        sample = snap['variants'][0]
        combinations = [('Red', 'M', 'Linen'), ('Red', 'L', 'Cotton'), ('Blue', 'M', 'Cotton')]
        snap['variants'] = [
            dict(sample, id=f'gid://shopify/ProductVariant/{n}',
                 selectedOptions=[{'name': label, 'value': value}
                                  for label, value in zip(('Color', 'Size', 'Fabric'), values, strict=True)])
            for n, values in enumerate(combinations, 20)
        ]
        run = self.env['qing.shopify.catalog.run'].create({'store_id': self.store.id})
        item = self.env['qing.shopify.catalog.item']._record_catalog_preview(run, snap['id'], snap)
        self.assertEqual(item.state, 'new')
        self.store._apply_shopify_catalog_product(item)
        products = self.env['qing.channel.binding'].search([
            ('store_id', '=', self.store.id), ('kind', '=', 'variant'),
        ]).product_id
        self.assertEqual(len(products), 3)
        self.assertEqual(len(products.product_tmpl_id), 1)
        self.assertEqual(products.product_tmpl_id.product_variant_count, 3)
        self.assertEqual(item.state, 'linked')

    def test_live_store_creates_new_inventory_mapping_paused(self):
        self.store.write({'onboarded': True, 'shopify_location': 'gid://shopify/Location/1'})
        product = self.env['product.product'].create({'name': 'Physical', 'type': 'consu', 'is_storable': True})
        self.store._map_shopify_inventory_item(product, {'id': 'gid://shopify/InventoryItem/67', 'tracked': True})
        binding = self.store._shopify_inventory_bindings()
        self.assertEqual(binding.product_id, product)
        self.assertTrue(binding.stock_paused and binding.shopify_takeover_pending)
        self.assertEqual(binding.quantity_version, 0)
        self.assertTrue(self.store.onboarded)

    def test_takeover_does_not_send_if_quantities_changed_since_preview(self):
        from odoo.addons.qing_channel.errors import ChannelReviewRequired

        self.store.write({
            'onboarded': True, 'shopify_location': 'gid://shopify/Location/1', 'shopify_health': 'connected',
        })
        product = self.env['product.product'].create({'name': 'Physical', 'type': 'consu', 'is_storable': True})
        self.store._map_shopify_inventory_item(product, {'id': 'gid://shopify/InventoryItem/68', 'tracked': True})
        binding = self.store._shopify_inventory_bindings()
        data = {'binding': binding.id, 'item': binding.external_id, 'location': self.store.shopify_location,
                'baseline': 8, 'target': 7, 'book': 3}
        action = self.env['qing.channel.action']._ensure_action(
            self.store, 'qa-stock-takeover', 'shopify_stock_takeover', data=data,
        )
        self.assertEqual(self.store._execute_channel_action(action), 'external')
        with patch.object(type(self.store), '_check_shopify_takeover', return_value=(7, 6, 4)):
            with self.assertRaisesRegex(ChannelReviewRequired, 'shopify_stock_takeover_preview_changed'):
                self.store._execute_channel_request(action.request_ids, recovering=False)
        self.assertTrue(binding.stock_paused)
        self.assertFalse(action.request_ids.first_sent_at)

    def test_import_sets_only_missing_shop_specific_fixed_price(self):
        company = self.env.company
        pricelist = self.env['product.pricelist'].create({
            'name': 'Catalog only', 'company_id': company.id, 'currency_id': company.currency_id.id,
        })
        self.store.shopify_pricelist_id = pricelist
        snapshot = self._product(300, 'CAT300')
        snapshot['variants'][0]['price'] = '17.50'
        run = self.env['qing.shopify.catalog.run'].create({
            'store_id': self.store.id, 'shop_currency': company.currency_id.name, 'shop_prices_include_tax': False,
        })
        item = self.env['qing.shopify.catalog.item']._record_catalog_preview(run, snapshot['id'], snapshot)
        self.store._apply_shopify_catalog_product(item)
        product = self.store._shopify_variant_products([snapshot['variants'][0]['id']])[snapshot['variants'][0]['id']]
        self.assertEqual(product.list_price, 0)
        price = pricelist.item_ids.filtered(lambda rule: rule.product_id == product)
        self.assertEqual(price.fixed_price, 17.5)
        snapshot['variants'][0]['price'] = '18.25'
        item = self.env['qing.shopify.catalog.item']._record_catalog_preview(run, snapshot['id'], snapshot)
        self.store._apply_shopify_catalog_product(item)
        self.assertEqual(price.fixed_price, 17.5)
        self.assertIn('shopify_catalog_variant_price_differs', item.issue)
