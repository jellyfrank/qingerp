from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


INVENTORY_IDENTIFIERS = {'kind', 'store_id', 'product_id', 'location_external_id', 'external_id'}
BASELINE_FIELDS = {
    'last_quantity', 'quantity_checked_at', 'quantity_version', 'shopify_confirmed_book', 'stock_hold_reason',
    'shopify_preview_at', 'shopify_preview_available', 'shopify_preview_target', 'shopify_preview_book',
}


class ShopifyBinding(models.Model):
    _inherit = 'qing.channel.binding'

    def _confirm_shopify_quantity(self, quantity, book):
        self.ensure_one()
        self.write({'last_quantity': quantity, 'shopify_confirmed_book': book,
                    'quantity_checked_at': fields.Datetime.now(), 'quantity_version': self.quantity_version + 1})

    @api.model_create_multi
    def create(self, vals_list):
        default_vals = self.default_get(['kind', 'store_id', *BASELINE_FIELDS])
        vals_list = [dict(default_vals, **vals) for vals in vals_list]
        if not self.env.su and any(BASELINE_FIELDS & vals.keys() for vals in vals_list):
            raise AccessError(_('Use the inventory cutover actions to set the Shopify comparison baseline.'))
        stores = self.env['qing.channel.store'].browse([
            vals['store_id'] for vals in vals_list if vals.get('kind') == 'inventory' and vals.get('store_id')
        ])
        stores.check_access('read')
        self._check_shopify_paused(stores)
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and BASELINE_FIELDS & vals.keys():
            raise AccessError(_('Use the inventory cutover actions to set the Shopify comparison baseline.'))
        if INVENTORY_IDENTIFIERS & vals.keys():
            old = self.filtered(lambda binding: binding.kind == 'inventory')
            stores = old.store_id
            if vals.get('kind') == 'inventory':
                stores |= self.store_id
            if vals.get('store_id') and (old or vals.get('kind') == 'inventory'):
                stores |= self.env['qing.channel.store'].browse(vals['store_id'])
            self._check_shopify_paused(stores)
            self._check_stock_requests(old)
            vals = dict(vals, shopify_preview_at=False)
        if 'stock_paused' in vals and not vals['stock_paused']:
            self._check_shopify_paused(self.filtered('stock_paused').store_id)
            vals = dict(vals, shopify_preview_at=False)
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _check_inventory_binding_removal(self):
        bindings = self.filtered(lambda binding: binding.kind == 'inventory' and binding.store_id.platform == 'shopify')
        self._check_shopify_paused(bindings.store_id)
        self._check_stock_requests(bindings)

    def _check_shopify_paused(self, stores):
        shops = stores.filtered(lambda store: store.platform == 'shopify')
        if shops:
            shops.lock_for_update(allow_referencing=True)
        if shops.filtered('onboarded'):
            raise UserError(_('Pause Shopify inventory publishing before changing inventory mappings.'))

    def _check_stock_requests(self, bindings):
        if bindings and self.env['qing.channel.request'].sudo().search_count([
            ('store_id', 'in', bindings.store_id.ids),
            ('resource', 'in', [f'stock:{binding.id}' for binding in bindings]),
            ('state', 'in', ('ready', 'uncertain', 'verify', 'review')),
        ], limit=1):
            raise UserError(_('Resolve the inventory request before changing its Shopify mapping.'))

    def action_shopify_activate_inventory(self):
        self.ensure_one()
        self.check_access('write')
        self.store_id._check_channel_manager()
        if self.store_id.platform != 'shopify':
            raise UserError(_('Choose a Shopify inventory mapping.'))
        self.store_id.sudo()._enqueue_shopify_inventory_activation(self.sudo())
        return True

    @api.depends('shopify_preview_target', 'shopify_preview_available')
    def _compute_shopify_preview_difference(self):
        for binding in self:
            binding.shopify_preview_difference = binding.shopify_preview_target - binding.shopify_preview_available

    shopify_confirmed_book = fields.Integer(copy=False, readonly=True)
    shopify_preview_at = fields.Datetime(copy=False, readonly=True)
    shopify_preview_available = fields.Integer(copy=False, readonly=True)
    shopify_preview_target = fields.Integer(copy=False, readonly=True)
    shopify_preview_book = fields.Integer(copy=False, readonly=True)
    shopify_preview_difference = fields.Integer(compute='_compute_shopify_preview_difference')
