import math

from odoo import api, Command, fields, models
from odoo.exceptions import UserError


class RepairPurchaseWizardLine(models.TransientModel):
    _name = 'qing.repair.purchase.wizard.line'
    _description = 'Repair Purchase Wizard Line'
    _check_company_auto = True

    def _source(self):
        self.ensure_one()
        if bool(self.repair_move_id) == bool(self.repair_service_line_id):
            raise UserError(self.env._('Choose exactly one source repair line.'))
        return self.repair_move_id or self.repair_service_line_id

    def _validate_purchase(self, arranged, seen):
        source = self._source()
        source.check_access('read')
        repair = self.wizard_id.repair_id
        eligible = source.repair_id == repair and source.product_id.purchase_ok and source.product_id == self.product_id
        if source._name == 'stock.move':
            eligible = eligible and source.repair_line_type == 'add' and source.state != 'cancel'
            eligible = eligible and not source.created_purchase_line_ids.filtered(lambda line:
                line.order_id.state != 'cancel' and not line.qing_repair_move_id)
        if not eligible:
            raise UserError(self.env._('The purchase source must be an eligible line of this repair.'))
        key = (source._name, source.id)
        if key in seen:
            raise UserError(self.env._('A repair source may appear only once.'))
        seen.add(key)
        if not math.isfinite(self.quantity) or self.quantity <= 0 or not math.isfinite(self.price_unit) or self.price_unit < 0:
            raise UserError(self.env._('Purchase quantity must be positive and price must be non-negative.'))
        if not math.isfinite(self.discount) or not 0 <= self.discount <= 100:
            raise UserError(self.env._('Discount must be between 0 and 100.'))
        if self.uom_id not in self.product_id._get_available_uoms():
            raise UserError(self.env._('Choose a unit supported by this product.'))
        uom = source.uom_id
        required = source.product_uom_qty if source._name == 'stock.move' else source.quantity
        quantity = self.uom_id._compute_quantity(self.quantity, uom)
        if uom.compare(quantity, required - arranged.get(key, 0)) > 0:
            raise UserError(self.env._('Purchase quantity exceeds the remaining repair requirement.'))
        self._check_company()

    def _preview_line(self):
        wizard = self.wizard_id
        currency = wizard.supplier_id.with_company(self.company_id).property_purchase_currency_id or self.company_id.currency_id
        order = self.env['purchase.order'].with_company(self.company_id).new({
            'partner_id': wizard.supplier_id.id, 'company_id': self.company_id.id,
            'currency_id': currency.id, 'date_order': wizard.date_order})
        line = self.env['purchase.order.line'].with_company(self.company_id).new({
            'order_id': order.id, 'product_id': self.product_id.id,
            'product_qty': self.quantity, 'uom_id': self.uom_id.id})
        line._compute_tax_id()
        line._compute_price_unit_and_date_planned_and_name()
        return line

    def _prepare_line(self):
        preview = self._preview_line()
        return {'product_id': self.product_id.id, 'product_qty': self.quantity,
            'uom_id': self.uom_id.id, 'price_unit': self.price_unit, 'technical_price_unit': self.price_unit,
            'discount': self.discount,
            'name': preview.name, 'tax_ids': [Command.set(preview.tax_ids.ids)],
            'date_planned': self.wizard_id.date_planned,
            'move_dest_ids': [Command.link(self.repair_move_id.id)] if self.repair_move_id else [],
            'propagate_cancel': False,
            'qing_repair_move_id': self.repair_move_id.id,
            'qing_repair_service_line_id': self.repair_service_line_id.id}

    @api.onchange('product_id', 'quantity', 'uom_id')
    def _onchange_price(self):
        for line in self:
            if line.wizard_id.supplier_id and line.product_id and line.uom_id:
                preview = line._preview_line()
                line.price_unit = preview.price_unit
                line.discount = preview.discount

    company_id = fields.Many2one(related='wizard_id.company_id', store=True)
    discount = fields.Float(string='Discount (%)', digits='Discount')
    price_unit = fields.Float(string='Unit Price', digits='Product Price')
    product_id = fields.Many2one('product.product', required=True, readonly=True, check_company=True)
    quantity = fields.Float(string='Purchase Quantity', required=True, digits='Product Unit')
    repair_move_id = fields.Many2one('stock.move', readonly=True, check_company=True)
    repair_service_line_id = fields.Many2one('repair.service.line', readonly=True, check_company=True)
    selected = fields.Boolean(string='Select', default=True)
    uom_id = fields.Many2one('uom.uom', required=True)
    wizard_id = fields.Many2one('qing.repair.purchase.wizard', required=True, ondelete='cascade')
