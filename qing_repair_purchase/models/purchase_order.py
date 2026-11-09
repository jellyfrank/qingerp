from odoo import api, fields, models
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    @api.depends('order_line.move_dest_ids.repair_id', 'qing_repair_id')
    def _compute_repair_count(self):
        for purchase in self:
            purchase.repair_count = len(purchase.order_line.move_dest_ids.repair_id | purchase.qing_repair_id)

    qing_repair_id = fields.Many2one('repair.order', string='Source Repair',
        check_company=True, index=True, copy=False, ondelete='restrict')

    def action_view_repair_orders(self):
        self.check_access('read')
        repairs = self.order_line.move_dest_ids.repair_id | self.qing_repair_id
        return {'type': 'ir.actions.act_window', 'name': self.env._('Source Repairs'),
            'res_model': 'repair.order', 'view_mode': 'list,form', 'domain': [('id', 'in', repairs.ids)]}

    def write(self, vals):
        if 'qing_repair_id' in vals and any(order.qing_repair_id.id != vals['qing_repair_id'] for order in self):
            raise UserError(self.env._('The source repair of an existing purchase cannot be changed.'))
        return super().write(vals)
