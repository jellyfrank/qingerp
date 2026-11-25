from odoo import fields, models


class B2BShortageConfirmation(models.TransientModel):
    _name = "qing.b2b.shortage.confirmation"
    _description = "Confirm a mobile quotation with insufficient stock"

    message = fields.Text(readonly=True)
    order_ids = fields.Many2many("sale.order", required=True, readonly=True)

    def action_continue(self):
        self.ensure_one()
        self.order_ids.check_access("write")
        return self.order_ids.with_context(b2b_acknowledge_shortages=True).action_confirm()
