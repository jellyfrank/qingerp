from odoo import models
from odoo.exceptions import UserError
from odoo.modules.module import get_manifest

from odoo.addons.qing_base.tools import addon_status


class StockPickingType(models.Model):
    _inherit = 'stock.picking.type'

    def action_redirect_to_barcode_installation(self):
        """Show our scanner's native install page without installing anything."""
        self.check_access('read')
        if not self.env.is_system():
            raise UserError(self.env._('Contact your administrator to install QingERP Warehouse Scanning.'))

        domain = [('name', '=', 'qing_stock_barcode')]
        module = self.env['ir.module.module'].search(domain, limit=1)
        status = addon_status(get_manifest('qing_stock_barcode'), module.state)
        if status in ('missing', 'unavailable'):
            raise UserError(self.env._('QingERP Warehouse Scanning is not available on this server. Contact your administrator to deploy it.'))
        if status == 'unindexed':
            raise UserError(self.env._('Update the Apps list to find QingERP Warehouse Scanning.'))

        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('QingERP Warehouse Scanning'),
            'res_model': 'ir.module.module',
            'res_id': module.id,
            'views': [(self.env.ref('base.module_form').id, 'form')],
            'target': 'current',
        }
