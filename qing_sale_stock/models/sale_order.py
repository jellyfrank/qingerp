from odoo import api, fields, models


DELIVERY_STATES = [
    ('no', 'Not Delivered'),
    ('partial', 'Partially Delivered'),
    ('delivered', 'Delivered'),
    ('partial_return', 'Partially Returned'),
    ('returned', 'Returned'),
]


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.depends(
        'state',
        'order_line.product_id',
        'order_line.product_uom_qty',
        'order_line.product_uom_id',
        'order_line.move_ids.state',
        'order_line.move_ids.quantity',
        'order_line.move_ids.uom_id',
        'order_line.move_ids.location_id.usage',
        'order_line.move_ids.location_dest_id.usage',
        'order_line.move_ids.origin_returned_move_id',
    )
    def _compute_qing_delivery_status(self):
        for order in self:
            lines = order.order_line.filtered(
                lambda line: line.product_id.type == 'consu' and line.product_uom_qty > 0
            )
            if order.state != 'sale' or not lines:
                order.qing_delivery_status = False
                continue

            any_delivery = False
            any_return = False
            fully_delivered = True
            fully_returned = True
            for line in lines:
                delivered_qty = returned_qty = 0.0
                for move in line.move_ids:
                    if move.state != 'done':
                        continue
                    quantity = move.uom_id._compute_quantity(
                        move.quantity, line.product_uom_id, rounding_method='HALF-UP'
                    )
                    if move.origin_returned_move_id and move.location_id.usage == 'customer':
                        returned_qty += quantity
                    elif not move.origin_returned_move_id and move.location_dest_id.usage == 'customer':
                        delivered_qty += quantity

                uom = line.product_uom_id
                any_delivery |= uom.compare(delivered_qty, 0) > 0
                any_return |= uom.compare(returned_qty, 0) > 0
                fully_delivered &= uom.compare(delivered_qty, line.product_uom_qty) >= 0
                fully_returned &= (
                    uom.compare(delivered_qty, 0) > 0
                    and uom.compare(returned_qty, delivered_qty) >= 0
                )

            if any_return:
                order.qing_delivery_status = 'returned' if fully_returned else 'partial_return'
            elif fully_delivered:
                order.qing_delivery_status = 'delivered'
            elif any_delivery:
                order.qing_delivery_status = 'partial'
            else:
                order.qing_delivery_status = 'no'

    qing_delivery_photo_ids = fields.One2many(
        'qing.sale.delivery.photo', 'order_id', string='Delivery Photos', copy=False,
    )
    qing_delivery_status = fields.Selection(
        DELIVERY_STATES,
        string='Delivery Status',
        compute='_compute_qing_delivery_status',
        compute_sudo=True,
    )
