from markupsafe import Markup, escape

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..models.constants import CHANNELS, RESULTS


class CrmFollowupWizard(models.TransientModel):
    _name = 'qing.crm.followup.wizard'
    _description = 'Log CRM Follow-up'

    attachment_ids = fields.Many2many('ir.attachment', string='Attachments')
    activity_type_id = fields.Many2one('mail.activity.type')
    channel = fields.Selection(CHANNELS, required=True, default='phone')
    duration_minutes = fields.Integer()
    effective_contact = fields.Boolean(default=True)
    next_activity_date = fields.Date()
    next_activity_summary = fields.Char()
    note = fields.Text(required=True)
    occurred_at = fields.Datetime(required=True, default=fields.Datetime.now)
    result = fields.Selection(RESULTS, required=True, default='connected')
    role_id = fields.Many2one('qing.partner.role', required=True)
    schedule_next = fields.Boolean(default=False)

    @api.constrains('duration_minutes')
    def _check_duration_minutes(self):
        if any(wizard.duration_minutes < 0 for wizard in self):
            raise ValidationError(self.env._('Duration cannot be negative.'))

    @api.constrains('schedule_next', 'activity_type_id', 'next_activity_date')
    def _check_next_activity(self):
        for wizard in self:
            if wizard.schedule_next and (not wizard.activity_type_id or not wizard.next_activity_date):
                raise ValidationError(self.env._('Select an activity type and deadline for the next activity.'))

    def action_confirm(self):
        self.ensure_one()
        role = self.role_id
        role._crm_check_owner_or_manager()
        foreign_attachments = self.attachment_ids.filtered(
            lambda attachment: attachment.res_model not in (False, self._name)
            or (attachment.res_model == self._name and attachment.res_id not in (0, self.id)))
        if foreign_attachments:
            raise AccessError(self.env._('Only attachments uploaded for this follow-up can be included.'))
        next_activity = False
        if self.schedule_next:
            next_activity = role.partner_id.activity_schedule(
                activity_type_id=self.activity_type_id.id,
                date_deadline=self.next_activity_date,
                summary=self.next_activity_summary or self.env._('Customer follow-up'),
                user_id=role.effective_owner_id.id or self.env.user.id,
            )
        followup = self.env['qing.crm.followup'].sudo().create({
            'attachment_ids': [Command.set(self.attachment_ids.ids)],
            'channel': self.channel,
            'company_id': role.company_id.id,
            'created_by_id': self.env.user.id,
            'crm_team_id': role.crm_team_id.id,
            'customer_grade': role.customer_grade,
            'duration_minutes': self.duration_minutes,
            'effective_contact': self.effective_contact,
            'health_status': role.health_status,
            'lifecycle_stage': role.lifecycle_stage,
            'next_activity_id': next_activity.id if next_activity else False,
            'note': self.note,
            'occurred_at': self.occurred_at,
            'owner_id': role.effective_owner_id.id,
            'partner_id': role.partner_id.id,
            'partner_name': role.partner_id.display_name,
            'partner_role_id': role.id,
            'result': self.result,
        })
        if self.attachment_ids:
            self.attachment_ids.sudo().write({'res_model': 'qing.crm.followup', 'res_id': followup.id})
        values = {'last_followup_at': self.occurred_at, 'next_activity_date': self.next_activity_date or False}
        if self.effective_contact:
            values['last_effective_followup_at'] = self.occurred_at
        role.sudo().with_context(qing_crm_service=True).write(values)
        role.partner_id.message_post(body=Markup('<b>%s</b><br/>%s') % (
            escape(self.env._('CRM Follow-up')), escape(self.note)))
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('Follow-up'),
            'res_model': 'qing.crm.followup', 'res_id': followup.id, 'view_mode': 'form', 'target': 'current',
        }
