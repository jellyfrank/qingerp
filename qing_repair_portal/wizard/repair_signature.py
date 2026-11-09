from odoo import fields, models
from odoo.exceptions import AccessError


class RepairSignature(models.TransientModel):
    _name = 'qing.repair.signature'
    _description = 'Customer Repair Signature'

    repair_id = fields.Many2one('repair.order', string='Repair Order', required=True, readonly=True)
    kind = fields.Selection([('price', 'Price Confirmation'), ('acceptance', 'Completion Acceptance')], string='Signature Type', required=True, readonly=True)
    signed_by = fields.Char(string='Customer Name', required=True)
    signature = fields.Binary(string='Customer Signature', required=True)
    price_hash = fields.Char(required=True, readonly=True)

    def action_sign(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('stock.group_stock_user'):
            raise AccessError(self.env._('Only repair users can collect customer signatures.'))
        self.repair_id.check_access('write')
        signature = self.signature.to_base64()
        self.repair_id.sudo()._qing_sign(self.kind, self.signed_by, signature, self.price_hash)
        return {'type': 'ir.actions.act_window_close'}
