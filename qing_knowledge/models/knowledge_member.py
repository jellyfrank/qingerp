from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Domain

MEMBER_ROLES = [
    ('reader', 'Reader'), ('editor', 'Editor'),
    ('publisher', 'Publisher'), ('manager', 'Manager'),
]


class KnowledgeMember(models.Model):
    _name = 'qing.knowledge.member'
    _description = 'Knowledge Space Member'
    _order = 'space_id, user_id'
    _check_company_auto = True

    _unique_member = models.Constraint('UNIQUE(space_id, user_id)', 'User is already a member of this space.')

    @api.model
    def _knowledge_access_domain(self):
        user = self.env.user.sudo()
        own = Domain('user_id', '=', user.id)
        manager = Domain('space_id.member_ids', 'any!', Domain('user_id', '=', user.id) & Domain('active', '=', True) & Domain('role', '=', 'manager'))
        return Domain('space_id', 'access', 'read') & (own | manager)

    def _compute_accessible(self):
        allowed = set(self.sudo().search(self._knowledge_access_domain() & Domain('id', 'in', self.ids)).ids)
        for member in self:
            member.accessible = member.id in allowed

    def _search_accessible(self, operator, value):
        domain = self._knowledge_access_domain()
        if operator in ('=', '!='):
            return domain if bool(value) == (operator == '=') else ~domain
        if operator in ('in', 'not in'):
            values = set(value)
            if values == {False, True}:
                return Domain.TRUE
            positive = (True in values) == (operator == 'in')
            return domain if positive else ~domain
        raise ValidationError(self.env._('Use equality for accessibility.'))

    accessible = fields.Boolean(compute='_compute_accessible', search='_search_accessible', compute_sudo=False)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', related='space_id.company_id', store=True, readonly=True)
    role = fields.Selection(MEMBER_ROLES, required=True, default='reader')
    space_id = fields.Many2one('qing.knowledge.space', required=True, ondelete='cascade', index=True)
    user_id = fields.Many2one('res.users', required=True, ondelete='restrict', index=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(self.env._('Use the space member action.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise AccessError(self.env._('Use the space member action.'))
        return super().write(vals)

    def unlink(self):
        raise AccessError(self.env._('Deactivate a member instead of deleting it.'))
