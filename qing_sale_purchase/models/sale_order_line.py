from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import SQL


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _qing_purchase_quantities(self):
        """Read quantities only across purchase rules; never expose costs or PO records."""
        result = dict.fromkeys(self.ids, 0.0)
        PurchaseLine = self.env['purchase.order.line'].sudo()
        domain = [('sale_line_id', 'in', self.ids), ('order_id.state', '!=', 'cancel')]
        groups = PurchaseLine._read_group(domain, ['sale_line_id', 'product_id', 'uom_id'], ['product_qty:sum'])
        for sale_line, product, unit, quantity in groups:
            if product == sale_line.product_id:
                result[sale_line.id] += unit._compute_quantity(
                    quantity, sale_line.product_uom_id, round=False,
                )
        return result

    @api.depends('product_id', 'product_uom_qty', 'product_uom_id', 'purchase_line_ids.product_qty',
                 'purchase_line_ids.uom_id', 'purchase_line_ids.product_id', 'purchase_line_ids.order_id.state')
    def _compute_qing_purchase_quantities(self):
        quantities = self._qing_purchase_quantities()
        for line in self:
            arranged = quantities.get(line.id, 0.0)
            line.qing_purchase_qty = arranged
            line.qing_purchase_remaining_qty = max(line.product_uom_qty - arranged, 0.0)
            line.qing_purchase_excess_qty = max(arranged - line.product_uom_qty, 0.0)

    def _qing_check_manual_purchase(self):
        """Reject automatic procurement by inspecting effective stock rules when installed."""
        checked_routes = {}
        for line in self:
            if (line.state not in line.order_id._qing_purchase_allowed_states() or line.display_type or line.is_downpayment
                    or line.product_id.type != 'consu' or not line.product_id.purchase_ok
                    or line.product_uom_qty <= 0):
                raise UserError(_('Only positive, purchasable goods on confirmed sales orders can be purchased manually.'))
            if 'move_ids' not in line._fields:
                continue
            warehouse = line.order_id.warehouse_id
            key = (line.product_id.id, warehouse.id, tuple(line.route_ids.ids),
                   line.order_id.partner_shipping_id.property_stock_customer.id)
            automatic = checked_routes.get(key)
            if automatic is None:
                Rule = self.env['stock.rule'].with_company(line.company_id)
                location = line.order_id.partner_shipping_id.property_stock_customer
                values = {'warehouse_id': warehouse, 'route_ids': line.route_ids}
                seen = Rule
                automatic = False
                while location:
                    rule = Rule._get_rule(line.product_id, location, values)
                    if not rule or rule in seen:
                        break
                    seen |= rule
                    if rule.procure_method == 'make_to_order' or rule.action in ('buy', 'manufacture'):
                        automatic = True
                        break
                    if rule.procure_method == 'make_to_stock' or rule.action not in ('pull', 'pull_push'):
                        break
                    location = rule.location_src_id
                checked_routes[key] = automatic
            if automatic:
                raise UserError(_('This sales line uses automatic procurement. Use its existing supply workflow: %s', line.display_name))
            if any(move.procure_method == 'make_to_order' for move in line.move_ids if move.state != 'cancel'):
                raise UserError(_('An automatic supply chain already exists for %s.', line.display_name))

    def _qing_can_change_purchased_product(self):
        self.ensure_one()
        return False

    def _qing_lock_purchase(self):
        """Serialize allocation, including transactions with a repeatable-read snapshot."""
        # Private primitive: callers enforce access on the SO or the edited PO.
        # Purchase-only users may update a linked PO without sales read access.
        self.flush_recordset()
        self.sorted('id').lock_for_update()
        # A real row update ensures a stale snapshot raises SerializationFailure.
        # Only this technical revision is elevated; no business document is sudo-created.
        if self.ids:
            self.env.cr.execute(SQL(
                "UPDATE sale_order_line SET qing_purchase_revision = COALESCE(qing_purchase_revision, 0) + 1 WHERE id IN %s",
                tuple(self.ids),
            ))
            self.invalidate_recordset(['qing_purchase_revision'])
        self.invalidate_recordset(['purchase_line_ids', 'qing_purchase_qty',
                                   'qing_purchase_remaining_qty', 'qing_purchase_excess_qty'])

    qing_purchase_excess_qty = fields.Float(
        string='Excess Purchase', compute='_compute_qing_purchase_quantities', digits='Product Unit')
    qing_purchase_qty = fields.Float(
        string='Arranged Purchase', compute='_compute_qing_purchase_quantities', digits='Product Unit')
    qing_purchase_remaining_qty = fields.Float(
        string='Unarranged Purchase', compute='_compute_qing_purchase_quantities', digits='Product Unit')
    qing_purchase_revision = fields.Integer(copy=False, groups='base.group_system')

    def write(self, vals):
        watched = {'product_id', 'product_uom_id', 'product_uom_qty'} & vals.keys()
        quantities = self._qing_purchase_quantities() if watched else {}
        if {'product_id', 'product_uom_id'} & vals.keys():
            for line in self:
                if quantities.get(line.id) and not line._qing_can_change_purchased_product() and any(
                    key in vals and vals[key] != line[key].id
                    for key in ('product_id', 'product_uom_id')
                ):
                    raise UserError(_('Cancel the linked purchases before changing the product or unit.'))
        result = super().write(vals)
        if 'product_uom_qty' in vals:
            reduced = self.filtered(lambda line: line.product_uom_id.compare(
                quantities.get(line.id, 0), line.product_uom_qty) > 0)
            reduced.order_id._qing_notify_purchase_change()
        return result
