from odoo import models


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    def _get_rendering_context(self, report, docids, data):
        if report.model == 'sale.order' and report.report_name in ('sale.report_saleorder', 'sale.report_saleorder_pro_forma'):
            orders = self.env['sale.order'].browse(docids)
            orders.check_access('read')
            orders._qing_rental_check_quote_billing()
        return super()._get_rendering_context(report, docids, data)
