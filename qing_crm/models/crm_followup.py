from odoo import api, fields, models
from odoo.exceptions import AccessError

from .constants import CHANNELS, CUSTOMER_GRADES, HEALTH_STATUSES, LIFECYCLE_STAGES, RESULTS


class CrmFollowup(models.Model):
    _name = 'qing.crm.followup'
    _description = 'CRM Structured Follow-up'
    _check_company_auto = True
    _order = 'occurred_at desc, id desc'

    attachment_ids = fields.Many2many('ir.attachment', string='Attachments', readonly=True)
    channel = fields.Selection(CHANNELS, required=True, index=True, readonly=True)
    company_id = fields.Many2one('res.company', required=True, index=True, readonly=True)
    created_by_id = fields.Many2one('res.users', required=True, default=lambda self: self.env.user, readonly=True)
    crm_team_id = fields.Many2one('crm.team', readonly=True)
    customer_grade = fields.Selection(CUSTOMER_GRADES, readonly=True)
    duration_minutes = fields.Integer(readonly=True)
    effective_contact = fields.Boolean(readonly=True)
    health_status = fields.Selection(HEALTH_STATUSES, readonly=True)
    lifecycle_stage = fields.Selection(LIFECYCLE_STAGES, readonly=True)
    next_activity_id = fields.Many2one('mail.activity', readonly=True)
    note = fields.Text(required=True, readonly=True)
    occurred_at = fields.Datetime(required=True, default=fields.Datetime.now, index=True, readonly=True)
    owner_id = fields.Many2one('res.users', readonly=True)
    partner_id = fields.Many2one('res.partner', required=True, ondelete='restrict', index=True, readonly=True)
    partner_name = fields.Char(string='Customer', required=True, index=True, readonly=True)
    partner_role_id = fields.Many2one('qing.partner.role', required=True, ondelete='restrict', index=True, readonly=True)
    result = fields.Selection(RESULTS, required=True, index=True, readonly=True)

    def action_open_customer(self):
        self.ensure_one()
        return self.partner_role_id.action_open_customer()

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(self.env._('Follow-ups must be recorded through the CRM workflow.'))
        return super().create(vals_list)

    def write(self, values):
        if not self.env.su:
            raise AccessError(self.env._('Recorded follow-ups are immutable. Add a corrective follow-up instead.'))
        return super().write(values)

    def unlink(self):
        if not self.env.su:
            raise AccessError(self.env._('Recorded follow-ups are immutable.'))
        return super().unlink()
