import base64
import hashlib
import io
import json
import zipfile
from uuid import uuid4

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Domain

from ..services.markdown import asset_paths, normalize_markdown
from ..services.package import (
    MAX_INTERACTIVE_ARTICLES, MAX_INTERACTIVE_EXPANDED_BYTES, build_package, parse_package,
)

ROLES = ('reader', 'editor', 'publisher', 'manager')
PUBLICATION_POLICIES = [('review', 'Review before publishing'), ('direct', 'Publishers may publish directly')]
TEMPLATES = {
    'blank': '',
    'sop': '## 目的\n\n## 适用范围\n\n## 操作步骤\n\n1. \n\n## 异常处理\n',
    'faq': '## 问题\n\n## 答案\n\n## 相关资料\n',
    'product': '## 产品概述\n\n## 型号与规格\n\n## 使用说明\n',
    'troubleshooting': '## 现象\n\n## 原因\n\n## 处理步骤\n\n## 验证\n',
    'implementation': '## 目标\n\n## 范围\n\n## 实施步骤\n\n## 验收\n',
    'policy': '## 目的\n\n## 适用范围\n\n## 规定\n\n## 生效与复核\n',
}


class KnowledgeSpace(models.Model):
    _name = 'qing.knowledge.space'
    _description = 'Knowledge Space'
    _order = 'name, id'
    _check_company_auto = True

    _uuid_unique = models.Constraint('UNIQUE(uuid)', 'Space UUID must be unique.')

    @api.model
    def _knowledge_access_domain(self):
        user = self.env.user.sudo()
        if not user._is_internal() or not user.active:
            return Domain.FALSE
        companies = list(set(self.env.companies.ids) & set(user.company_ids.ids))
        member = Domain('user_id', '=', user.id) & Domain('active', '=', True)
        return Domain('company_id', 'in', companies) & Domain('member_ids', 'any!', member)

    def _compute_accessible(self):
        allowed = set(self.sudo().search(self._knowledge_access_domain() & Domain('id', 'in', self.ids)).ids)
        for space in self:
            space.accessible = space.id in allowed

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

    def _lock_policy(self):
        self.ensure_one()
        self.env.cr.execute('SELECT id FROM qing_knowledge_space WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset(['policy_epoch', 'publication_policy'])

    def _member_role(self, user=None):
        self.ensure_one()
        user = user or self.env.user
        if not user.active or not user._is_internal() or self.company_id not in user.company_ids or self.company_id not in self.env.companies:
            return False
        domain = [('space_id', '=', self.id), ('user_id', '=', user.id), ('active', '=', True)]
        member = self.env['qing.knowledge.member'].sudo().search(domain, limit=1)
        return member.role or False

    def _require_role(self, roles):
        self.ensure_one()
        if self._member_role() not in roles:
            raise AccessError(self.env._('You do not have the required role in this space.'))

    def _bump_policy(self):
        self.ensure_one()
        self.sudo().write({'policy_epoch': self.policy_epoch + 1})
        self.env['qing.knowledge.event'].sudo().create({
            'space_id': self.id, 'actor_id': self.env.uid,
            'kind': 'policy', 'operation_uuid': str(uuid4()),
            'policy_epoch': self.policy_epoch,
        })

    accessible = fields.Boolean(compute='_compute_accessible', search='_search_accessible', compute_sudo=False)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    member_ids = fields.One2many('qing.knowledge.member', 'space_id', string='Members')
    name = fields.Char(required=True)
    policy_epoch = fields.Integer(default=1, readonly=True)
    publication_policy = fields.Selection(PUBLICATION_POLICIES, default='review', required=True)
    uuid = fields.Char(required=True, default=lambda self: str(uuid4()), readonly=True, index=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.user._is_internal():
            raise AccessError(self.env._('Only internal users may create spaces.'))
        for vals in vals_list:
            company = self.env['res.company'].browse(vals.get('company_id') or self.env.company.id)
            if company not in self.env.companies or company not in self.env.user.company_ids:
                raise AccessError(self.env._('Choose one of your enabled companies.'))
            if set(vals) - {'name', 'company_id', 'active', 'uuid', 'publication_policy'}:
                raise AccessError(self.env._('Unsupported space creation field.'))
            # Form defaults may echo the displayed UUID; identity is always server-issued.
            vals.pop('uuid', None)
        spaces = super().create(vals_list)
        for space in spaces:
            self.env['qing.knowledge.member'].sudo().create({
                'space_id': space.id, 'user_id': self.env.user.id, 'role': 'manager',
            })
        return spaces

    def write(self, vals):
        if not self.env.su:
            for space in self:
                space._lock_policy()
                space._require_role(('manager',))
            if set(vals) - {'name', 'active', 'publication_policy'}:
                raise AccessError(self.env._('Use the space management actions.'))
        result = super().write(vals)
        if 'publication_policy' in vals:
            for space in self:
                space._bump_policy()
        return result

    def unlink(self):
        raise AccessError(self.env._('Archive a space instead of deleting it.'))

    def set_member(self, user_id, role=None, active=True):
        """Change one explicit member and invalidate the space policy atomically."""
        self.ensure_one()
        self._lock_policy()
        self._require_role(('manager',))
        user = self.env['res.users'].browse(user_id).exists()
        if not user or not user.active or not user._is_internal() or self.company_id not in user.company_ids:
            raise ValidationError(self.env._('Member must be an active internal user of this company.'))
        if role not in ROLES:
            raise ValidationError(self.env._('Invalid knowledge role.'))
        Member = self.env['qing.knowledge.member'].sudo()
        member = Member.search([('space_id', '=', self.id), ('user_id', '=', user.id)], limit=1)
        if member and member.role == 'manager' and (role != 'manager' or not active):
            other = Member.search_count([('space_id', '=', self.id), ('role', '=', 'manager'), ('active', '=', True), ('id', '!=', member.id)])
            if not other:
                raise ValidationError(self.env._('Each space needs an active manager.'))
        values = {'role': role, 'active': bool(active)}
        if member:
            member.write(values)
        else:
            values.update(space_id=self.id, user_id=user.id)
            member = Member.create(values)
        self._bump_policy()
        return member.id

    def create_article(self, title, parent_id=None, template='blank'):
        self.ensure_one()
        self._lock_policy()
        self._require_role(('editor', 'publisher', 'manager'))
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 255:
            raise ValidationError(self.env._('Enter a valid article title.'))
        title = title.strip()
        if template not in TEMPLATES:
            raise ValidationError(self.env._('Unknown knowledge template.'))
        parent = self.env['qing.knowledge.article'].browse(parent_id).exists() if parent_id else False
        if parent and (parent.space_id != self or not parent._can_edit()):
            raise AccessError(self.env._('Parent article is not editable in this space.'))
        article = self.env['qing.knowledge.article'].sudo().create({
            'space_id': self.id, 'company_id': self.company_id.id,
            'owner_id': self.env.user.id, 'parent_id': parent.id if parent else False,
        })
        ancestors = parent.ancestor_ids | parent if parent else self.env['qing.knowledge.article']
        article.sudo().write({'ancestor_ids': [fields.Command.set((ancestors | article).ids)]})
        draft = self.env['qing.knowledge.draft'].sudo().create({
            'article_id': article.id, 'author_id': self.env.user.id, 'title': title,
            'markdown': TEMPLATES[template],
        })
        return {'article_uuid': article.uuid, 'draft_uuid': draft.uuid, 'draft_version': draft.draft_version}

    def _resolve_tags(self, names):
        """Call only inside the policy lock after checking an editor role."""
        self.ensure_one()
        if not isinstance(names, list) or len(names) > 20:
            raise ValidationError(self.env._('Choose at most 20 tags.'))
        tags = self.env['qing.knowledge.tag'].sudo()
        seen = set()
        for raw in names:
            if not isinstance(raw, str):
                raise ValidationError(self.env._('Invalid tag name.'))
            name = ' '.join(raw.split())
            normalized = name.casefold()
            if not name or len(name) > 64:
                raise ValidationError(self.env._('Tag names must contain 1–64 characters.'))
            if normalized in seen:
                continue
            seen.add(normalized)
            tag = self.env['qing.knowledge.tag'].sudo().search([
                ('space_id', '=', self.id), ('normalized_name', '=', normalized),
            ], limit=1)
            if not tag:
                tag = self.env['qing.knowledge.tag'].sudo().create({
                    'space_id': self.id, 'name': name, 'normalized_name': normalized,
                })
            tags |= tag
        return tags

    def import_package(self, encoded_zip, operation_uuid):
        """Import a bounded ZIP into new personal drafts, atomically and idempotently."""
        self.ensure_one()
        self._require_role(('editor', 'publisher', 'manager'))
        if not operation_uuid or len(operation_uuid) > 64:
            raise ValidationError(self.env._('A valid import operation UUID is required.'))
        if not isinstance(encoded_zip, (str, bytes)) or len(encoded_zip) > 70 * 1024 * 1024:
            raise ValidationError(self.env._('ZIP encoding exceeds the upload limit.'))
        try:
            raw = base64.b64decode(encoded_zip, validate=True)
        except (ValueError, TypeError) as error:
            raise ValidationError(self.env._('Invalid ZIP encoding.')) from error
        articles, assets = parse_package(raw, max_articles=MAX_INTERACTIVE_ARTICLES,
                                         max_expanded_bytes=MAX_INTERACTIVE_EXPANDED_BYTES)
        # Resolve only identities inside this package. Never bind a source UUID
        # directly to an existing article in the target database.
        by_source = {spec['source_uuid']: spec for spec in articles if spec['source_uuid']}
        ordered, visit_state = [], {}

        def visit(spec):
            path = spec['path']
            if visit_state.get(path) == 'visiting':
                raise ValidationError(self.env._('Package article hierarchy contains a cycle.'))
            if visit_state.get(path) == 'done':
                return
            visit_state[path] = 'visiting'
            parent = by_source.get(spec['parent_uuid'])
            if parent:
                visit(parent)
            visit_state[path] = 'done'
            ordered.append(spec)

        for spec in articles:
            visit(spec)
        request_hash = hashlib.sha256(raw).hexdigest()
        self._lock_policy()
        self._require_role(('editor', 'publisher', 'manager'))
        Transfer = self.env['qing.knowledge.transfer'].sudo()
        prior = Transfer.search([('operation_uuid', '=', operation_uuid)], limit=1)
        if prior:
            if prior.space_id != self or prior.actor_id.id != self.env.uid or prior.request_hash != request_hash:
                raise ValidationError(self.env._('Import operation UUID was already used.'))
            return json.loads(prior.result_json)
        result = []
        created_by_source = {}
        Asset = self.env['qing.knowledge.asset']
        Draft = self.env['qing.knowledge.draft']
        for spec in ordered:
            created = self.create_article(spec['title'], parent_id=created_by_source.get(spec['parent_uuid']))
            if spec['source_uuid']:
                created_by_source[spec['source_uuid']] = self.env['qing.knowledge.article'].sudo().search([
                    ('uuid', '=', created['article_uuid']),
                ], limit=1).id
            draft = Draft.search([('uuid', '=', created['draft_uuid'])], limit=1)
            for path in asset_paths(spec['markdown']):
                raw_asset = assets.get(path)
                if raw_asset is None:
                    raise ValidationError(self.env._('Package is missing a referenced asset.'))
                uploaded = Asset.upload_to_draft(draft.uuid, path.rsplit('/', 1)[-1], base64.b64encode(raw_asset))
                if uploaded['path'] != path:
                    raise ValidationError(self.env._('Package asset checksum does not match its path.'))
            draft.save_draft(draft.draft_version, spec['title'], '', spec['markdown'], spec['tags'])
            result.append({'path': spec['path'], 'article_uuid': created['article_uuid']})
        Transfer.create({'space_id': self.id, 'actor_id': self.env.uid,
                         'operation_uuid': operation_uuid, 'request_hash': request_hash,
                         'result_json': json.dumps(result, ensure_ascii=False)})
        return result

    def import_markdown(self, title, source, operation_uuid):
        self.ensure_one()
        if not isinstance(title, str) or not title or len(title) > 255:
            raise ValidationError(self.env._('Enter a valid article title.'))
        source = normalize_markdown(source)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as package:
            article_info = zipfile.ZipInfo('article.md', (1980, 1, 1, 0, 0, 0))
            manifest_info = zipfile.ZipInfo('manifest.json', (1980, 1, 1, 0, 0, 0))
            article_info.compress_type = zipfile.ZIP_DEFLATED
            manifest_info.compress_type = zipfile.ZIP_DEFLATED
            package.writestr(article_info, source.encode('utf-8'))
            package.writestr(manifest_info, json.dumps({
                'schema': 'qing-knowledge-package/1',
                'articles': [{'path': 'article.md', 'title': title}],
            }, sort_keys=True))
        return self.import_package(base64.b64encode(buffer.getvalue()), operation_uuid)

    def export_package(self, article_uuids):
        """Export the caller's current visible published revisions as a Markdown ZIP."""
        self.ensure_one()
        self._require_role(('reader', 'editor', 'publisher', 'manager'))
        if not isinstance(article_uuids, list) or not article_uuids or len(article_uuids) > MAX_INTERACTIVE_ARTICLES or len(set(article_uuids)) != len(article_uuids):
            raise ValidationError(self.env._('Choose 1–30 unique articles for export.'))
        domain = [('uuid', 'in', article_uuids), ('space_id', '=', self.id), ('lifecycle', '=', 'published')]
        articles = self.env['qing.knowledge.article'].search(domain, order='id')
        if len(articles) != len(article_uuids):
            raise AccessError(self.env._('One or more articles are not available for export.'))
        pairs = []
        expanded = 0
        for article in articles:
            revision = article.published_revision_id
            revision.check_access('read')
            expanded += len(revision.markdown.encode('utf-8'))
            for asset in revision.asset_ids:
                asset.check_access('read')
                asset.attachment_id.check_access('read')
                expanded += asset.size
            if expanded > MAX_INTERACTIVE_EXPANDED_BYTES:
                raise ValidationError(self.env._('Export exceeds the current interactive size limit.'))
            pairs.append((article, revision))
        return base64.b64encode(build_package(pairs)).decode('ascii')

    def action_set_member(self):
        self.ensure_one()
        self._require_role(('manager',))
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Set space member'),
            'res_model': 'qing.knowledge.member.wizard',
            'view_mode': 'form', 'target': 'new',
            'context': {'default_space_id': self.id},
        }
