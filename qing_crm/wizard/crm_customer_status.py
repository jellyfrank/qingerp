from odoo import api, fields, models

from ..models.constants import CUSTOMER_GRADES, HEALTH_STATUSES, LIFECYCLE_STAGES


class CrmCustomerStatusWizard(models.TransientModel):
    _name = 'qing.crm.customer.status.wizard'
    _description = 'Update CRM Customer Status'

    customer_grade = fields.Selection(CUSTOMER_GRADES, required=True)
    health_status = fields.Selection(HEALTH_STATUSES, required=True)
    lifecycle_stage = fields.Selection(LIFECYCLE_STAGES, required=True)
    reason = fields.Text(required=True)
    role_id = fields.Many2one('qing.partner.role', required=True)

    @api.onchange('role_id')
    def _onchange_role_id(self):
        if self.role_id:
            self.customer_grade = self.role_id.customer_grade
            self.health_status = self.role_id.health_status
            self.lifecycle_stage = self.role_id.lifecycle_stage

    def action_confirm(self):
        self.ensure_one()
        self.role_id._crm_update_status(
            self.lifecycle_stage, self.health_status, self.customer_grade, self.reason)
        return {'type': 'ir.actions.act_window_close'}
