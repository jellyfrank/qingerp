from odoo import api, fields, models
from odoo.exceptions import ValidationError


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    @api.constrains('qing_repair_move_id', 'qing_repair_service_line_id', 'order_id', 'product_id')
    def _check_qing_repair_source(self):
        for line in self:
            sources = line.qing_repair_move_id
            service = line.qing_repair_service_line_id
            if sources and service:
                raise ValidationError(self.env._('Choose only one repair purchase source.'))
            source = sources or service
            if source and (source.repair_id != line.order_id.qing_repair_id or source.product_id != line.product_id):
                raise ValidationError(self.env._('The purchased product must match its source repair line.'))

    qing_repair_move_id = fields.Many2one('stock.move', string='Repair Part Source',
        check_company=True, copy=False, ondelete='restrict')
    qing_repair_service_line_id = fields.Many2one('repair.service.line', string='Repair Service Source',
        check_company=True, copy=False, ondelete='restrict')
