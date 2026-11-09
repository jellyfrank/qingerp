import math

from odoo import Command, api, fields, models, _
from odoo.exceptions import UserError


class PurchaseWizardLine(models.TransientModel):
    _name = 'qing.sale.purchase.wizard.line'
    _description = 'Sales Purchase Wizard Line'
    _check_company_auto = True

    def _purchase_currency(self):
        self.ensure_one()
        return (self.wizard_id.currency_id
                or self.supplier_id.with_company(self.company_id).property_purchase_currency_id
                or self.company_id.currency_id)

    def _preview_line(self, order=None):
        self.ensure_one()
        PurchaseOrder = self.env['purchase.order'].with_company(self.company_id)
        if order is None:
            order = PurchaseOrder.new(self.wizard_id._prepare_order_values(
                self.supplier_id, self._purchase_currency()))
        line = self.env['purchase.order.line'].with_company(self.company_id).new({
            'order_id': order.id, 'product_id': self.product_id.id,
            'product_qty': self.quantity, 'uom_id': self.uom_id.id,
        })
        line._compute_tax_id()
        line._compute_price_unit_and_date_planned_and_name()
        return line

    def _validate_purchase(self):
        self.ensure_one()
        if not self.supplier_id or not self.supplier_id.active:
            raise UserError(_('Choose an active vendor for every selected line.'))
        if self.supplier_id.company_id and self.supplier_id.company_id != self.company_id:
            raise UserError(_('The vendor belongs to another company.'))
        if not math.isfinite(self.quantity) or self.quantity <= 0:
            raise UserError(_('Purchase quantity must be positive.'))
        if not math.isfinite(self.price_unit) or self.price_unit < 0:
            raise UserError(_('Purchase price must be a finite, non-negative number.'))
        if not math.isfinite(self.discount) or not 0 <= self.discount <= 100:
            raise UserError(_('Discount must be between 0 and 100.'))
        if not self._purchase_currency().active:
            raise UserError(_('The purchase currency must be active.'))
        allowed = self.product_id._get_available_uoms() | self.product_id.seller_ids.uom_id
        if self.uom_id not in allowed:
            raise UserError(_('Choose a purchase unit supported by this product.'))
        self._check_company()

    def _prepare_line_values(self, order):
        preview = self._preview_line(order)
        return {
            'order_id': order.id, 'product_id': self.product_id.id,
            'product_qty': self.quantity, 'uom_id': self.uom_id.id,
            'price_unit': self.price_unit, 'technical_price_unit': self.price_unit,
            'discount': self.discount, 'tax_ids': [Command.set(preview.tax_ids.ids)],
            'name': preview.name, 'date_planned': self.wizard_id.date_planned,
            'sale_line_id': self.sale_line_id.id, 'qing_manual_purchase': True,
            'qing_overage_reason': self.overage_reason,
            'analytic_distribution': self.sale_line_id.analytic_distribution,
        }

    @api.depends('supplier_id', 'wizard_id.currency_id', 'company_id')
    def _compute_currency(self):
        for line in self:
            line.currency_id = line._purchase_currency()

    @api.onchange('supplier_id', 'quantity', 'uom_id')
    def _onchange_price_inputs(self):
        for line in self:
            line.price_unit = 0
            line.discount = 0
            if not line.supplier_id or not line.quantity or not line.uom_id:
                continue
            seller = line.product_id.with_company(line.company_id)._select_seller(
                partner_id=line.supplier_id, quantity=line.quantity,
                date=fields.Date.to_date(line.wizard_id.date_order), uom_id=line.uom_id,
            )
            if seller:
                preview = line._preview_line()
                line.price_unit = preview.price_unit
                line.discount = preview.discount

    arranged_qty = fields.Float(related='sale_line_id.qing_purchase_qty', string='Arranged Purchase')
    company_id = fields.Many2one(related='wizard_id.company_id', store=True)
    currency_id = fields.Many2one('res.currency', compute='_compute_currency')
    discount = fields.Float(string='Discount (%)', digits='Discount')
    overage_reason = fields.Char(string='Over-purchase Reason')
    price_unit = fields.Float(string='Unit Price', digits='Product Price')
    product_id = fields.Many2one(related='sale_line_id.product_id')
    quantity = fields.Float(string='Purchase Quantity', digits='Product Unit')
    remaining_qty = fields.Float(related='sale_line_id.qing_purchase_remaining_qty', string='Unarranged Purchase')
    sale_line_id = fields.Many2one('sale.order.line', required=True, readonly=True, check_company=True)
    sale_qty = fields.Float(related='sale_line_id.product_uom_qty', string='Sales Quantity')
    sale_uom_id = fields.Many2one(related='sale_line_id.product_uom_id', string='Sales Unit')
    selected = fields.Boolean(string='Select', default=True)
    supplier_id = fields.Many2one('res.partner', string='Vendor', check_company=True)
    uom_id = fields.Many2one('uom.uom', string='Purchase Unit', required=True)
    wizard_id = fields.Many2one('qing.sale.purchase.wizard', required=True, ondelete='cascade')
