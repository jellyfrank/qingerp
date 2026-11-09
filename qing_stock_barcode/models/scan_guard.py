from odoo import api, models
from odoo.exceptions import AccessError

from ..services.common import INTERNAL


class ScanGuard(models.AbstractModel):
    _name = 'qing.stock.scan.guard'
    _description = 'Controlled Scan Record Mutation'

    def _check_internal(self):
        if self.env.context.get('_qing_scan_internal') is not INTERNAL:
            raise AccessError(self.env._('Use the warehouse scanning workflow to change these records.'))

    @api.model_create_multi
    def create(self, vals_list):
        self._check_internal()
        return super().create(vals_list)

    def write(self, values):
        self._check_internal()
        return super().write(values)

    def unlink(self):
        raise AccessError(self.env._('Scan evidence cannot be deleted. Cancel the session instead.'))
