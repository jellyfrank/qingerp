from uuid import uuid4
from difflib import unified_diff

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Domain
from odoo.tools import SQL

from ..services.search import projection, query_expression

LIFECYCLES = [('unpublished', 'Unpublished'), ('published', 'Published'), ('archived', 'Archived'), ('trashed', 'Trash')]
ACCESS_MODES = [('inherit', 'Space members'), ('restricted', 'Selected members')]


class KnowledgeArticle(models.Model):
    _name = 'qing.knowledge.article'
    _description = 'Knowledge Article'
    _order = 'sequence, id'
    _check_company_auto = True
    _rec_name = 'name'

    _unique_uuid = models.Constraint('UNIQUE(uuid)', 'Article UUID must be unique.')

    @api.model
    def _knowledge_access_domain(self):
        user = self.env.user.sudo()
        if not user.active or not user._is_internal():
            return Domain.FALSE
        member = Domain('user_id', '=', user.id) & Domain('active', '=', True)
        writer = Domain('space_id.member_ids', 'any!', member & Domain('role', 'in', ['editor', 'publisher', 'manager']))
        restricted = Domain('access_mode', '=', 'inherit') | Domain('restricted_member_ids', 'any!', member)
        manager = Domain('space_id.member_ids', 'any!', member & Domain('role', '=', 'manager'))
        lineage_block = Domain('lifecycle', '!=', 'published') | (
            Domain('access_mode', '=', 'restricted') & ~Domain('restricted_member_ids', 'any!', member)
        )
        published = Domain('lifecycle', '=', 'published') & ~Domain('ancestor_ids', 'any!', lineage_block)
        own_draft = writer & Domain('draft_ids', 'any!', Domain('author_id', '=', user.id) & Domain('state', 'in', ['editing', 'submitted']))
        review = Domain('draft_ids', 'any!', Domain('state', '=', 'submitted')) & Domain(
            'space_id.member_ids', 'any!', member & Domain('role', '=', 'publisher'))
        return Domain('space_id', 'access', 'read') & (manager | (restricted & (published | own_draft | review)))

    def _compute_accessible(self):
        allowed = set(self.sudo().search(self._knowledge_access_domain() & Domain('id', 'in', self.ids)).ids)
        for article in self:
            article.accessible = article.id in allowed

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

    def _role(self):
        self.ensure_one()
        return self.space_id._member_role()

    def _can_edit(self):
        self.ensure_one()
        if self._role() not in ('editor', 'publisher', 'manager'):
            return False
        return bool(self.sudo().search(self._knowledge_access_domain() & Domain('id', '=', self.id), limit=1))

    def _require_role(self, roles):
        self.ensure_one()
        if self._role() not in roles or not self.sudo().search(self._knowledge_access_domain() & Domain('id', '=', self.id), limit=1):
            raise AccessError(self.env._('You cannot change this article.'))

    def _compute_name(self):
        for article in self:
            if article.published_revision_id and article.lifecycle in ('published', 'trashed'):
                article.name = article.published_revision_id.title
            else:
                own = article.draft_ids.filtered(lambda d: d.author_id.id == self.env.uid)[:1]
                article.name = own.title if own else article.uuid

    accessible = fields.Boolean(compute='_compute_accessible', search='_search_accessible', compute_sudo=False)
    access_mode = fields.Selection(ACCESS_MODES, default='inherit', required=True)
    ancestor_ids = fields.Many2many('qing.knowledge.article', 'qing_knowledge_ancestor_rel', 'article_id', 'ancestor_id', readonly=True)
    company_id = fields.Many2one('res.company', required=True, index=True)
    draft_ids = fields.One2many('qing.knowledge.draft', 'article_id', string='Personal drafts')
    lifecycle = fields.Selection(LIFECYCLES, default='unpublished', required=True, index=True)
    name = fields.Char(compute='_compute_name', compute_sudo=False)
    owner_id = fields.Many2one('res.users', required=True, index=True)
    parent_id = fields.Many2one('qing.knowledge.article', ondelete='restrict', index=True)
    policy_epoch = fields.Integer(default=1)
    published_revision_id = fields.Many2one('qing.knowledge.revision', readonly=True, ondelete='restrict')
    restricted_member_ids = fields.Many2many('qing.knowledge.member', 'qing_knowledge_restricted_rel', 'article_id', 'member_id')
    sequence = fields.Integer(default=10)
    search_terms = fields.Text(readonly=True, copy=False)
    space_id = fields.Many2one('qing.knowledge.space', required=True, ondelete='restrict', index=True)
    uuid = fields.Char(required=True, default=lambda self: str(uuid4()), readonly=True, index=True)

    def init(self):
        self.env.cr.execute("""
            CREATE INDEX IF NOT EXISTS qing_knowledge_article_search_gin
            ON qing_knowledge_article USING GIN
            (to_tsvector('simple', coalesce(search_terms, '')))
        """)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(self.env._('Use the create article action.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise AccessError(self.env._('Use an article management action.'))
        return super().write(vals)

    def unlink(self):
        raise AccessError(self.env._('Move articles to trash instead of deleting them.'))

    def open_draft(self):
        self.ensure_one()
        self.space_id._lock_policy()
        self._require_role(('editor', 'publisher', 'manager'))
        Draft = self.env['qing.knowledge.draft'].sudo()
        draft = Draft.search([('article_id', '=', self.id), ('author_id', '=', self.env.uid)], limit=1)
        if not draft:
            base = self.published_revision_id
            draft = Draft.create({
                'article_id': self.id, 'author_id': self.env.uid,
                'base_revision_id': base.id if base else False,
                'title': base.title if base else '',
                'summary': base.summary if base else '',
                'markdown': base.markdown if base else '',
                'tag_ids': [fields.Command.set(base.tag_ids.ids if base else [])],
            })
            if base:
                for asset in base.asset_ids:
                    asset._copy_to_draft(draft)
        elif draft.state in ('merged', 'discarded'):
            base = self.published_revision_id
            draft.write({
                'state': 'editing', 'base_revision_id': base.id if base else False,
                'draft_version': draft.draft_version + 1,
                'title': base.title if base else '', 'summary': base.summary if base else '',
                'markdown': base.markdown if base else '',
                'tag_ids': [fields.Command.set(base.tag_ids.ids if base else [])],
            })
            if base:
                for asset in base.asset_ids:
                    asset._copy_to_draft(draft)
        return {'draft_uuid': draft.uuid, 'draft_version': draft.draft_version,
                'base_revision_uuid': draft.base_revision_id.uuid or False,
                'content_hash': draft._content_hash()}

    def set_restriction(self, member_ids, restricted=True):
        self.ensure_one()
        self.space_id._lock_policy()
        self._require_role(('manager',))
        members = self.env['qing.knowledge.member'].sudo().browse(member_ids).exists()
        if len(members) != len(set(member_ids)) or any(m.space_id != self.space_id or not m.active for m in members):
            raise ValidationError(self.env._('Restricted readers must be active members of this space.'))
        self.sudo().write({'access_mode': 'restricted' if restricted else 'inherit',
                           'restricted_member_ids': [fields.Command.set(members.ids)],
                           'policy_epoch': self.policy_epoch + 1})
        self.space_id._bump_policy()

    def move_to(self, parent_id=None):
        self.ensure_one()
        self.space_id._lock_policy()
        self._require_role(('manager',))
        parent = self.browse(parent_id).exists() if parent_id else False
        if parent_id and not parent:
            raise ValidationError(self.env._('Parent article does not exist.'))
        if parent and (parent == self or parent.space_id != self.space_id or self in parent.ancestor_ids):
            raise ValidationError(self.env._('A page cannot move across spaces or into its descendants.'))
        descendants = self.sudo().search([('ancestor_ids', 'in', self.id)], order='id')
        old_ancestors = self.ancestor_ids - self
        new_ancestors = (parent.ancestor_ids | parent) if parent else self.browse()
        self.sudo().write({'parent_id': parent.id if parent else False})
        for page in descendants:
            updated = (page.ancestor_ids - old_ancestors) | new_ancestors
            page.sudo().write({'ancestor_ids': [fields.Command.set(updated.ids)], 'policy_epoch': page.policy_epoch + 1})
        self.space_id._bump_policy()

    def set_lifecycle(self, lifecycle):
        self.ensure_one()
        transitions = {
            'unpublished': {'archived', 'trashed'},
            'published': {'unpublished', 'archived', 'trashed'},
            'archived': {'unpublished', 'trashed'},
            'trashed': {'unpublished'},
        }
        previous = self.lifecycle
        if lifecycle not in transitions.get(previous, set()):
            raise ValidationError(self.env._('Invalid article state transition.'))
        self.space_id._lock_policy()
        self._require_role(('publisher', 'manager') if previous == 'published' and lifecycle == 'unpublished' else ('manager',))
        target = 'archived' if previous == 'trashed' and lifecycle == 'unpublished' else lifecycle
        self.sudo().write({'lifecycle': target, 'policy_epoch': self.policy_epoch + 1})
        self.space_id._bump_policy()
        self.env['qing.knowledge.event'].sudo().create({
            'space_id': self.space_id.id, 'article_id': self.id,
            'revision_id': self.published_revision_id.id,
            'actor_id': self.env.uid,
            'kind': 'restored' if previous in ('archived', 'trashed') and lifecycle == 'unpublished' else (
                'withdrawn' if target == 'unpublished' else target
            ),
            'operation_uuid': str(uuid4()), 'policy_epoch': self.space_id.policy_epoch,
        })

    def get_article(self):
        """Return only the current published content and caller capabilities."""
        self.ensure_one()
        self.check_access('read')
        revision = self.published_revision_id
        if not revision or self.lifecycle != 'published':
            data = {'uuid': self.uuid, 'published': False,
                    'can_edit': self.lifecycle != 'trashed' and self._can_edit(),
                    'can_publish': self._role() in ('publisher', 'manager'),
                    'can_manage': self._role() == 'manager', 'lifecycle': self.lifecycle,
                    'title': revision.title if revision else self.name,
                    'space_id': self.space_id.id, 'space_name': self.space_id.name,
                    'publication_policy': self.space_id.publication_policy,
                    'revision_uuid': revision.uuid if revision else False,
                    'parent_id': self.parent_id.id or False}
            if data['can_manage']:
                data.update(access_mode=self.access_mode, restricted_member_ids=self.restricted_member_ids.ids)
            return data
        revision.check_access('read')
        bookmark = self.env['qing.knowledge.bookmark'].sudo().search([
            ('article_id', '=', self.id), ('user_id', '=', self.env.uid),
        ], limit=1)
        if not bookmark:
            bookmark = self.env['qing.knowledge.bookmark'].sudo().create({
                'article_id': self.id, 'user_id': self.env.uid,
                'last_opened_at': fields.Datetime.now(),
            })
        elif not bookmark.last_opened_at or (fields.Datetime.now() - bookmark.last_opened_at).total_seconds() >= 900:
            bookmark.sudo().write({'last_opened_at': fields.Datetime.now()})
        data = {
            'uuid': self.uuid, 'published': True, 'title': revision.title,
            'summary': revision.summary, 'markdown': revision.markdown,
            'html': revision.rendered_html(), 'revision_uuid': revision.uuid,
            'number': revision.number, 'can_edit': self._can_edit(),
            'space_id': self.space_id.id, 'space_name': self.space_id.name,
            'publication_policy': self.space_id.publication_policy,
            'author': revision.author_id.name,
            'published_at': fields.Datetime.to_string(revision.published_at),
            'can_publish': self._role() in ('publisher', 'manager'),
            'can_manage': self._role() == 'manager', 'lifecycle': self.lifecycle,
            'is_favorite': bookmark.is_favorite,
            'tags': revision.tag_ids.mapped('name'),
            'parent_id': self.parent_id.id or False,
        }
        if data['can_manage']:
            data.update(access_mode=self.access_mode, restricted_member_ids=self.restricted_member_ids.ids)
        return data

    def toggle_favorite(self):
        self.ensure_one()
        self.check_access('read')
        if self.lifecycle != 'published':
            raise AccessError(self.env._('Only published articles can be favorited.'))
        bookmark = self.env['qing.knowledge.bookmark'].sudo().search([
            ('article_id', '=', self.id), ('user_id', '=', self.env.uid),
        ], limit=1)
        if bookmark:
            bookmark.sudo().write({'is_favorite': not bookmark.is_favorite})
            return bookmark.is_favorite
        self.env['qing.knowledge.bookmark'].sudo().create({
            'article_id': self.id, 'user_id': self.env.uid,
            'is_favorite': True, 'last_opened_at': fields.Datetime.now(),
        })
        return True

    def get_revisions(self):
        self.ensure_one()
        self.check_access('read')
        revisions = self.env['qing.knowledge.revision'].search([
            ('article_id', '=', self.id),
        ], order='number desc', limit=100)
        return [{
            'id': revision.id, 'uuid': revision.uuid, 'number': revision.number,
            'title': revision.title, 'published_at': revision.published_at,
            'author': revision.author_id.name,
        } for revision in revisions]

    def compare_revisions(self, left_uuid, right_uuid):
        self.ensure_one()
        self.check_access('read')
        revisions = self.env['qing.knowledge.revision'].search([
            ('article_id', '=', self.id), ('uuid', 'in', [left_uuid, right_uuid]),
        ])
        by_uuid = {revision.uuid: revision for revision in revisions}
        if left_uuid not in by_uuid or right_uuid not in by_uuid:
            raise AccessError(self.env._('One or more revisions are unavailable.'))
        left, right = by_uuid[left_uuid], by_uuid[right_uuid]
        return ''.join(unified_diff(
            left.markdown.splitlines(keepends=True), right.markdown.splitlines(keepends=True),
            fromfile='revision-%s' % left.number, tofile='revision-%s' % right.number,
        ))[:200_000]

    def compare_submission(self, draft_id):
        self.ensure_one()
        self.check_access('read')
        draft = self.env['qing.knowledge.draft'].browse(draft_id).exists()
        if not draft or draft.article_id != self or draft.state != 'submitted':
            raise AccessError(self.env._('Submitted draft is unavailable.'))
        draft.check_access('read')
        previous = self.published_revision_id
        if previous:
            previous.check_access('read')
        return ''.join(unified_diff(
            (previous.markdown or '').splitlines(keepends=True) if previous else [],
            draft.markdown.splitlines(keepends=True),
            fromfile='current-published', tofile='submitted-draft',
        ))[:200_000]

    @api.model
    def search_articles(self, query, space_uuid=None, limit=40, offset=0):
        """Bounded lexical search over currently published, authorized articles."""
        query = (query or '').strip()
        if len(query) > 100 or limit < 1 or limit > 100 or offset < 0:
            raise ValidationError(self.env._('Search parameters exceed the allowed limits.'))
        domain = Domain('lifecycle', '=', 'published')
        if space_uuid:
            domain &= Domain('space_id.uuid', '=', space_uuid)
        if not query:
            articles = self.search(domain, limit=limit, offset=offset)
        else:
            expression = query_expression(query)
            if not expression:
                return []
            # _search includes the model's access domain before SQL ranking/pagination.
            allowed = self._search(domain).select()
            ranking = SQL("""
                SELECT article.id FROM (%s) AS allowed
                JOIN qing_knowledge_article AS article ON article.id = allowed.id
                JOIN qing_knowledge_revision AS revision
                  ON revision.id = article.published_revision_id
                WHERE to_tsvector('simple', coalesce(article.search_terms, ''))
                      @@ to_tsquery('simple', %s)
                ORDER BY
                    CASE WHEN lower(revision.title) = lower(%s) THEN 100 ELSE 0 END
                    + ts_rank_cd(to_tsvector('simple', coalesce(article.search_terms, '')),
                                 to_tsquery('simple', %s)) DESC,
                    article.id DESC
                LIMIT %s OFFSET %s
            """, allowed, expression, query, expression, limit, offset)
            articles = self.browse([row[0] for row in self.env.execute_query(ranking)])
        return [{
            'id': article.id, 'uuid': article.uuid, 'title': article.published_revision_id.title,
            'summary': (article.published_revision_id.summary or '')[:240],
            'space_uuid': article.space_id.uuid, 'space_id': article.space_id.id,
            'space_name': article.space_id.name,
        } for article in articles]

    @api.model
    def rebuild_search_projection(self, after_id=0, batch_size=100):
        """Bounded rebuild after a tokenizer upgrade; return only the next cursor."""
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._('Only system administrators can rebuild search terms.'))
        if not isinstance(after_id, int) or after_id < 0 or not isinstance(batch_size, int) or batch_size < 1 or batch_size > 100:
            raise ValidationError(self.env._('Invalid search rebuild batch.'))
        articles = self.sudo().search([('id', '>', after_id)], order='id', limit=batch_size)
        for article in articles:
            self.env.cr.execute('SELECT id FROM qing_knowledge_article WHERE id = %s FOR UPDATE', [article.id])
            article.invalidate_recordset(['published_revision_id', 'lifecycle'])
            revision = article.published_revision_id
            terms = projection(revision.title, revision.summary, revision.markdown,
                               revision.tag_ids.mapped('name')) if revision and article.lifecycle == 'published' else ''
            article.sudo().write({'search_terms': terms})
        return {'after_id': articles[-1].id if articles else after_id, 'done': len(articles) < batch_size}
