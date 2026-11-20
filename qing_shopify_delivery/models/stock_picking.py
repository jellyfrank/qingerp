from odoo import api, models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _action_done(self):
        result = super()._action_done()
        self._qing_enqueue_shopify_shipment()
        return result

    def _qing_enqueue_shopify_shipment(self):
        eligible = self.filtered(lambda picking: picking.state == 'done' and picking.location_dest_id.usage == 'customer')
        for picking in eligible:
            line_ids = picking.move_ids.sale_line_id.ids
            if not line_ids:
                continue
            Line = self.env['qing.channel.order.line'].sudo()
            domain = [('sale_line_id', 'in', line_ids), ('order_id.store_id.platform', '=', 'shopify'),
                      ('company_id', '=', picking.company_id.id)]
            orders = Line.search(domain).order_id
            for order in orders:
                order.store_id._enqueue_shopify_shipment(order, picking.sudo())

    def write(self, vals):
        result = super().write(vals)
        if {'carrier_tracking_ref', 'carrier_id'} & vals.keys():
            self._qing_enqueue_shopify_shipment()
        return result
