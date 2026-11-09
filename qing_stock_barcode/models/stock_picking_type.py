from odoo import fields, models

from ..services.common import check_operator


class StockPickingType(models.Model):
    _inherit = 'stock.picking.type'

    qing_scan_destination_required = fields.Boolean('Require destination scan')
    qing_scan_source_required = fields.Boolean('Require source scan')

    def action_redirect_to_barcode_installation(self):
        """Open the installed Community scanner instead of the Enterprise upsell."""
        check_operator(self.env)
        action = self.env['ir.actions.actions']._for_xml_id(
            'qing_stock_barcode.action_scan_workbench'
        )
        operation = self.env.context.get('restricted_picking_type_code')
        if operation in ('incoming', 'outgoing', 'internal'):
            action['params'] = {'operation': operation}
        return action
