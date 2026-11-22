from decimal import Decimal, InvalidOperation
from uuid import uuid4

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.addons.qing_channel.errors import ChannelReviewRequired, DeferredChannelError

from ..services.catalog import CATALOG_RECHECK_TOPIC, fetch_product, variant_code_keys


CATALOG_STATES = [
    ('new', 'To Create'), ('link', 'To Link'), ('different', 'Differences'),
    ('linked', 'Linked'), ('review', 'Needs Review'), ('archived', 'Archived'), ('skipped', 'Skipped'),
]


class ShopifyCatalogItem(models.Model):
    _name = 'qing.shopify.catalog.item'
    _description = 'Shopify Channel Product'
    _check_company_auto = True
    _order = 'id desc'

    @api.depends('remote_snapshot')
    def _compute_variant_count(self):
        for item in self:
            item.variant_count = len((item.remote_snapshot or {}).get('variants', ()))

    @api.depends('remote_snapshot', 'run_id.shop_currency', 'store_id.shopify_currency_id.name')
    def _compute_shop_price(self):
        for item in self:
            prices = []
            for variant in (item.remote_snapshot or {}).get('variants', ()):
                raw_price = variant.get('price')
                if raw_price is None:
                    continue
                try:
                    price = Decimal(str(raw_price))
                except InvalidOperation:
                    continue
                if price.is_finite() and price >= 0:
                    prices.append(price)
            if not prices:
                item.shop_price = False
                continue
            low, high = min(prices), max(prices)
            amount = format(low, 'f') if low == high else f'{low:f}–{high:f}'
            currency = item.run_id.shop_currency or item.store_id.shopify_currency_id.name
            item.shop_price = f'{amount} {currency}' if currency else amount

    @api.depends('remote_snapshot', 'remote_status')
    def _compute_catalog_code_keys(self):
        for item in self:
            snapshot = item.remote_snapshot or {}
            if item.remote_status in ('ARCHIVED', 'DELETED') or snapshot.get('isGiftCard'):
                item.catalog_code_keys = []
            else:
                item.catalog_code_keys = sorted({
                    key for variant in snapshot.get('variants', []) if not variant.get('requiresComponents')
                    for key in variant_code_keys(variant)
                })

    @api.depends('remote_snapshot', 'plan')
    def _compute_variant_preview(self):
        labels = {'create': _('To Create'), 'link': _('To Link'), 'keep': _('Linked'),
                  'skip': _('Skipped')}
        for item in self:
            variants = (item.remote_snapshot or {}).get('variants', ())
            plan = item.plan or {}
            products = {product.id: product.display_name for product in self.env['product.product'].sudo().browse([
                line['product'] for line in plan.values()
                if isinstance(line, dict) and line.get('product')
            ]).exists()}
            rows = []
            for variant in variants:
                details = plan.get(variant['id'], {})
                options = ' / '.join(val['value'] for val in variant.get('selectedOptions', []))
                description = options or variant.get('title') or _('Default Variant')
                rows.append(Markup('<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>').format(
                    description, variant.get('sku') or '', variant.get('price') or '',
                    labels.get(details.get('action'), _('Needs Review')),
                    products.get(details.get('product'), ''),
                ))
            header = Markup('<tr><th>{}</th><th>SKU</th><th>{}</th><th>{}</th><th>{}</th></tr>').format(
                _('Variant'), _('Shopify Price'), _('Preview Result'), _('ERP Product'),
            )
            item.variant_preview = Markup('<table class="table"><thead>{}</thead><tbody>{}</tbody></table>').format(
                header, Markup('').join(rows),
            ) if rows else False

    @api.model
    def _record_catalog_preview(self, run, external_id, snapshot):
        item = run.store_id._find_catalog_snapshot_item(external_id, snapshot)
        if not snapshot:
            if item:
                item.write({'run_id': run.id, 'state': 'archived', 'remote_status': 'DELETED',
                            'issue': False, 'apply_pending': False})
            return item
        values = {
            'name': snapshot['title'], 'run_id': run.id, 'remote_snapshot': snapshot,
            'remote_status': snapshot['status'], 'issue': False, 'apply_pending': False,
        }
        if not item:
            item = self.create(dict(values, store_id=run.store_id.id, external_id=external_id))
        else:
            item.write(values)
        item._refresh_catalog_plan()
        return item

    def _refresh_catalog_plan(self):
        self.ensure_one()
        try:
            run = self.run_id
            state, plan = self.store_id._plan_shopify_catalog_product(
                self.remote_snapshot, catalog_run=run if run.state == 'preview' else None,
                check_catalog_codes=run.state != 'scanning',
            )
            self.write({'state': state, 'plan': plan, 'issue': False})
        except ChannelReviewRequired as exc:
            self.write({'state': 'review', 'plan': {}, 'issue': exc.code})

    apply_pending = fields.Boolean(readonly=True, copy=False)
    catalog_code_keys = fields.Json(compute='_compute_catalog_code_keys', store=True, readonly=True)
    company_id = fields.Many2one(related='store_id.company_id', store=True, index=True, precompute=True)
    external_id = fields.Char(copy=False, readonly=True)
    issue = fields.Char(copy=False, readonly=True)
    last_synced_at = fields.Datetime(copy=False, readonly=True)
    name = fields.Char(required=True, readonly=True)
    plan = fields.Json(copy=False, readonly=True)
    remote_snapshot = fields.Json(copy=False, readonly=True)
    remote_status = fields.Char(copy=False, readonly=True)
    run_id = fields.Many2one('qing.shopify.catalog.run', check_company=True, ondelete='set null', readonly=True, index=True)
    shop_price = fields.Char(string='Shopify Store Price', compute='_compute_shop_price')
    state = fields.Selection(CATALOG_STATES, default='new', required=True, readonly=True, index=True)
    store_id = fields.Many2one('qing.channel.store', required=True, ondelete='restrict', index=True, readonly=True)
    variant_count = fields.Integer(compute='_compute_variant_count')
    variant_preview = fields.Html(compute='_compute_variant_preview', string='Catalog Variant Details')

    _remote_unique = models.UniqueIndex('(store_id, external_id) WHERE external_id IS NOT NULL')
    _catalog_codes_index = models.Index('USING gin (catalog_code_keys)')

    def action_catalog_recheck(self):
        self.ensure_one()
        self.check_access('read')
        self.store_id._check_channel_manager()
        item = self.sudo().with_company(self.company_id)
        store = item.store_id
        run = item.run_id
        if not item.external_id or not store.active or store.shopify_health != 'connected':
            raise UserError(_('Connect the Shopify store before rechecking this product.'))
        if run.state == 'preview':
            run.lock_for_update(allow_referencing=True)
            try:
                snapshot = fetch_product(store._shopify_client(), item.external_id)
                item._record_catalog_preview(run, item.external_id, snapshot)
            except DeferredChannelError as exc:
                raise UserError(_('Cannot recheck the Shopify product (%s).', exc.code)) from None
            return True
        if (run and run.state not in ('done', 'cancelled')) or not store.shopify_catalog_imported_at:
            raise UserError(_('Wait for the current Shopify catalog job before rechecking this product.'))

        Delivery = self.env['qing.channel.delivery'].sudo()
        if not Delivery.search_count([
            ('store_id', '=', store.id), ('external_id', '=', item.external_id),
            ('topic', '=', CATALOG_RECHECK_TOPIC), ('state', 'in', ('pending', 'retry')),
        ], limit=1):
            Delivery._receive(store, f'catalog-recheck:{uuid4().hex}', item.external_id, CATALOG_RECHECK_TOPIC)
        self.env.ref('qing_channel.cron_channel_inbox').sudo()._trigger()
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'message': _('Shopify product recheck queued.'), 'type': 'info'},
        }
