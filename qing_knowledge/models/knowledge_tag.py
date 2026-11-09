from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Domain


class KnowledgeTag(models.Model):
    _name = 'qing.knowledge.tag'
    _description = 'Knowledge Space Tag'
    _order = 'name, id'

    _unique_name = models.Constraint('UNIQUE(space_id, normalized_name)', 'Tag name already exists in this space.')

    @api.model
    def _knowledge_access_domain(self):
        return Domain('space_id', 'access', 'read')

    def _compute_accessible(self):
        allowed = set(self.sudo().search(self._knowledge_access_domain() & Domain('id', 'in', self.ids)).ids)
        for tag in self:
            tag.accessible = tag.id in allowed

    def _search_accessible(self, operator, value):
        if operator not in ('=', '!='):
            raise ValidationError(self.env._('Use equality for tag visibility.'))
        domain = self._knowledge_access_domain()
        return domain if bool(value) == (operator == '=') else ~domain

    accessible = fields.Boolean(compute='_compute_accessible', search='_search_accessible', compute_sudo=False)
    company_id = fields.Many2one('res.company', related='space_id.company_id', store=True, readonly=True)
    name = fields.Char(required=True)
    normalized_name = fields.Char(required=True, readonly=True)
    space_id = fields.Many2one('qing.knowledge.space', required=True, ondelete='cascade', index=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(self.env._('Set tags through an editing draft.'))
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(self.env._('Knowledge tags cannot be changed in place.'))

    def unlink(self):
        raise AccessError(self.env._('Knowledge tags cannot be deleted in place.'))
