from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .constants import CUSTOMER_GRADES, HEALTH_STATUSES, LIFECYCLE_STAGES


class PartnerRole(models.Model):
    _inherit = 'qing.partner.role'

    available_since = fields.Datetime(index=True, tracking=True)
    crm_managed = fields.Boolean(default=False, index=True, tracking=True)
    crm_owner_mode = fields.Selection([
        ('inherited', 'Use Contact Salesperson'),
        ('explicit', 'CRM Owner'),
        ('unassigned', 'Unassigned'),
    ], default='inherited', required=True, tracking=True)
    crm_team_id = fields.Many2one('crm.team', check_company=True, tracking=True, index=True)
    customer_grade = fields.Selection(CUSTOMER_GRADES, default='c', tracking=True, index=True)
    health_status = fields.Selection(HEALTH_STATUSES, default='healthy', tracking=True, index=True)
    last_effective_followup_at = fields.Datetime(readonly=True, index=True)
    last_followup_at = fields.Datetime(readonly=True, index=True)
    lifecycle_stage = fields.Selection(LIFECYCLE_STAGES, default='potential', tracking=True, index=True)
    next_activity_date = fields.Date(readonly=True, index=True)
    followup_ids = fields.One2many('qing.crm.followup', 'partner_role_id', readonly=True)
    history_ids = fields.One2many('qing.crm.customer.event', 'partner_role_id', readonly=True)
    pool_id = fields.Many2one('qing.crm.pool', check_company=True, tracking=True, index=True)
    pool_state = fields.Selection([
        ('private', 'Private'),
        ('public', 'Public Pool'),
    ], default='private', required=True, tracking=True, index=True)

    @api.depends(
        'role', 'crm_managed', 'crm_owner_mode', 'partner_id.user_id', 'partner_id.buyer_id',
        'override_owner_id', 'company_id', 'partner_id.user_id.company_ids',
        'partner_id.buyer_id.company_ids', 'partner_id.user_id.share',
        'partner_id.buyer_id.share', 'override_owner_id.company_ids', 'override_owner_id.share')
    def _compute_owner_id(self):
        for record in self:
            if record.role == 'customer' and record.crm_managed and record.crm_owner_mode == 'unassigned':
                record.effective_owner_id = False
                continue
            if record.role == 'customer' and record.crm_managed and record.crm_owner_mode == 'explicit':
                owner = record.override_owner_id
            else:
                owner = record.override_owner_id or (
                    record.partner_id.user_id if record.role == 'customer' else record.partner_id.buyer_id)
            record.effective_owner_id = owner if owner and record.company_id in owner.company_ids and not owner.share else False

    @api.constrains('crm_managed', 'role', 'pool_state', 'crm_owner_mode', 'override_owner_id')
    def _check_crm_assignment(self):
        for role in self:
            if role.crm_managed and role.role != 'customer':
                raise ValidationError(self.env._('Only customer roles can be managed in CRM.'))
            if role.crm_managed and role.pool_state == 'public' and role.crm_owner_mode != 'unassigned':
                raise ValidationError(self.env._('A public-pool customer must be unassigned.'))
            if role.crm_managed and role.crm_owner_mode == 'explicit' and not role.override_owner_id:
                raise ValidationError(self.env._('Select an owner for explicitly assigned customers.'))

    @api.model_create_multi
    def create(self, vals_list):
        protected = {'crm_managed', 'crm_owner_mode', 'crm_team_id', 'pool_id', 'pool_state'}
        if not self.env.su and any(protected & set(values) for values in vals_list):
            raise AccessError(self.env._('Use the customer management workflow to enroll and assign customers.'))
        roles = super().create(vals_list)
        roles._crm_sync_pool_entry()
        return roles

    def write(self, values):
        protected = {
            'available_since', 'crm_managed', 'crm_owner_mode', 'crm_team_id', 'customer_grade',
            'health_status', 'last_effective_followup_at', 'last_followup_at', 'lifecycle_stage',
            'next_activity_date', 'pool_id', 'pool_state',
        }
        if not self.env.su and protected & set(values):
            raise AccessError(self.env._('Use the customer management workflow to change CRM customer data.'))
        if not self.env.su and {'override_owner_id', 'shared_user_ids'} & set(values) and any(self.mapped('crm_managed')):
            raise AccessError(self.env._('Use the customer assignment workflow for managed customers.'))
        result = super().write(values)
        if not self.env.context.get('qing_crm_pool_sync') and \
                {'crm_managed', 'pool_state', 'pool_id', 'available_since', 'lifecycle_stage', 'customer_grade'} & set(values):
            self._crm_sync_pool_entry()
        return result

    def _crm_is_manager(self):
        return self.env.user.has_group('sales_team.group_sale_manager') \
            or self.env.user.has_group('qing_partner.group_partner_manager') \
            or self.env.user.has_group('base.group_system')

    def _crm_check_sales_user(self):
        if self.env.su:
            return
        user = self.env.user
        if not user._is_internal() or not user.has_group('sales_team.group_sale_salesman'):
            raise AccessError(self.env._('Only internal sales users can use customer management workflows.'))
        if any(role.company_id not in self.env.companies for role in self):
            raise AccessError(self.env._('The customer belongs to a company outside your active companies.'))

    def _crm_lock(self):
        if self.ids:
            self.env.cr.execute('SELECT id FROM qing_partner_role WHERE id = ANY(%s) FOR UPDATE', [self.ids])
            self.invalidate_recordset()

    def _crm_check_owner_or_manager(self):
        self._crm_lock()
        self._crm_check_sales_user()
        if self._crm_is_manager():
            return
        user = self.env.user
        if any(role.effective_owner_id != user and user not in role.shared_user_ids for role in self):
            raise AccessError(self.env._('Only the customer owner, a collaborator, or a sales manager can do this.'))

    def _crm_event(self, event_type, reason='', previous=None):
        previous = previous or {}
        values = []
        for role in self:
            values.append({
                'company_id': role.company_id.id,
                'customer_grade': role.customer_grade,
                'event_type': event_type,
                'health_status': role.health_status,
                'lifecycle_stage': role.lifecycle_stage,
                'owner_id': role.effective_owner_id.id,
                'partner_id': role.partner_id.id,
                'partner_name': role.partner_id.display_name,
                'partner_role_id': role.id,
                'performed_by_id': self.env.user.id,
                'pool_id': role.pool_id.id,
                'previous_customer_grade': previous.get('customer_grade'),
                'previous_health_status': previous.get('health_status'),
                'previous_lifecycle_stage': previous.get('lifecycle_stage'),
                'previous_owner_id': previous.get('owner_id'),
                'previous_pool_state': previous.get('pool_state'),
                'reason': reason,
            })
        return self.env['qing.crm.customer.event'].sudo().create(values)

    def _crm_sync_pool_entry(self):
        Entry = self.env['qing.crm.pool.entry'].sudo().with_context(active_test=False)
        for role in self.sudo():
            entry = Entry.search([('role_id', '=', role.id)], limit=1)
            public = role.crm_managed and role.role == 'customer' and role.pool_state == 'public'
            if not public:
                if entry and entry.active:
                    entry.write({'active': False})
                continue
            pool = role.pool_id or self.env['qing.crm.pool']._get_default_pool(role.company_id)
            if not role.pool_id:
                super(PartnerRole, role.with_context(qing_crm_pool_sync=True)).write({'pool_id': pool.id})
            values = {
                'active': True,
                'available_since': role.available_since or fields.Datetime.now(),
                'city': role.partner_id.city,
                'company_id': role.company_id.id,
                'country_id': role.partner_id.country_id.id,
                'customer_grade': role.customer_grade,
                'lifecycle_stage': role.lifecycle_stage,
                'name': role.partner_id.display_name,
                'pool_id': pool.id,
                'role_id': role.id,
            }
            entry.write(values) if entry else Entry.create(values)

    def _crm_assign(self, user, reason='', event_type='claimed'):
        self.ensure_one()
        self._crm_lock()
        previous = self._crm_previous_values()
        if user.share or self.company_id not in user.company_ids \
                or not user.has_group('sales_team.group_sale_salesman'):
            raise ValidationError(self.env._('The owner must be an internal sales user of the customer company.'))
        self.sudo().with_context(qing_crm_service=True).write({
            'available_since': False,
            'crm_managed': True,
            'crm_owner_mode': 'explicit',
            'override_owner_id': user.id,
            'pool_state': 'private',
        })
        self._crm_event(event_type, reason, previous)

    def _crm_return(self, pool, reason='', event_type='returned'):
        self.ensure_one()
        self._crm_check_owner_or_manager()
        previous = self._crm_previous_values()
        pool = pool or self.env['qing.crm.pool']._get_default_pool(self.company_id)
        if pool.company_id != self.company_id:
            raise ValidationError(self.env._('The pool and customer must belong to the same company.'))
        self.sudo().with_context(qing_crm_service=True).write({
            'available_since': fields.Datetime.now(),
            'crm_managed': True,
            'crm_owner_mode': 'unassigned',
            'override_owner_id': False,
            'pool_id': pool.id,
            'pool_state': 'public',
            'shared_user_ids': [Command.clear()],
        })
        self._crm_event(event_type, reason, previous)

    def _crm_update_status(self, lifecycle_stage, health_status, customer_grade, reason):
        self.ensure_one()
        self._crm_check_owner_or_manager()
        if not reason:
            raise UserError(self.env._('Enter a reason for the customer status change.'))
        previous = self._crm_previous_values()
        self.sudo().with_context(qing_crm_service=True).write({
            'customer_grade': customer_grade,
            'health_status': health_status,
            'lifecycle_stage': lifecycle_stage,
        })
        self._crm_event('status', reason, previous)

    def _crm_previous_values(self):
        self.ensure_one()
        return {
            'customer_grade': self.customer_grade,
            'health_status': self.health_status,
            'lifecycle_stage': self.lifecycle_stage,
            'owner_id': self.effective_owner_id.id,
            'pool_state': self.pool_state,
        }

    def action_open_assignment_wizard(self):
        self.ensure_one()
        self._crm_check_owner_or_manager()
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('Customer Assignment'),
            'res_model': 'qing.crm.assignment.wizard', 'view_mode': 'form', 'target': 'new',
            'context': {'default_role_id': self.id},
        }

    def action_open_customer(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': self.partner_id.display_name,
            'res_model': 'qing.partner.role', 'res_id': self.id, 'view_mode': 'form', 'target': 'current',
            'views': [(self.env.ref('qing_crm.view_crm_customer_form').id, 'form')],
        }

    def action_open_followup_wizard(self):
        self.ensure_one()
        self._crm_check_owner_or_manager()
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('Log Follow-up'),
            'res_model': 'qing.crm.followup.wizard', 'view_mode': 'form', 'target': 'new',
            'context': {'default_role_id': self.id},
        }

    def action_open_status_wizard(self):
        self.ensure_one()
        self._crm_check_owner_or_manager()
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('Update Customer Status'),
            'res_model': 'qing.crm.customer.status.wizard', 'view_mode': 'form', 'target': 'new',
            'context': {'default_role_id': self.id},
        }
