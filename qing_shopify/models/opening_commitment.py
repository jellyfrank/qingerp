from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ShopifyOpeningCommitment(models.Model):
    _name = 'qing.shopify.opening.commitment'
    _description = 'Shopify Opening Inventory Commitment'
    _check_company_auto = True

    @api.constrains('quantity', 'sale_line_id', 'product_id', 'store_id')
    def _check_commitment(self):
        for record in self:
            if record.quantity < 0:
                raise ValidationError(_('An open fulfilment quantity cannot be negative.'))
            if record.sale_line_id and record.sale_line_id.product_id != record.product_id:
                raise ValidationError(_('The linked sales order line has a different product.'))
            if record.sale_line_id and record.sale_line_id.order_id.warehouse_id != record.store_id.warehouse_id:
                raise ValidationError(_('The linked sales order must use the Shopify cutover warehouse.'))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._invalidate_open_order_review(records.store_id)
        records._apply_matches_to_imported_orders()
        return records

    def write(self, vals):
        stores = self.store_id
        match_fields = {'store_id', 'external_line_id', 'sale_line_id'}
        previous_matches = []
        if match_fields & vals.keys():
            for entry in self.filtered('sale_line_id'):
                if (vals.get('store_id', entry.store_id.id) != entry.store_id.id
                        or vals.get('external_line_id', entry.external_line_id) != entry.external_line_id
                        or vals.get('sale_line_id', entry.sale_line_id.id) != entry.sale_line_id.id):
                    previous_matches.append((entry, entry._find_imported_order_line(), entry.sale_line_id))
        result = super().write(vals)
        if vals:
            self._invalidate_open_order_review(stores | self.store_id)
        for entry, line, sale_line in previous_matches:
            entry._detach_imported_sale_line(line, sale_line)
        if (match_fields | {'product_id', 'external_order'}) & vals.keys():
            self._apply_matches_to_imported_orders()
        return result

    def _find_imported_order_line(self):
        self.ensure_one()
        lines = self.env['qing.channel.order.line'].search([
            ('order_id.store_id', '=', self.store_id.id), ('external_id', '=', self.external_line_id),
        ], limit=2)
        if len(lines) > 1:
            raise ValidationError(_('This Shopify line number occurs on more than one imported order.'))
        return lines

    def _apply_matches_to_imported_orders(self):
        for entry in self:
            line = entry._find_imported_order_line()
            if not line:
                continue
            line.order_id.lock_for_update(allow_referencing=True)
            entry._attach_imported_sale_line(line)

    def _check_imported_order_line(self, line):
        self.ensure_one()
        order = line.order_id
        if self.external_order not in (order.external_id, order.external_name) or not order.historical:
            raise ValidationError(_('The opening commitment does not identify the imported Shopify order before cutover.'))
        if line.product_id != self.product_id:
            raise ValidationError(_('The imported Shopify order line has a different product.'))

    def _attach_imported_sale_line(self, line):
        self._check_imported_order_line(line)
        if not self.sale_line_id or line.sale_line_id == self.sale_line_id:
            return
        if line.sale_line_id or self.env['qing.channel.order.line'].search_count([
            ('sale_line_id', '=', self.sale_line_id.id), ('order_id.store_id.platform', '=', 'shopify'),
        ], limit=1):
            raise ValidationError(_('The ERP sales line or the Shopify order line already has another match.'))
        line.sudo().sale_line_id = self.sale_line_id

    def _detach_imported_sale_line(self, line, sale_line):
        if not line or not sale_line:
            return
        order = line.order_id
        order.lock_for_update(allow_referencing=True)
        if line.sale_line_id != sale_line or sale_line.shopify_line_key == line.external_id:
            return
        if order.sale_id or order.shopify_takeover_requested or order.shopify_finance_requested:
            raise ValidationError(_('This Shopify order already has an ERP sale or a pending takeover. Review the match manually.'))
        line.sudo().sale_line_id = False

    def _invalidate_open_order_review(self, stores):
        stores.lock_for_update(allow_referencing=True)
        if stores.filtered('onboarded'):
            raise ValidationError(_('Pause Shopify inventory publishing before changing opening commitments.'))
        stores.filtered('shopify_open_orders_verified').sudo().write({'shopify_open_orders_verified': False})

    company_id = fields.Many2one(related='store_id.company_id', store=True, index=True, precompute=True)
    external_line_id = fields.Char(required=True, index=True)
    external_order = fields.Char(required=True)
    product_id = fields.Many2one('product.product', required=True, check_company=True, ondelete='restrict')
    quantity = fields.Integer(required=True)
    sale_line_id = fields.Many2one('sale.order.line', check_company=True, ondelete='restrict')
    store_id = fields.Many2one('qing.channel.store', required=True, ondelete='restrict', index=True)

    _external_unique = models.UniqueIndex('(store_id, external_line_id)')
    _sale_unique = models.UniqueIndex('(sale_line_id)')

    @api.ondelete(at_uninstall=False)
    def _unlink_when_inventory_paused(self):
        self._invalidate_open_order_review(self.store_id)
        for entry in self.filtered('sale_line_id'):
            entry._detach_imported_sale_line(entry._find_imported_order_line(), entry.sale_line_id)
