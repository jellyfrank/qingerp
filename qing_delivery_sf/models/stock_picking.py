from odoo import fields, models

from .delivery_carrier import EXPRESS_TYPES, PAYMENT_METHODS


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _sf_express_type(self):
        self.ensure_one()
        if self.carrier_id.sf_select_express_type and self.sf_express_type:
            return self.sf_express_type
        return self.carrier_id.sf_default_express_type or '1'

    def _sf_payment_method(self):
        self.ensure_one()
        if self.carrier_id.sf_payment_select and self.sf_payment_method:
            return self.sf_payment_method
        return self.carrier_id.sf_default_payment or '1'

    sf_declared_currency_id = fields.Many2one('res.currency', string='SF Declared Value Currency', copy=False)
    sf_declared_value = fields.Float(string='SF Declared Value', copy=False)
    sf_express_type = fields.Selection(EXPRESS_TYPES, string='SF Product Type', copy=False)
    sf_express_type_select = fields.Boolean(related='carrier_id.sf_select_express_type')
    sf_manual_order = fields.Boolean(related='carrier_id.sf_manual_order')
    sf_payment_method = fields.Selection(PAYMENT_METHODS, string='SF Payment Method', copy=False)
    sf_payment_select = fields.Boolean(related='carrier_id.sf_payment_select')

    def action_sf_order(self):
        """Create the SF waybill before validation."""
        for picking in self:
            picking.send_to_shipper()
        return True

    def action_sf_print_label(self):
        for picking in self:
            picking.carrier_id.sf_reprint_label(picking)
        return True
