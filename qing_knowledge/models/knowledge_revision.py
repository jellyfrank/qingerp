from uuid import uuid4

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Domain

from ..services.markdown import render_markdown


class KnowledgeRevision(models.Model):
    _name = 'qing.knowledge.revision'
    _description = 'Published Knowledge Revision'
    _order = 'article_id, number desc'
    _check_company_auto = True
    _rec_name = 'title'

    _unique_number = models.Constraint('UNIQUE(article_id, number)', 'Revision number must be unique per article.')
    _unique_uuid = models.Constraint('UNIQUE(uuid)', 'Revision UUID must be unique.')

    @api.model
    def _knowledge_access_domain(self):
        user = self.env.user.sudo()
        if not user.active or not user._is_internal():
            return Domain.FALSE
        member = Domain('user_id', '=', user.id) & Domain('active', '=', True)
        editor = Domain('article_id.space_id.member_ids', 'any!', member & Domain('role', 'in', ['editor', 'publisher', 'manager']))
        current = Domain('is_current', '=', True) & Domain('article_id.lifecycle', '=', 'published')
        # Current revision is also available to readers; historical revisions need editing role.
        return Domain('article_id', 'access', 'read') & (editor | current)

    def _compute_accessible(self):
        allowed = set(self.sudo().search(self._knowledge_access_domain() & Domain('id', 'in', self.ids)).ids)
        for revision in self:
            revision.accessible = revision.id in allowed

    def _search_accessible(self, operator, value):
        domain = self._knowledge_access_domain()
        if operator in ('=', '!='):
            return domain if bool(value) == (operator == '=') else ~domain
        if operator in ('in', 'not in'):
            values = set(value)
            if values == {False, True}:
                return Domain.TRUE
            return domain if (True in values) == (operator == 'in') else ~domain
        raise ValidationError(self.env._('Use equality for accessibility.'))

    accessible = fields.Boolean(compute='_compute_accessible', search='_search_accessible', compute_sudo=False)
    article_id = fields.Many2one('qing.knowledge.article', required=True, ondelete='restrict', index=True)
    asset_ids = fields.One2many('qing.knowledge.asset', 'revision_id', string='Assets')
    author_id = fields.Many2one('res.users', required=True)
    company_id = fields.Many2one('res.company', related='article_id.company_id', store=True, readonly=True)
    content_hash = fields.Char(required=True, readonly=True)
    is_current = fields.Boolean(default=False, readonly=True, index=True)
    markdown = fields.Text(readonly=True)
    number = fields.Integer(required=True, readonly=True)
    parent_uuid = fields.Char(readonly=True)
    published_at = fields.Datetime(required=True, readonly=True)
    published_by = fields.Many2one('res.users', required=True, readonly=True)
    summary = fields.Text(readonly=True)
    tag_ids = fields.Many2many('qing.knowledge.tag', 'qing_knowledge_revision_tag_rel', 'revision_id', 'tag_id', readonly=True)
    title = fields.Char(required=True, readonly=True)
    uuid = fields.Char(required=True, default=lambda self: str(uuid4()), readonly=True, index=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(self.env._('Only the publish action can create revisions.'))
        return super().create(vals_list)

    def write(self, vals):
        if self.env.su and set(vals) == {'is_current'}:
            return super().write(vals)
        raise AccessError(self.env._('Published revision content is immutable.'))

    def unlink(self):
        raise AccessError(self.env._('Published revisions cannot be deleted.'))

    def rendered_html(self):
        self.ensure_one()
        self.check_access('read')
        return render_markdown(self.markdown, lambda path: '/qing/knowledge/asset/%s/%s' % (self.uuid, path))

    def restore_as_draft(self):
        self.ensure_one()
        self.check_access('read')
        result = self.article_id.open_draft()
        draft = self.env['qing.knowledge.draft'].search([('uuid', '=', result['draft_uuid'])], limit=1)
        draft.save_draft(draft.draft_version, self.title, self.summary, self.markdown,
                         self.tag_ids.mapped('name'))
        for asset in self.asset_ids:
            asset._copy_to_draft(draft)
        return draft.uuid
