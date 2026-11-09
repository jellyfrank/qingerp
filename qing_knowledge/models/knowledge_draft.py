from uuid import uuid4

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Domain

from ..services.markdown import asset_paths, content_hash, normalize_markdown, render_markdown
from ..services.search import projection

DRAFT_STATES = [('editing', 'Editing'), ('submitted', 'Submitted'), ('merged', 'Published'), ('discarded', 'Discarded')]


class KnowledgeDraft(models.Model):
    _name = 'qing.knowledge.draft'
    _description = 'Personal Knowledge Draft'
    _order = 'write_date desc'
    _check_company_auto = True
    _rec_name = 'title'

    _unique_draft = models.Constraint('UNIQUE(article_id, author_id)', 'A user has one working copy per article.')
    _unique_uuid = models.Constraint('UNIQUE(uuid)', 'Draft UUID must be unique.')

    @api.model
    def _knowledge_access_domain(self):
        user = self.env.user.sudo()
        if not user.active or not user._is_internal():
            return Domain.FALSE
        article = Domain('article_id', 'access', 'read')
        member = Domain('user_id', '=', user.id) & Domain('active', '=', True)
        writer = Domain('article_id.space_id.member_ids', 'any!', member & Domain('role', 'in', ['editor', 'publisher', 'manager']))
        own = Domain('author_id', '=', user.id) & writer
        reviewer = Domain('state', '=', 'submitted') & Domain('article_id.space_id.member_ids', 'any!', member & Domain('role', 'in', ['publisher', 'manager']))
        manager = Domain('article_id.space_id.member_ids', 'any!', member & Domain('role', '=', 'manager'))
        return article & (own | reviewer | manager)

    def _compute_accessible(self):
        allowed = set(self.sudo().search(self._knowledge_access_domain() & Domain('id', 'in', self.ids)).ids)
        for draft in self:
            draft.accessible = draft.id in allowed

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

    def _require_author(self):
        self.ensure_one()
        if self.author_id.id != self.env.uid:
            raise AccessError(self.env._('Only the author may edit this draft.'))
        self.article_id._require_role(('editor', 'publisher', 'manager'))

    def _check_assets(self):
        self.ensure_one()
        expected = asset_paths(self.markdown)
        available = set(self.asset_ids.mapped('logical_path'))
        if expected - available:
            raise ValidationError(self.env._('The draft references assets that have not been uploaded.'))

    def _content_hash(self):
        self.ensure_one()
        referenced = asset_paths(self.markdown)
        assets = [(a.logical_path, a.checksum) for a in self.asset_ids if a.logical_path in referenced]
        return content_hash(self.title, self.summary, self.markdown, assets,
                            self.tag_ids.mapped('normalized_name'))

    @api.depends('tag_ids.name')
    def _compute_tag_names(self):
        for draft in self:
            draft.tag_names = ', '.join(draft.tag_ids.mapped('name'))

    accessible = fields.Boolean(compute='_compute_accessible', search='_search_accessible', compute_sudo=False)
    article_id = fields.Many2one('qing.knowledge.article', required=True, ondelete='cascade', index=True)
    asset_ids = fields.One2many('qing.knowledge.asset', 'draft_id', string='Assets')
    author_id = fields.Many2one('res.users', required=True, index=True)
    base_revision_id = fields.Many2one('qing.knowledge.revision', ondelete='set null')
    company_id = fields.Many2one('res.company', related='article_id.company_id', store=True, readonly=True)
    content_hash = fields.Char(readonly=True)
    draft_version = fields.Integer(default=1, required=True)
    markdown = fields.Text(default='')
    state = fields.Selection(DRAFT_STATES, default='editing', required=True)
    submitted_at = fields.Datetime(readonly=True)
    summary = fields.Text()
    tag_ids = fields.Many2many('qing.knowledge.tag', 'qing_knowledge_draft_tag_rel', 'draft_id', 'tag_id')
    tag_names = fields.Char(compute='_compute_tag_names', compute_sudo=False)
    title = fields.Char(required=True)
    uuid = fields.Char(required=True, default=lambda self: str(uuid4()), readonly=True, index=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(self.env._('Open an article draft instead.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise AccessError(self.env._('Use save, submit or withdraw actions.'))
        return super().write(vals)

    def unlink(self):
        raise AccessError(self.env._('Discard a draft instead of deleting it.'))

    def save_draft(self, expected_version, title, summary, markdown, tag_names=None):
        """Save only the author's current editing version after a policy lock."""
        self.ensure_one()
        self.article_id.space_id._lock_policy()
        self.invalidate_recordset(['draft_version', 'state'])
        self._require_author()
        if self.state != 'editing' or self.draft_version != expected_version:
            raise ValidationError(self.env._('DRAFT_CONFLICT: Reload the latest draft before saving.'))
        if not title or len(title) > 255:
            raise ValidationError(self.env._('Enter a title of at most 255 characters.'))
        markdown = normalize_markdown(markdown)
        values = {
            'title': title, 'summary': summary or '', 'markdown': markdown,
            'content_hash': False, 'draft_version': self.draft_version + 1,
        }
        if tag_names is not None:
            if not isinstance(tag_names, list):
                raise ValidationError(self.env._('Tag names must be a list.'))
            tags = self.article_id.space_id._resolve_tags(tag_names)
            values['tag_ids'] = [fields.Command.set(tags.ids)]
        self.sudo().write(values)
        return {'draft_uuid': self.uuid, 'draft_version': self.draft_version,
                'content_hash': self._content_hash()}

    def submit_draft(self, expected_version):
        self.ensure_one()
        self.article_id.space_id._lock_policy()
        self.invalidate_recordset(['draft_version', 'state'])
        self._require_author()
        if self.state != 'editing' or self.draft_version != expected_version:
            raise ValidationError(self.env._('DRAFT_CONFLICT: Reload the latest draft before submitting.'))
        self._check_assets()
        digest = self._content_hash()
        self.sudo().write({'state': 'submitted', 'submitted_at': fields.Datetime.now(),
                           'content_hash': digest, 'draft_version': self.draft_version + 1})
        return {'draft_uuid': self.uuid, 'draft_version': self.draft_version, 'content_hash': digest}

    def withdraw_draft(self, expected_version):
        self.ensure_one()
        self.article_id.space_id._lock_policy()
        self.invalidate_recordset(['draft_version', 'state'])
        if self.author_id.id != self.env.uid and self.article_id._role() != 'manager':
            raise AccessError(self.env._('Only the author or space manager may withdraw a draft.'))
        if self.state != 'submitted' or self.draft_version != expected_version:
            raise ValidationError(self.env._('DRAFT_CONFLICT: This submission has changed.'))
        self.sudo().write({'state': 'editing', 'content_hash': False,
                           'submitted_at': False, 'draft_version': self.draft_version + 1})
        return self.draft_version

    def publish_draft(self, expected_version, expected_published_revision_uuid, expected_content_hash, operation_uuid):
        """Switch the current revision exactly once under the common policy lock."""
        self.ensure_one()
        if not operation_uuid or len(operation_uuid) > 64:
            raise ValidationError(self.env._('A valid operation UUID is required.'))
        article = self.article_id
        space = article.space_id
        space._lock_policy()
        self.invalidate_recordset(['draft_version', 'state', 'content_hash', 'base_revision_id'])
        article.invalidate_recordset(['published_revision_id', 'lifecycle'])
        article._require_role(('publisher', 'manager'))
        Event = self.env['qing.knowledge.event'].sudo()
        prior = Event.search([('operation_uuid', '=', operation_uuid)], limit=1)
        if prior:
            if prior.draft_id != self or prior.request_hash != expected_content_hash:
                raise ValidationError(self.env._('Operation UUID was already used for another request.'))
            return {'revision_uuid': prior.revision_id.uuid, 'number': prior.revision_id.number}
        current = article.published_revision_id
        if (current.uuid or False) != (expected_published_revision_uuid or False) or self.draft_version != expected_version:
            raise ValidationError(self.env._('REVISION_CONFLICT: The article or submission has changed.'))
        if (self.base_revision_id.uuid or False) != (current.uuid or False):
            raise ValidationError(self.env._('REVISION_CONFLICT: Review the latest published version first.'))
        if self.state != 'submitted' or self.content_hash != expected_content_hash or self._content_hash() != expected_content_hash:
            raise ValidationError(self.env._('DRAFT_CONFLICT: Submission content has changed.'))
        self._check_assets()
        Revision = self.env['qing.knowledge.revision'].sudo()
        revision = Revision.create({
            'article_id': article.id, 'number': current.number + 1 if current else 1,
            'title': self.title, 'summary': self.summary, 'markdown': self.markdown,
            'tag_ids': [fields.Command.set(self.tag_ids.ids)],
            'content_hash': self.content_hash, 'author_id': self.author_id.id,
            'published_by': self.env.uid, 'published_at': fields.Datetime.now(),
            'parent_uuid': article.parent_id.uuid or False,
        })
        referenced = asset_paths(self.markdown)
        for asset in self.asset_ids.filtered(lambda item: item.logical_path in referenced):
            asset._copy_to_revision(revision)
        if current:
            current.sudo().write({'is_current': False})
        revision.sudo().write({'is_current': True})
        article.sudo().write({
            'published_revision_id': revision.id, 'lifecycle': 'published',
            'search_terms': projection(revision.title, revision.summary, revision.markdown,
                                       revision.tag_ids.mapped('name')),
        })
        self.sudo().write({'state': 'merged'})
        Event.create({
            'article_id': article.id, 'draft_id': self.id, 'revision_id': revision.id,
            'space_id': space.id,
            'operation_uuid': operation_uuid, 'request_hash': expected_content_hash,
            'actor_id': self.env.uid, 'kind': 'published', 'policy_epoch': space.policy_epoch,
        })
        return {'revision_uuid': revision.uuid, 'number': revision.number}

    def publish_direct(self, expected_version, expected_published_revision_uuid, expected_content_hash, operation_uuid):
        """Publish the author's saved draft atomically, only in a direct-publish space."""
        self.ensure_one()
        space = self.article_id.space_id
        space._lock_policy()
        self._require_author()
        self.article_id._require_role(('publisher', 'manager'))
        if not operation_uuid or len(operation_uuid) > 64:
            raise ValidationError(self.env._('A valid operation UUID is required.'))
        Event = self.env['qing.knowledge.event'].sudo()
        prior = Event.search([('operation_uuid', '=', operation_uuid)], limit=1)
        if prior:
            if prior.draft_id != self or prior.actor_id.id != self.env.uid or prior.request_hash != expected_content_hash:
                raise ValidationError(self.env._('Operation UUID was already used for another request.'))
            return {'revision_uuid': prior.revision_id.uuid, 'number': prior.revision_id.number}
        if space.publication_policy != 'direct':
            raise AccessError(self.env._('This space requires review before publishing.'))
        self.invalidate_recordset(['draft_version', 'state', 'base_revision_id', 'markdown', 'title', 'summary', 'tag_ids', 'asset_ids'])
        if self.state != 'editing' or self.draft_version != expected_version or self._content_hash() != expected_content_hash:
            raise ValidationError(self.env._('DRAFT_CONFLICT: The saved draft has changed.'))
        if (self.base_revision_id.uuid or False) != (expected_published_revision_uuid or False):
            raise ValidationError(self.env._('REVISION_CONFLICT: Review the latest published version first.'))
        submitted = self.submit_draft(expected_version)
        return self.publish_draft(submitted['draft_version'], expected_published_revision_uuid,
                                  expected_content_hash, operation_uuid)

    @api.model
    def list_my_drafts(self, query='', space_uuid=None, limit=40):
        """Search only the caller's accessible working copies, including unpublished pages."""
        if not isinstance(query, str) or len(query) > 100 or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValidationError(self.env._('Search parameters exceed the allowed limits.'))
        domain = Domain('author_id', '=', self.env.uid) & Domain('state', 'in', ['editing', 'submitted'])
        domain &= Domain('article_id.lifecycle', '!=', 'trashed')
        if space_uuid:
            domain &= Domain('article_id.space_id.uuid', '=', space_uuid)
        query = query.strip()
        if query:
            domain &= (Domain('title', 'ilike', query) | Domain('summary', 'ilike', query)
                       | Domain('markdown', 'ilike', query))
        drafts = self.search(domain, limit=limit, order='write_date desc, id desc')
        return [{
            'id': draft.id, 'uuid': draft.uuid, 'article_id': draft.article_id.id,
            'title': draft.title, 'summary': (draft.summary or '')[:240],
            'state': draft.state, 'space_id': draft.article_id.space_id.id,
            'space_name': draft.article_id.space_id.name,
            'updated_at': fields.Datetime.to_string(draft.write_date),
        } for draft in drafts]

    def preview_markdown(self, source):
        self.ensure_one()
        self.check_access('read')
        if self.author_id.id != self.env.uid and self.state != 'submitted':
            raise AccessError(self.env._('Only the author may preview an editing draft.'))
        return render_markdown(source, lambda path: '/qing/knowledge/draft_asset/%s/%s' % (self.uuid, path))
