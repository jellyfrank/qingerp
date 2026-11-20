import re

from psycopg2.errors import UniqueViolation

from odoo import api, fields, models
from odoo.addons.qing_base.tools.channel_queue import locked_job_environment
from odoo.addons.qing_channel.errors import ChannelReviewRequired, DeferredChannelError
from odoo.fields import Domain


BARCODE_FIELDS = 'barcodes(first: 20) { nodes { value } pageInfo { hasNextPage } }'
VARIANT_FIELDS = f'id sku displayName requiresComponents product {{ status }} {BARCODE_FIELDS}'
SKU_MATCHES = 'productVariants(first: 20, query: $sku) { nodes { id sku } pageInfo { hasNextPage } }'
LOOKUP_VARIANT = f'''
query ChannelLookupVariant($id: ID!) {{
    productVariant(id: $id) {{ {VARIANT_FIELDS} }}
}}
'''
MATCH_SKU = f'''
query ChannelMatchSku($sku: String!) {{
    skuMatches: {SKU_MATCHES}
}}
'''
MATCH_VARIANT = f'''
query ChannelMatchVariant($id: ID!, $sku: String!) {{
    productVariant(id: $id) {{ {VARIANT_FIELDS} }}
    skuMatches: {SKU_MATCHES}
}}
'''
MATCH_BARCODE = f'''
query ChannelMatchBarcode($query: String!) {{
    productVariants(first: 12, query: $query) {{
        nodes {{ id {BARCODE_FIELDS} }} pageInfo {{ hasNextPage }}
    }}
}}
'''

def _catalog_search_value(field, value):
    escaped = re.sub(r'([\\:()"])', r'\\\1', value)
    return f'{field}:"{escaped}"'


def _barcode_values(variant):
    connection = variant['barcodes']
    if connection['pageInfo']['hasNextPage']:
        raise ChannelReviewRequired('shopify_catalog_codes_ambiguous')
    return {item['value'] for item in connection['nodes'] if item['value']}


def _check_catalog_match(connection, matches, variant_id):
    if connection['pageInfo']['hasNextPage'] or len(matches) > 1:
        raise ChannelReviewRequired('shopify_catalog_codes_ambiguous')
    if matches != {variant_id}:
        raise ChannelReviewRequired('shopify_catalog_variant_unverified')


class ShopifyStoreCatalog(models.Model):
    _inherit = 'qing.channel.store'

    @api.model
    def _cron_shopify_recover_product_mappings(self):
        domain = [('platform', '=', 'shopify'), ('active', '=', True), ('shopify_health', '=', 'connected')]
        for store in self.sudo().search(domain):
            with locked_job_environment(self.env.registry, f'qing-shopify-product-recovery:{store.id}') as env:
                if env is None:
                    continue
                shop = env[self._name].browse(store.id).with_company(store.company_id)
                if shop._channel_worker_ready():
                    try:
                        with env.cr.savepoint():
                            shop._shopify_recover_product_mappings()
                    except DeferredChannelError as exc:
                        shop._channel_operation_failed(exc.code)
                    env.cr.commit()

    def _shopify_recover_product_mappings(self, limit=100):
        Cursor = self.env['qing.channel.cursor']
        domain = [('store_id', '=', self.id), ('stream', '=', 'shopify_product_mapping')]
        cursor = Cursor.search(domain, limit=1)
        after_id = int(cursor.page_cursor or 0)
        lines = self.env['qing.channel.order.line'].search([
            ('order_id.store_id', '=', self.id), ('order_id.historical', '=', False),
            ('order_id.sale_id', '=', False), ('id', '>', after_id), ('quantity', '>', 0),
            ('product_id', '=', False), ('variant_id', '!=', False),
        ], order='id', limit=limit)
        orders = lines.order_id.filtered(lambda order: not order.snapshot.get('cancelledAt'))
        pending = orders.line_ids.filtered(lambda line: line.quantity > 0 and not line.product_id)
        bindings = self._shopify_variant_products(pending.mapped('variant_id'))
        checked = set()
        last_id = after_id
        for line in lines:
            variant_id = line.variant_id
            if variant_id not in bindings and variant_id not in checked and line.order_id in orders:
                if len(checked) >= 8:
                    break
                checked.add(variant_id)
                try:
                    bindings[variant_id] = self._resolve_shopify_product(line.snapshot, False)
                except ChannelReviewRequired:
                    pass
            last_id = line.id

        Delivery = self.env['qing.channel.delivery']
        pending_by_order = pending.grouped('order_id')
        for order in orders:
            if all(line.variant_id in bindings for line in pending_by_order[order]):
                Delivery._receive(
                    self, f'product-rematch:{order.id}:{order.snapshot_version}', order.external_id, 'orders/reconcile',
                )
        if lines or cursor:
            more_lines = lines and (len(lines) == limit or last_id < lines[-1].id)
            values = {'page_cursor': str(last_id) if more_lines else False}
            if cursor:
                cursor.write(values)
            else:
                Cursor.create(dict(values, store_id=self.id, stream='shopify_product_mapping', through=fields.Datetime.now()))

    def _shopify_variant_products(self, variant_ids):
        bindings = self.env['qing.channel.binding'].search([
            ('store_id', '=', self.id), ('kind', '=', 'variant'), ('external_id', 'in', variant_ids),
        ])
        return {binding.external_id: binding.product_id for binding in bindings}

    def _resolve_shopify_product(self, data, bound_product):
        variant = data.get('variant') or {}
        if data.get('isGiftCard') or data.get('lineItemGroup') or variant.get('requiresComponents'):
            raise ChannelReviewRequired('shopify_product_outside_pilot')
        if not variant.get('id'):
            raise ChannelReviewRequired('shopify_variant_deleted_or_unavailable')
        if bound_product:
            self._check_shopify_product(bound_product, data)
            self._map_shopify_inventory_item(bound_product, variant.get('inventoryItem') or {})
            return bound_product

        # Serialize new product identities across deliveries and the recovery job.
        self.env.cr.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))',
                            [f'qing-shopify-catalog:{self.id}'])
        locked, = self.env.cr.fetchone()
        if not locked:
            raise DeferredChannelError('shopify_product_mapping_in_progress', retry_after=30)
        Binding = self.env['qing.channel.binding']
        try:
            with self.env.cr.savepoint():
                product = self._find_shopify_product(data)
                self._check_shopify_product(product, data)
                self._map_shopify_inventory_item(product, variant.get('inventoryItem') or {})
                Binding.create({
                    'store_id': self.id, 'kind': 'variant',
                    'external_id': variant['id'], 'product_id': product.id,
                })
        except UniqueViolation as exc:
            if exc.diag.constraint_name != 'qing_channel_binding_external_unique':
                raise
            raise DeferredChannelError('shopify_product_mapping_in_progress', retry_after=30) from None
        return product

    def _check_shopify_product(self, product, data):
        if not product.active or not product.sale_ok or (data['requiresShipping'] and not product.is_storable):
            raise ChannelReviewRequired('shopify_stock_product_mapping_required')
        if product.type == 'combo' or (
            'mrp.bom' in self.env and self.env['mrp.bom'].sudo()._bom_find(
                product, company_id=self.company_id.id, bom_type='phantom'
            )[product]
        ):
            raise ChannelReviewRequired('shopify_product_outside_pilot')

    def _find_shopify_product(self, data):
        variant_id = data['variant']['id']
        sku = (data.get('sku') or '').strip()
        client = self._shopify_client()
        if sku:
            result = client.query(MATCH_VARIANT, {'id': variant_id, 'sku': _catalog_search_value('sku', sku)})
        else:
            result = client.query(LOOKUP_VARIANT, {'id': variant_id})
        current = result.get('productVariant')
        if not current or current['id'] != variant_id:
            raise ChannelReviewRequired('shopify_variant_deleted_or_unavailable')
        if current.get('requiresComponents'):
            raise ChannelReviewRequired('shopify_product_outside_pilot')
        sku = sku or (current.get('sku') or '').strip()
        barcodes = _barcode_values(current)
        if sku:
            sku_matches = result.get('skuMatches')
            if sku_matches is None:
                sku_matches = client.query(MATCH_SKU, {'sku': _catalog_search_value('sku', sku)})['skuMatches']
            exact_sku_ids = {entry['id'] for entry in sku_matches['nodes']
                             if (entry['sku'] or '').casefold() == sku.casefold()}
            _check_catalog_match(sku_matches, exact_sku_ids, variant_id)

        Product = self.env['product.product'].with_company(self.company_id).with_context(active_test=False)
        scope = Domain('company_id', 'in', [False, self.company_id.id])
        criteria = Domain('default_code', '=', sku) if sku else Domain.FALSE
        if barcodes:
            criteria |= Domain('barcode', 'in', sorted(barcodes))
        products = Product.search(scope & criteria, limit=2)
        if len(products) > 1:
            raise ChannelReviewRequired('shopify_product_codes_ambiguous')
        if not products:
            if self.shopify_import_unmatched_products:
                return self._import_shopify_product(Product, client, current, data, sku, barcodes, scope)
            raise ChannelReviewRequired('shopify_product_mapping_required' if sku or barcodes else 'shopify_sku_missing')
        already_used = [('store_id', '=', self.id), ('kind', '=', 'variant'),
                        ('product_id', '=', products.id), ('external_id', '!=', variant_id)]
        if self.env['qing.channel.binding'].search_count(already_used, limit=1):
            raise ChannelReviewRequired('shopify_product_matches_multiple_variants')
        if not sku or products.default_code != sku:
            self._check_shopify_catalog_barcodes(client, {products.barcode}, variant_id)
        return products

    def _check_shopify_catalog_barcodes(self, client, barcodes, variant_id):
        query = ' OR '.join(_catalog_search_value('barcode', barcode) for barcode in sorted(barcodes))
        matches = client.query(MATCH_BARCODE, {'query': query})['productVariants']
        expected = {barcode.casefold() for barcode in barcodes}
        matching_ids = {entry['id'] for entry in matches['nodes']
                        if any(value.casefold() in expected for value in _barcode_values(entry))}
        _check_catalog_match(matches, matching_ids, variant_id)

    def _import_shopify_product(self, Product, client, variant, data, sku, barcodes, scope):
        if (variant.get('product') or {}).get('status') != 'ACTIVE' or not variant.get('displayName'):
            raise ChannelReviewRequired('shopify_product_mapping_required')
        if barcodes:
            self._check_shopify_catalog_barcodes(client, barcodes, variant['id'])
        code = sku or f"SHOPIFY-{self.id}-{variant['id'].rsplit('/', 1)[-1]}"
        if not sku and Product.search_count(scope & Domain('default_code', '=', code), limit=1):
            raise ChannelReviewRequired('shopify_product_codes_ambiguous')
        return Product.create({
            'name': variant['displayName'], 'company_id': self.company_id.id,
            'default_code': code, 'barcode': next(iter(barcodes)) if len(barcodes) == 1 else False,
            'type': 'consu' if data['requiresShipping'] else 'service',
            'is_storable': data['requiresShipping'], 'sale_ok': True, 'purchase_ok': False,
            'list_price': 0.0,
        })

    def _map_shopify_inventory_item(self, product, inventory):
        if not product.is_storable or not inventory.get('tracked') or not inventory.get('id') or not self.shopify_location:
            return
        Binding = self.env['qing.channel.binding']
        scope = Domain('store_id', '=', self.id) & Domain('kind', '=', 'inventory') & Domain(
            'location_external_id', '=', self.shopify_location)
        candidates = Binding.search(scope & (
            Domain('external_id', '=', inventory['id']) | Domain('product_id', '=', product.id)
        ), limit=2)
        if candidates:
            if len(candidates) != 1 or candidates.external_id != inventory['id'] or candidates.product_id != product:
                raise ChannelReviewRequired('shopify_inventory_mapping_conflict')
            return
        if not self.onboarded:
            Binding.create({'store_id': self.id, 'kind': 'inventory', 'product_id': product.id,
                            'external_id': inventory['id'], 'location_external_id': self.shopify_location})

    shopify_import_unmatched_products = fields.Boolean(
        string='Import Unmatched Shopify Products', copy=False,
        help='For active Shopify variants on orders, use a unique SKU or barcode match first. '
             'If no existing product matches, create a product and link it to the Shopify variant.',
    )
