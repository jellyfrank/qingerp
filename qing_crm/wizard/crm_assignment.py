from odoo import Command, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class CrmAssignmentWizard(models.TransientModel):
    _name = 'qing.crm.assignment.wizard'
    _description = 'CRM Customer Assignment'

    company_id = fields.Many2one('res.company', related='role_id.company_id')
    operation = fields.Selection([
        ('assign', 'Assign or Transfer'),
        ('return', 'Return to Public Pool'),
        ('share', 'Add Collaborator'),
        ('unshare', 'Remove Collaborator'),
    ], required=True, default='assign')
    pool_id = fields.Many2one('qing.crm.pool', check_company=True)
    reason = fields.Text(required=True)
    role_id = fields.Many2one('qing.partner.role', required=True)
    user_id = fields.Many2one('res.users', domain=[('share', '=', False)])

    def action_confirm(self):
        self.ensure_one()
        role = self.role_id
        role._crm_check_owner_or_manager()
        if self.operation == 'return':
            role._crm_return(self.pool_id, self.reason)
        elif self.operation == 'assign':
            if not self.user_id:
                raise UserError(self.env._('Select the new owner.'))
            if self.user_id != self.env.user and not role._crm_is_manager():
                raise AccessError(self.env._('Only a sales manager can transfer a customer to another owner.'))
            role._crm_assign(self.user_id, self.reason, 'assigned')
        else:
            if not self.user_id:
                raise UserError(self.env._('Select a collaborator.'))
            if self.user_id.share or role.company_id not in self.user_id.company_ids:
                raise ValidationError(self.env._('The collaborator must be an internal user of the customer company.'))
            previous = role._crm_previous_values()
            command = Command.link(self.user_id.id) if self.operation == 'share' else Command.unlink(self.user_id.id)
            role.sudo().with_context(qing_crm_service=True).write({'shared_user_ids': [command]})
            role._crm_event('shared', self.reason, previous)
        return {'type': 'ir.actions.act_window_close'}
