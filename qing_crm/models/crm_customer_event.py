from odoo import api, fields, models
from odoo.exceptions import AccessError

from .constants import CUSTOMER_GRADES, HEALTH_STATUSES, LIFECYCLE_STAGES


class CrmCustomerEvent(models.Model):
    _name = 'qing.crm.customer.event'
    _description = 'CRM Customer History'
    _check_company_auto = True
    _order = 'occurred_at desc, id desc'

    company_id = fields.Many2one('res.company', required=True, index=True, readonly=True)
    customer_grade = fields.Selection(CUSTOMER_GRADES, readonly=True)
    event_type = fields.Selection([
        ('enrolled', 'Enrolled'),
        ('claimed', 'Claimed'),
        ('assigned', 'Assigned'),
        ('returned', 'Returned to Pool'),
        ('shared', 'Collaboration Changed'),
        ('status', 'Customer Status Changed'),
    ], required=True, index=True, readonly=True)
    health_status = fields.Selection(HEALTH_STATUSES, readonly=True)
    lifecycle_stage = fields.Selection(LIFECYCLE_STAGES, readonly=True)
    occurred_at = fields.Datetime(required=True, default=fields.Datetime.now, index=True, readonly=True)
    owner_id = fields.Many2one('res.users', readonly=True)
    partner_id = fields.Many2one('res.partner', required=True, ondelete='restrict', readonly=True)
    partner_name = fields.Char(string='Customer', required=True, readonly=True)
    partner_role_id = fields.Many2one('qing.partner.role', required=True, ondelete='cascade', readonly=True)
    performed_by_id = fields.Many2one('res.users', required=True, default=lambda self: self.env.user, readonly=True)
    pool_id = fields.Many2one('qing.crm.pool', readonly=True)
    previous_customer_grade = fields.Selection(CUSTOMER_GRADES, readonly=True)
    previous_health_status = fields.Selection(HEALTH_STATUSES, readonly=True)
    previous_lifecycle_stage = fields.Selection(LIFECYCLE_STAGES, readonly=True)
    previous_owner_id = fields.Many2one('res.users', readonly=True)
    previous_pool_state = fields.Selection([('private', 'Private'), ('public', 'Public Pool')], readonly=True)
    reason = fields.Text(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(self.env._('Customer history is created by CRM workflows.'))
        return super().create(vals_list)

    def write(self, values):
        if not self.env.su:
            raise AccessError(self.env._('Customer history is immutable.'))
        return super().write(values)

    def unlink(self):
        if not self.env.su:
            raise AccessError(self.env._('Customer history is immutable.'))
        return super().unlink()
