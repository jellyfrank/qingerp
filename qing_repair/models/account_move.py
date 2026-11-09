from odoo import models
from odoo.exceptions import UserError
from odoo.tools import float_compare


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _post(self, soft=True):
        for invoice in self.filtered(lambda record: record.repair_order_id
                and record.repair_order_id.sudo().qing_pricing_mode == 'direct' and record.move_type == 'out_invoice'):
            repair = invoice.repair_order_id.sudo()
            repair._qing_lock()
            if (repair.qing_pricing_mode != 'direct' or repair.state != 'done'
                    or invoice.company_id != repair.company_id
                    or invoice.partner_id.commercial_partner_id != repair.partner_id.commercial_partner_id
                    or invoice.currency_id != repair.qing_currency_id):
                raise UserError(self.env._('A repair invoice must match its completed repair, customer, company, and currency.'))
            seen = set()
            for line in invoice.invoice_line_ids.filtered(lambda record: not record.display_type or record.display_type == 'product'):
                source = (line.repair_service_line_id or line.stock_move_id).sudo()
                if not source and not line.quantity:
                    continue
                if not source or source.repair_id != repair or (source._name == 'stock.move' and source.repair_line_type != 'add'):
                    raise UserError(self.env._('Repair invoices can contain only charges from their repair order.'))
                key = (source._name, source.id)
                values = source._qing_invoice_values()
                duplicate = source.invoice_line_ids.filtered(lambda item: item.move_id != invoice and item.move_id.state == 'posted' and item.move_id.move_type == 'out_invoice')
                if (key in seen or duplicate or line.product_id != source.product_id or line.product_uom_id != source.uom_id
                        or float_compare(line.quantity, source._qing_quantity(), precision_digits=6)
                        or float_compare(line.price_unit, values['price_unit'], precision_digits=6)
                        or float_compare(line.discount, values['discount'], precision_digits=6)
                        or line.tax_ids != source.qing_tax_ids):
                    raise UserError(self.env._('Repair invoice prices, quantities, and taxes must match the repair order. Update the repair charges instead.'))
                seen.add(key)
        return super()._post(soft=soft)
