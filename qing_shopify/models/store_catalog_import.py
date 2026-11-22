from collections import Counter
from datetime import timedelta

from odoo import _, Command, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL
from odoo.addons.qing_base.tools.channel_queue import locked_job_environment
from odoo.addons.qing_channel.errors import ChannelReviewRequired, DeferredChannelError

from ..services.catalog import (
    CATALOG_RECHECK_TOPIC, PRODUCT_SCOPES, PRODUCT_TOPICS, fetch_product, normalize_catalog_code, product_ids,
    variant_code_keys,
)


def _variant_codes(variant):
    barcodes = variant.get('barcodes') or {}
    if barcodes.get('pageInfo', {}).get('hasNextPage') is not False:
        raise ChannelReviewRequired('shopify_catalog_codes_incomplete')
    values = {normalize_catalog_code(node.get('value')) for node in barcodes['nodes']}
    return normalize_catalog_code(variant.get('sku')), values - {''}


class ShopifyStoreCatalogImport(models.Model):
    _inherit = 'qing.channel.store'

    def _import_delivery(self, delivery):
        if self.platform != 'shopify' or delivery.topic not in PRODUCT_TOPICS:
            return super()._import_delivery(delivery)
        if not self.shopify_catalog_imported_at:
            return
        if self.shopify_health != 'connected':
            raise DeferredChannelError('shopify_disconnected')
        snapshot = fetch_product(self._shopify_client(), delivery.external_id)
        items = self.env['qing.shopify.catalog.item']
        item = self._find_catalog_snapshot_item(delivery.external_id, snapshot)
        if item and item.run_id.state in ('scanning', 'preview', 'applying'):
            raise DeferredChannelError('shopify_catalog_preview_in_progress', retry_after=60)
        if not snapshot:
            if item:
                item.write({'state': 'archived', 'remote_status': 'DELETED', 'issue': False})
                self._withdraw_catalog_variants(item, [])
            return
        if delivery.topic != CATALOG_RECHECK_TOPIC and item and (
            item.remote_snapshot == snapshot and item.state not in ('new', 'link', 'different')
        ):
            return
        values = {'remote_snapshot': snapshot, 'remote_status': snapshot['status'], 'name': snapshot['title']}
        if item:
            item.write(values)
        else:
            item = items.create(dict(values, store_id=self.id, external_id=delivery.external_id))
        item._refresh_catalog_plan()
        self._catalog_observed_product(item)
        if item.state == 'archived':
            self._withdraw_catalog_variants(item, [])
        if item.state in ('new', 'link', 'different', 'linked'):
            try:
                with self.env.cr.savepoint():
                    self._apply_shopify_catalog_product(item)
            except ChannelReviewRequired as exc:
                item.write({'state': 'review', 'issue': exc.code})

    def _catalog_observed_product(self, item):
        """Allow optional catalog extensions to schedule independent work."""

    def _find_catalog_snapshot_item(self, external_id, snapshot):
        return self.env['qing.shopify.catalog.item'].search([
            ('store_id', '=', self.id), ('external_id', '=', external_id),
        ], limit=1)

    @api.model
    def _cron_shopify_catalog_reconcile(self):
        domain = [
            ('shopify_catalog_imported_at', '!=', False), ('platform', '=', 'shopify'),
            ('active', '=', True), ('shopify_health', '=', 'connected'),
        ]
        for store in self.sudo().search(domain):
            with locked_job_environment(self.env.registry, f'qing-shopify-catalog-scan:{store.id}') as env:
                if env is None:
                    continue
                shop = env[self._name].browse(store.id).with_company(store.company_id)
                try:
                    shop._scan_shopify_catalog_changes()
                    shop._schedule_shopify_catalog_presence()
                except DeferredChannelError as exc:
                    env.cr.rollback()
                    if isinstance(exc, ChannelReviewRequired):
                        env['qing.channel.exception']._flag(shop, shop, exc.code)
                env.cr.commit()

    def _catalog_reconcile_cursor(self, stream, initial):
        Cursor = self.env['qing.channel.cursor']
        return Cursor.search([('store_id', '=', self.id), ('stream', '=', stream)], limit=1) or Cursor.create({
            'store_id': self.id, 'stream': stream, 'through': initial,
        })

    def _scan_shopify_catalog_changes(self):
        self.ensure_one()
        now = fields.Datetime.now()
        cursor = self._catalog_reconcile_cursor('shopify_catalog_changes', self.shopify_catalog_imported_at)
        if not cursor.window_end:
            if now - cursor.through < timedelta(minutes=15):
                return
            cursor.write({'window_start': cursor.through - timedelta(minutes=1), 'window_end': now})
        since = cursor.window_start.strftime('%Y-%m-%dT%H:%M:%SZ')
        until = cursor.window_end.strftime('%Y-%m-%dT%H:%M:%SZ')
        client = self._shopify_client()
        try:
            ids, after = product_ids(client, cursor.page_cursor, f"updated_at:>='{since}' updated_at:<='{until}'")
        except DeferredChannelError as exc:
            if exc.code != 'shopify_page_cursor_invalid':
                raise
            if cursor.page_restarts:
                raise ChannelReviewRequired('shopify_catalog_cursor_invalid_after_restart') from None
            cursor.write({'page_cursor': False, 'page_restarts': 1})
            return
        Delivery = self.env['qing.channel.delivery']
        for gid in ids:
            Delivery._receive(self, f'catalog:{until}:{gid}', gid, 'products/reconcile')
        if after:
            cursor.page_cursor = after
        else:
            cursor._finish_window(cursor.window_end)

    def _schedule_shopify_catalog_presence(self):
        self.ensure_one()
        now = fields.Datetime.now()
        cursor = self._catalog_reconcile_cursor('shopify_catalog_presence', now)
        if not cursor.window_end:
            if now - cursor.through < timedelta(days=1):
                return
            cursor.window_end = now
        items = self.env['qing.shopify.catalog.item'].search([
            ('store_id', '=', self.id), ('external_id', '!=', False), ('state', '!=', 'archived'),
            ('id', '>', int(cursor.page_cursor or 0)),
        ], order='id', limit=100)
        Delivery = self.env['qing.channel.delivery']
        day = cursor.window_end.date().isoformat()
        for item in items:
            Delivery._receive(self, f'catalog-presence:{day}:{item.external_id}', item.external_id, 'products/reconcile')
        if len(items) == 100:
            cursor.page_cursor = str(items[-1].id)
        else:
            cursor._finish_window(cursor.window_end)

    def _catalog_read_permission(self):
        self.ensure_one()
        data = self._shopify_client().query(PRODUCT_SCOPES)
        scopes = (data.get('currentAppInstallation') or {}).get('accessScopes') or []
        if 'read_products' not in {scope['handle'] for scope in scopes}:
            raise ChannelReviewRequired('shopify_catalog_read_scope_required')
        shop = data.get('shop') or {}
        if shop.get('myshopifyDomain') != self.shopify_domain or not shop.get('currencyCode'):
            raise ChannelReviewRequired('shopify_shop_identity_or_currency_mismatch')
        return shop

    def _catalog_candidates(self, codes):
        skus = sorted({sku for sku, _ in codes.values()} - {''})
        barcodes = sorted(set().union(*(barcodes for _, barcodes in codes.values())))
        Product = self.env['product.product'].with_company(self.company_id).with_context(active_test=False)
        if not skus and not barcodes:
            return Product.browse()
        Product.flush_model(['default_code', 'barcode', 'product_tmpl_id'])
        self.env['product.template'].flush_model(['company_id'])
        ids = self.env.execute_query(SQL("""
            SELECT p.id
              FROM product_product p JOIN product_template t ON t.id = p.product_tmpl_id
             WHERE (t.company_id IS NULL OR t.company_id = %s)
               AND (lower(btrim(p.default_code)) = ANY(%s) OR lower(btrim(p.barcode)) = ANY(%s))
            """, self.company_id.id, skus, barcodes))
        return Product.browse([pid for pid, in ids])

    def _catalog_has_conflicting_codes(self, snapshot, keys, catalog_run):
        if not keys:
            return False
        self.env['qing.shopify.catalog.item'].flush_model(['catalog_code_keys', 'run_id'])
        run_filter = SQL('AND run_id = %s', catalog_run.id) if catalog_run else SQL()
        found, = self.env.execute_query(SQL("""
            SELECT EXISTS (
                SELECT 1
                  FROM qing_shopify_catalog_item
                 WHERE store_id = %s AND external_id != %s AND catalog_code_keys ?| %s::text[]
                       %s
            )
        """, self.id, snapshot['id'], sorted(keys), run_filter))[0]
        return found

    def _plan_shopify_catalog_product(self, snapshot, *, catalog_run=None, check_catalog_codes=True):
        self.ensure_one()
        if snapshot.get('status') == 'ARCHIVED':
            return 'archived', {}
        variants = snapshot['variants']
        if snapshot.get('isGiftCard'):
            return 'skipped', {'reason': 'shopify_gift_card'}
        in_scope = [variant for variant in variants if not variant['requiresComponents']]
        if not in_scope:
            return 'skipped', {'reason': 'shopify_bundle'}
        scope = [('store_id', '=', self.id), ('kind', '=', 'variant')]
        Binding = self.env['qing.channel.binding']
        bindings = Binding.search(scope + [('external_id', 'in', [variant['id'] for variant in in_scope])])
        products = {binding.external_id: binding.product_id for binding in bindings}
        unknown = [variant for variant in in_scope if variant['id'] not in products]
        codes = {variant['id']: _variant_codes(variant) for variant in unknown}
        candidates = self._catalog_candidates(codes)
        counts = Counter(
            value for variant in in_scope
            for value in {key.split(':', 1)[1] for key in variant_code_keys(variant)}
        )
        new_keys = {key for variant in unknown for key in variant_code_keys(variant)}
        if any(counts[key.split(':', 1)[1]] > 1 for key in new_keys) or (
            check_catalog_codes and self._catalog_has_conflicting_codes(snapshot, new_keys, catalog_run)
        ):
            raise ChannelReviewRequired('shopify_catalog_codes_ambiguous')
        already_used = {
            binding.product_id.id for binding in Binding.search(scope + [('product_id', 'in', candidates.ids)])
        }
        matched_ids = set(bindings.product_id.ids)
        plan = {}
        for variant in variants:
            gid = variant['id']
            if variant['requiresComponents']:
                plan[gid] = {'action': 'skip', 'reason': 'shopify_bundle'}
                continue
            product = products.get(gid)
            if product:
                self._check_catalog_variant(product, variant)
                plan[gid] = {'action': 'keep', 'product': product.id}
                continue
            sku, barcodes = codes[gid]
            matches = candidates.filtered(
                lambda product: (sku and normalize_catalog_code(product.default_code) == sku)
                or normalize_catalog_code(product.barcode) in barcodes
            )
            if len(matches) > 1:
                raise ChannelReviewRequired('shopify_product_codes_ambiguous')
            if not matches:
                plan[gid] = {'action': 'create'}
                continue
            if matches.id in already_used or matches.id in matched_ids:
                raise ChannelReviewRequired('shopify_product_matches_multiple_variants')
            self._check_catalog_variant(matches, variant)
            matched_ids.add(matches.id)
            plan[gid] = {'action': 'link', 'product': matches.id}
        templates = self.env['product.product'].browse(list(matched_ids)).product_tmpl_id
        actions = {line['action'] for line in plan.values()}
        if 'create' in actions and len(templates) > 1:
            raise ChannelReviewRequired('shopify_catalog_aggregation_needs_review')
        if 'create' in actions:
            pending = [variant for variant in variants if plan[variant['id']]['action'] == 'create']
            self._check_catalog_structure(pending, templates)
            return ('different' if templates else 'new'), plan
        return ('link' if 'link' in actions else 'linked'), plan

    def _check_catalog_variant(self, product, variant):
        inventory = variant.get('inventoryItem') or {}
        self._check_shopify_product(product, {'requiresShipping': bool(inventory.get('requiresShipping'))})

    def _apply_shopify_catalog_product(self, item):
        self.ensure_one()
        self.env.cr.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))',
                            [f'qing-shopify-catalog:{self.id}'])
        locked, = self.env.cr.fetchone()
        if not locked:
            raise DeferredChannelError('shopify_product_mapping_in_progress', retry_after=10)
        snapshot = item.remote_snapshot
        if item.state == 'archived':
            self._withdraw_catalog_variants(item, [])
            return
        run = item.run_id
        _state, plan = self._plan_shopify_catalog_product(snapshot, catalog_run=run if run.state == 'applying' else None)
        if item.plan != plan:
            item.write({'state': 'review', 'issue': 'shopify_catalog_preview_changed', 'plan': plan})
            return
        target = self.env['product.product'].browse([line['product'] for line in plan.values() if line.get('product')])
        to_create = [variant for variant in snapshot['variants'] if plan[variant['id']]['action'] == 'create']
        template = target.product_tmpl_id
        if (to_create and not template and run.state == 'applying' and run.product_defaults
                and run.product_defaults != self._catalog_new_product_defaults()):
            raise ChannelReviewRequired('shopify_catalog_product_defaults_changed')
        created = self._create_catalog_variants(snapshot, to_create, template) if to_create else {}
        Binding = self.env['qing.channel.binding']
        applied_ids = set()
        price_issues = set()
        pricelist = self._catalog_price_context() if any('price' in variant for variant in snapshot['variants']) else False
        for variant in snapshot['variants']:
            gid = variant['id']
            entry = plan[gid]
            if entry['action'] == 'skip':
                continue
            product = created.get(gid) or self.env['product.product'].browse(entry.get('product'))
            if entry['action'] != 'keep':
                Binding.create({
                    'store_id': self.id, 'kind': 'variant', 'external_id': gid,
                    'product_id': product.id, 'shopify_catalog_item_id': item.id,
                })
            else:
                binding = Binding.search([('store_id', '=', self.id), ('kind', '=', 'variant'),
                                          ('external_id', '=', gid)], limit=1)
                binding.write({'shopify_catalog_item_id': item.id, 'shopify_catalog_withdrawn': False})
            self._map_shopify_inventory_item(product, variant.get('inventoryItem') or {})
            price_issue = self._catalog_seed_variant_price(item, product, variant, pricelist)
            if price_issue:
                price_issues.add(price_issue)
            applied_ids.add(gid)
        self._withdraw_catalog_variants(item, list(applied_ids))
        item.write({'state': 'linked', 'last_synced_at': fields.Datetime.now(),
                    'issue': ', '.join(sorted(price_issues)) or False})

    def _withdraw_catalog_variants(self, item, present):
        Binding = self.env['qing.channel.binding']
        missing = Binding.search([
            ('store_id', '=', self.id), ('kind', '=', 'variant'), ('shopify_catalog_item_id', '=', item.id),
            ('external_id', 'not in', present), ('shopify_catalog_withdrawn', '=', False),
        ])
        if missing:
            inventory = self._shopify_inventory_bindings().filtered(lambda binding: binding.product_id in missing.product_id)
            inventory.write({'stock_paused': True, 'shopify_takeover_pending': True})
            missing.write({'shopify_catalog_withdrawn': True})

    def _catalog_variant_options(self, variant):
        options = variant.get('selectedOptions') or []
        if options == [{'name': 'Title', 'value': 'Default Title'}]:
            return {}
        values = {entry['name']: entry['value'] for entry in options}
        if len(values) != len(options) or len(values) > 3 or not all(values) or not all(values.values()):
            raise ChannelReviewRequired('shopify_catalog_variant_options_invalid')
        return values

    def _check_catalog_structure(self, variants, template):
        variants_options = [self._catalog_variant_options(variant) for variant in variants]
        names = set(variants_options[0])
        combinations = {tuple(sorted(options.items())) for options in variants_options}
        if any(set(options) != names for options in variants_options) or len(combinations) != len(variants):
            raise ChannelReviewRequired('shopify_catalog_variant_options_invalid')
        if not template:
            kinds = {bool((variant.get('inventoryItem') or {}).get('requiresShipping')) for variant in variants}
            if len(kinds) != 1:
                raise ChannelReviewRequired('shopify_catalog_mixed_product_types')
            return variants_options
        lines = template.attribute_line_ids
        if not names or set(lines.attribute_id.mapped('name')) != names or len(lines) != len(names):
            raise ChannelReviewRequired('shopify_catalog_variant_structure_requires_review')
        if any(line.attribute_id.create_variant == 'no_variant' for line in lines):
            raise ChannelReviewRequired('shopify_catalog_variant_structure_requires_review')
        ProductValue = self.env['product.template.attribute.value']
        for options in variants_options:
            combination = ProductValue.browse()
            for line in lines:
                selection = line.product_template_value_ids.filtered(
                    lambda value: value.ptav_active and value.name == options[line.attribute_id.name])
                if len(selection) > 1:
                    raise ChannelReviewRequired('shopify_catalog_variant_options_invalid')
                combination |= selection
            if len(combination) == len(lines) and template._is_combination_possible(combination):
                continue
            if len(combination) == len(lines) or not template.has_dynamic_attributes():
                raise ChannelReviewRequired('shopify_catalog_variant_structure_requires_review')
        return variants_options

    def _create_catalog_variants(self, snapshot, variants, template):
        variants_options = self._check_catalog_structure(variants, template)
        product_type = 'consu' if (variants[0].get('inventoryItem') or {}).get('requiresShipping') else 'service'
        if not template:
            template = self._create_catalog_template(snapshot, variants_options, product_type)
        ProductValue = self.env['product.template.attribute.value']
        products = {}
        Binding = self.env['qing.channel.binding']
        for variant, options in zip(variants, variants_options, strict=True):
            if options:
                combination = ProductValue.browse()
                for line in template.attribute_line_ids:
                    value_name = options[line.attribute_id.name]
                    selected = line.product_template_value_ids.filtered(
                        lambda entry: entry.ptav_active and entry.name == value_name)
                    if not selected:
                        selection = line.attribute_id.value_ids.filtered(lambda value: value.name == value_name)
                        if len(selection) > 1:
                            raise ChannelReviewRequired('shopify_catalog_variant_options_invalid')
                        if not selection:
                            selection = self.env['product.attribute.value'].create({
                                'attribute_id': line.attribute_id.id, 'name': value_name,
                            })
                        line.with_context(create_product_product=False).write({'value_ids': [Command.link(selection.id)]})
                        selected = line.product_template_value_ids.filtered(
                            lambda entry: entry.ptav_active and entry.product_attribute_value_id == selection)
                    combination |= selected
                product = template._get_variant_for_combination(combination)
                existed = bool(product)
                if not product:
                    product = template._create_product_variant(combination)
            else:
                product = template.product_variant_id
                existed = False
            if not product or product in products.values():
                raise ChannelReviewRequired('shopify_catalog_variant_structure_requires_review')
            if Binding.search_count([
                ('store_id', '=', self.id), ('kind', '=', 'variant'), ('product_id', '=', product.id),
            ], limit=1):
                raise ChannelReviewRequired('shopify_product_matches_multiple_variants')
            self._check_catalog_variant(product, variant)
            if not existed:
                self._set_catalog_variant_codes(product, variant)
            products[variant['id']] = product
        return products

    def _catalog_new_product_defaults(self):
        company = self.company_id
        template = self.env['product.template'].with_company(company)
        return {
            'categ_id': self.shopify_catalog_category_id.id,
            'uom_id': template.default_get(['uom_id'])['uom_id'],
            'invoice_policy': 'order' if self.shopify_invoice_policy == 'capture' else 'delivery',
            'purchase_ok': self.shopify_catalog_purchase_ok,
            'sale_tax_ids': company.account_sale_tax_id.ids,
            'purchase_tax_ids': company.account_purchase_tax_id.ids,
        }

    def _create_catalog_template(self, snapshot, variants_options, product_type):
        lines = []
        Attribute = self.env['product.attribute']
        Value = self.env['product.attribute.value']
        for name in variants_options[0]:
            attribute = Attribute.create({'name': name, 'create_variant': 'dynamic'})
            names = dict.fromkeys(options[name] for options in variants_options)
            values = Value.create([{'attribute_id': attribute.id, 'name': value} for value in names])
            lines.append(Command.create({'attribute_id': attribute.id, 'value_ids': [Command.set(values.ids)]}))
        defaults = self._catalog_new_product_defaults()
        return self.env['product.template'].with_context(create_product_product=not lines).create({
            'company_id': self.company_id.id, 'name': snapshot['title'],
            'type': product_type, 'is_storable': product_type == 'consu',
            'sale_ok': True, 'list_price': 0, 'attribute_line_ids': lines,
            'supplier_taxes_id': [Command.set(defaults.pop('purchase_tax_ids'))],
            'taxes_id': [Command.set(defaults.pop('sale_tax_ids'))],
            **defaults,
        })

    def _set_catalog_variant_codes(self, product, variant):
        sku = (variant['sku'] or '').strip()
        code = sku or f"SHOPIFY-{self.id}-{variant['id'].rsplit('/', 1)[-1]}"
        barcodes = variant['barcodes']['nodes']
        candidates = self._catalog_candidates({
            variant['id']: (normalize_catalog_code(code), {normalize_catalog_code(x['value']) for x in barcodes} - {''}),
        })
        if candidates - product:
            raise ChannelReviewRequired('shopify_product_codes_ambiguous')
        product.write({'default_code': code, 'barcode': barcodes[0]['value'] if barcodes else False})

    shopify_catalog_category_id = fields.Many2one(
        'product.category', string='Imported Product Category', default=lambda self: self.env.ref('product.product_category_goods'),
    )
    shopify_catalog_imported_at = fields.Datetime(readonly=True, copy=False)
    shopify_catalog_purchase_ok = fields.Boolean(string='Imported Products Can Be Purchased')

    def action_shopify_preview_catalog(self):
        self.ensure_one()
        self._check_channel_manager()
        if self.platform != 'shopify' or not self.active:
            raise UserError(_('Choose an active Shopify store.'))
        if not self.shopify_catalog_category_id:
            raise UserError(_('Choose a category for new catalog products.'))
        self.lock_for_update(allow_referencing=True)
        Run = self.env['qing.shopify.catalog.run'].sudo()
        run = Run.search([
            ('store_id', '=', self.id), ('state', 'in', ('scanning', 'preview', 'applying', 'review')),
        ], limit=1)
        if not run:
            run = Run.create({'store_id': self.id})
            self.env.ref('qing_shopify.cron_shopify_catalog').sudo()._trigger()
        return run._open_catalog_run()
