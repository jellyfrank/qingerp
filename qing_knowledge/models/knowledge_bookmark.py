from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.fields import Domain


class KnowledgeBookmark(models.Model):
    _name = 'qing.knowledge.bookmark'
    _description = 'Personal Knowledge Bookmark'
    _order = 'last_opened_at desc, id desc'

    _unique_user_article = models.Constraint('UNIQUE(user_id, article_id)', 'Bookmark already exists.')

    @api.model
    def _knowledge_access_domain(self):
        return Domain('user_id', '=', self.env.uid) & Domain('article_id', 'access', 'read') & Domain('article_id.lifecycle', '=', 'published')

    def _compute_accessible(self):
        allowed = set(self.sudo().search(self._knowledge_access_domain() & Domain('id', 'in', self.ids)).ids)
        for record in self:
            record.accessible = record.id in allowed

    def _search_accessible(self, operator, value):
        if operator not in ('=', '!='):
            raise AccessError(self.env._('Use equality for bookmark visibility.'))
        domain = self._knowledge_access_domain()
        return domain if bool(value) == (operator == '=') else ~domain

    accessible = fields.Boolean(compute='_compute_accessible', search='_search_accessible', compute_sudo=False)
    article_id = fields.Many2one('qing.knowledge.article', required=True, ondelete='cascade', index=True)
    user_id = fields.Many2one('res.users', required=True, ondelete='cascade', index=True)
    is_favorite = fields.Boolean(default=False)
    last_opened_at = fields.Datetime()

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(self.env._('Use the article favorite action.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su or set(vals) - {'is_favorite', 'last_opened_at'}:
            raise AccessError(self.env._('Use the article favorite action.'))
        return super().write(vals)

    def unlink(self):
        raise AccessError(self.env._('Bookmarks are retained until account removal.'))
