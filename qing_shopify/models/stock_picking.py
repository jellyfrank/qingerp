from odoo import _, fields, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _action_done(self):
        self._check_shopify_outbound_capacity()
        return super()._action_done()

    def _check_shopify_outbound_capacity(self):
        moves = self.move_ids.filtered(
            lambda move: move.picked and move.state not in ('done', 'cancel')
            and move.sale_line_id and move.location_dest_usage == 'customer'
        )
        if not moves:
            return
        lines = self.env['qing.channel.order.line'].sudo().search([
            ('sale_line_id', 'in', moves.sale_line_id.ids), ('company_id', 'in', self.company_id.ids),
            ('order_id.store_id.platform', '=', 'shopify'),
        ])
        if not lines:
            return
        picked_by_line = moves.grouped(lambda move: move.sale_line_id.id)
        orders = lines.order_id
        orders.lock_for_update(allow_referencing=True)
        for order in orders:
            _transactions, void_requires_review = order.store_id._shopify_confirmation_transactions(order)
            if void_requires_review:
                raise UserError(_(
                    "The Shopify payment authorization was voided. Verify payment before validating this shipment.",
                ))
        cancellations = {order.id: order.store_id._shopify_cancelled_refund_quantities(order) for order in orders}
        for line in lines:
            order = line.order_id
            maximum = order.store_id._shopify_fulfillable_after_cancellation(
                line, cancellations[order.id][line.external_id],
            )
            if maximum is None:
                continue
            unit = line.sale_line_id.product_id.uom_id
            picked = sum(
                move.uom_id._compute_quantity(move._get_picked_quantity(), unit, round=False)
                for move in picked_by_line[line.sale_line_id.id]
            )
            if unit.compare(picked, maximum) > 0:
                raise UserError(_(
                    "Shopify canceled some items on this order. Reconcile the shipment quantities before validating.",
                ))

    shopify_refund_id = fields.Many2one('qing.channel.refund', check_company=True, ondelete='restrict', copy=False)
