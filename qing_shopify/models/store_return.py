from odoo import models
from odoo.addons.qing_channel.errors import ChannelReviewRequired

from ..services.orders import nodes


class ShopifyStoreReturn(models.Model):
    _inherit = 'qing.channel.store'

    def _execute_channel_action(self, action):
        if self.platform != 'shopify' or action.kind != 'return':
            return super()._execute_channel_action(action)
        if not action.order_id or action.snapshot_version != action.order_id.snapshot_version:
            return 'obsolete'
        refund = self.env['qing.channel.refund'].browse(action.data['refund']).exists()
        if not refund or refund.order_id != action.order_id:
            return 'obsolete'
        self._prepare_shopify_return(refund)

    def _prepare_shopify_return(self, refund):
        if refund.return_picking_ids:
            return
        lines_by_key = {line.external_id: line.sale_line_id for line in refund.order_id.line_ids}
        moves_by_picking = {}
        for detail in nodes(refund.snapshot['refundLineItems']):
            if detail['restockType'] != 'RETURN':
                continue
            line = lines_by_key.get(detail['lineItem']['id'])
            if not line:
                raise ChannelReviewRequired('shopify_return_sale_line_missing')
            outgoing = line.move_ids.filtered(
                lambda move: move.state == 'done' and move.location_dest_id.usage == 'customer'
            ).sorted(lambda move: (move.date, move.id))
            remaining = detail['quantity']
            for move in outgoing:
                product_uom = move.product_id.uom_id
                shipped = move.uom_id._compute_quantity(move.quantity, product_uom, round=False)
                returned = sum(
                    returned_move.uom_id._compute_quantity(
                        returned_move.quantity if returned_move.state == 'done' else returned_move.product_uom_qty,
                        product_uom, round=False)
                    for returned_move in move.returned_move_ids if returned_move.state != 'cancel'
                )
                qty = min(max(0, shipped - returned), remaining)
                if qty:
                    moves_by_picking.setdefault(move.picking_id, {})[move.id] = qty
                    remaining -= qty
                if not remaining:
                    break
            if remaining:
                raise ChannelReviewRequired('shopify_return_exceeds_delivered_quantity')
        for original, quantities in moves_by_picking.items():
            picking = original._create_return()
            picking.move_ids.filtered(lambda move: move.origin_returned_move_id.id not in quantities).unlink()
            for move in picking.move_ids:
                move.product_uom_qty = move.product_id.uom_id._compute_quantity(
                    quantities[move.origin_returned_move_id.id], move.uom_id)
            picking.shopify_refund_id = refund
