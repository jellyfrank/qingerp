from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Domain

ROLES = [('customer', 'Customer'), ('supplier', 'Supplier')]
STATES = [('pending', 'Pending review'), ('enabled', 'Enabled'), ('disabled', 'Disabled')]


class PartnerRole(models.Model):
    _name = 'qing.partner.role'
    _description = 'Partner Business Role'
    _inherit = ['mail.thread']
    _check_company_auto = True
    _rec_name = 'partner_id'
    _order = 'company_id, partner_id, role'

    _role_unique = models.Constraint('UNIQUE(partner_id, company_id, role)', 'A business role already exists for this partner and company.')

    @api.model
    def _qing_scope_domain(self):
        """Build a same-row SQL domain; never materialize partner ID lists."""
        user = self.env.user.sudo()
        company_ids = list(set(self.env.companies.ids) & set(user.company_ids.ids))
        domain = Domain('company_id', 'in', company_ids)
        if user.has_group('qing_partner.group_partner_manager') or user.has_group('base.group_system'):
            return domain
        if not user._is_internal():
            return Domain.FALSE
        if user.qing_partner_access_mode == 'shared':
            return domain
        scopes = []
        for role, scope in [('customer', user.qing_customer_scope), ('supplier', user.qing_supplier_scope)]:
            if scope == 'none':
                continue
            ownership = Domain('effective_owner_id', '=', user.id) | Domain('shared_user_ids', 'in', [user.id])
            if scope == 'team':
                if role == 'supplier':
                    ownership |= Domain('team_id', 'any!', Domain('member_ids', 'in', [user.id]))
                else:
                    teams = self.env['crm.team'].sudo().search([
                        ('active', '=', True), ('company_id', 'in', [False] + company_ids),
                        '|', ('member_ids', 'in', [user.id]), ('user_id', '=', user.id),
                    ])
                    for team in teams:
                        colleagues = team.member_ids | team.user_id
                        team_scope = Domain('effective_owner_id', 'in', colleagues.ids)
                        if team.company_id:
                            team_scope &= Domain('company_id', '=', team.company_id.id)
                        ownership |= team_scope
            if scope == 'all':
                # Pending migration hints do not implicitly grant access.
                ownership |= Domain('state', 'in', ['enabled', 'disabled'])
            scopes.append(Domain('role', '=', role) & ownership)
        return domain & Domain.OR(scopes)

    @api.depends_context('uid', 'company', 'allowed_company_ids')
    def _compute_qing_accessible(self):
        domain = self._qing_scope_domain() & Domain('id', 'in', self.ids)
        allowed = set(self.sudo().search(domain).ids)
        for role in self:
            role.qing_accessible = role.id in allowed

    def _search_qing_accessible(self, operator, value):
        domain = self._qing_scope_domain()
        return self.env['res.partner']._qing_boolean_domain(domain, operator, value)

    @api.constrains('partner_id', 'company_id', 'override_owner_id', 'shared_user_ids', 'team_id')
    def _check_assignment(self):
        for record in self:
            partner = record.partner_id
            if partner != partner.commercial_partner_id:
                raise ValidationError(self.env._('Assign roles to the commercial entity, not a child contact.'))
            if partner.company_id and partner.company_id != record.company_id:
                raise ValidationError(self.env._('The partner and role must belong to the same company.'))
            users = record.override_owner_id | record.shared_user_ids
            if any(user.share or record.company_id not in user.company_ids for user in users):
                raise ValidationError(self.env._('Owners and shared users must be internal users of the role company.'))

    @api.depends('role', 'partner_id.user_id', 'partner_id.buyer_id', 'override_owner_id', 'company_id',
                 'partner_id.user_id.company_ids', 'partner_id.buyer_id.company_ids',
                 'partner_id.user_id.share', 'partner_id.buyer_id.share', 'override_owner_id.company_ids', 'override_owner_id.share')
    def _compute_owner_id(self):
        for record in self:
            owner = record.override_owner_id or (record.partner_id.user_id if record.role == 'customer' else record.partner_id.buyer_id)
            record.effective_owner_id = owner if record.company_id in owner.company_ids and not owner.share else False

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True, tracking=True)
    effective_owner_id = fields.Many2one('res.users', string='Effective Owner', compute='_compute_owner_id', store=True, index=True)
    override_owner_id = fields.Many2one('res.users', string='Company-specific Owner', domain=[('share', '=', False)], tracking=True)
    partner_id = fields.Many2one('res.partner', required=True, ondelete='restrict', index=True, tracking=True)
    qing_accessible = fields.Boolean(compute='_compute_qing_accessible', search='_search_qing_accessible', compute_sudo=False)
    role = fields.Selection(ROLES, required=True, index=True, tracking=True)
    shared_user_ids = fields.Many2many('res.users', string='Explicitly shared with', domain=[('share', '=', False)], tracking=True)
    state = fields.Selection(STATES, default='pending', required=True, index=True, tracking=True)
    team_id = fields.Many2one('qing.partner.team', check_company=True, tracking=True, ondelete='restrict', index=True)

    @api.model_create_multi
    def create(self, vals_list):
        if any('effective_owner_id' in values for values in vals_list):
            raise ValidationError(self.env._('Use the native responsible user or a company-specific override.'))
        roles = super().create(vals_list)
        self.env.invalidate_all()
        return roles

    def write(self, values):
        if 'effective_owner_id' in values:
            raise ValidationError(self.env._('Use the native responsible user or a company-specific override.'))
        for field in ('partner_id', 'company_id', 'role'):
            if field in values and any(
                (record[field].id if field != 'role' else record[field]) != values[field] for record in self
            ):
                raise ValidationError(self.env._('Disable the old role and create a new one instead of changing its identity.'))
        result = super().write(values)
        self.env.invalidate_all()
        return result

    def unlink(self):
        if not self.env.su:
            raise AccessError(self.env._('Disable business roles instead of deleting them.'))
        result = super().unlink()
        self.env.invalidate_all()
        return result
