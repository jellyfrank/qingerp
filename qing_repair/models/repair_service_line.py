from odoo import api, models
from odoo.exceptions import UserError

from .repair_pricing_mixin import PRICING_FIELDS


class RepairServiceLine(models.Model):
    _name = 'repair.service.line'
    _inherit = ['repair.service.line', 'qing.repair.pricing.mixin']

    def _qing_quantity(self):
        self.ensure_one()
        return self.quantity

    def _qing_description(self):
        self.ensure_one()
        return self.description or super()._qing_description()

    @api.depends('quantity')
    def _compute_qing_price_subtotal(self):
        return super()._compute_qing_price_subtotal()

    def _prepare_repair_service_line_common_vals(self):
        vals = super()._prepare_repair_service_line_common_vals()
        if self.repair_id.qing_pricing_mode == 'direct':
            vals.update(self._qing_invoice_values())
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        repairs = self.env['repair.order'].browse({vals['repair_id'] for vals in vals_list if vals.get('repair_id')})
        for repair in repairs:
            repair._qing_lock()
            repair._qing_check_price_confirmation()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('repair_id'):
            target = self.env['repair.order'].browse(vals['repair_id'])
            target._qing_lock()
            target._qing_check_price_confirmation()
        if 'repair_id' in vals and self.filtered(lambda line: line.repair_id and line.repair_id.id != vals['repair_id']):
            raise UserError(self.env._('Existing repair charges cannot be moved to another repair.'))
        if (PRICING_FIELDS | {'quantity', 'description', 'repair_id'}) & vals.keys():
            self._qing_check_editable()
        res = super().write(vals)
        if (PRICING_FIELDS | {'description'}) & vals.keys():
            self._qing_sync_invoice_prices()
        return res

    def unlink(self):
        self._qing_check_editable()
        return super().unlink()
