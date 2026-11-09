import math

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    def _qing_check_capacity(self):
        lines = self.filtered(lambda line: line.qing_manual_purchase and line.state != 'cancel')
        quantities = lines.sale_line_id._qing_purchase_quantities()
        for line in lines:
            source = line.sale_line_id.sudo()
            if not source or source.company_id != line.company_id or source.product_id != line.product_id:
                raise UserError(_('The purchase line must match its source sales product and company.'))
            if not math.isfinite(line.product_qty) or line.product_qty <= 0:
                raise UserError(_('Purchase quantity must be positive.'))
            if source.product_uom_id.compare(quantities[source.id], source.product_uom_qty) <= 0:
                continue
            if not self.env.user.has_group('purchase.group_purchase_manager') or not (line.qing_overage_reason or '').strip():
                raise UserError(_('The arranged quantity exceeds the sales quantity. A purchase manager must enter an over-purchase reason.'))

    qing_manual_purchase = fields.Boolean(string='Manual Sales Purchase', copy=False, readonly=True)
    qing_overage_reason = fields.Char(string='Over-purchase Reason', copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        sources = self.env['sale.order.line'].browse([
            vals['sale_line_id'] for vals in vals_list if vals.get('sale_line_id')
        ])
        if sources:
            sources._qing_lock_purchase()
        lines = super().create(vals_list)
        lines._qing_check_capacity()
        return lines

    def write(self, vals):
        self.check_access('write')
        manual = self.filtered('qing_manual_purchase')
        if manual and 'qing_manual_purchase' in vals and not vals['qing_manual_purchase']:
            raise AccessError(_('The manual purchase marker cannot be changed.'))
        if manual and {'sale_line_id', 'product_id', 'order_id'} & vals.keys():
            for line in manual:
                if any(key in vals and vals[key] != line[key].id
                       for key in ('sale_line_id', 'product_id', 'order_id')):
                    raise UserError(_('Create a new purchase line instead of changing its source.'))
        watched = {'sale_line_id', 'product_qty', 'uom_id', 'order_id', 'qing_manual_purchase'} & vals.keys()
        if watched:
            sources = self.sale_line_id | self.env['sale.order.line'].browse(vals.get('sale_line_id', []))
            sources._qing_lock_purchase()
        result = super().write(vals)
        if watched:
            self._qing_check_capacity()
        return result

    def unlink(self):
        self.check_access('unlink')
        self.sale_line_id._qing_lock_purchase()
        return super().unlink()
