import base64
import hashlib
from io import BytesIO
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Domain

MAX_ASSET_BYTES = 20 * 1024 * 1024
MIMES = {'png': 'image/png', 'jpeg': 'image/jpeg', 'gif': 'image/gif', 'webp': 'image/webp', 'pdf': 'application/pdf'}
EXTENSIONS = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp', 'application/pdf': 'pdf'}


class KnowledgeAsset(models.Model):
    _name = 'qing.knowledge.asset'
    _description = 'Knowledge Revision or Draft Asset'
    _order = 'id'
    _check_company_auto = True
    _rec_name = 'logical_path'

    _unique_uuid = models.Constraint('UNIQUE(uuid)', 'Asset UUID must be unique.')
    _asset_owner = models.Constraint('CHECK((draft_id IS NULL) <> (revision_id IS NULL))', 'Asset must belong to a draft or revision.')

    @api.model
    def _knowledge_access_domain(self):
        return Domain('draft_id', 'access', 'read') | Domain('revision_id', 'access', 'read')

    def _compute_accessible(self):
        allowed = set(self.sudo().search(self._knowledge_access_domain() & Domain('id', 'in', self.ids)).ids)
        for asset in self:
            asset.accessible = asset.id in allowed

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

    def _copy_to_revision(self, revision):
        self.ensure_one()
        source = self.attachment_id.sudo()
        clone = self.sudo().create({
            'revision_id': revision.id, 'logical_path': self.logical_path,
            'checksum': self.checksum, 'mime': self.mime, 'size': self.size,
        })
        attachment = self.env['ir.attachment'].sudo().create({
            'name': source.name, 'raw': source.raw, 'mimetype': source.mimetype,
            'res_model': clone._name, 'res_id': clone.id, 'public': False,
        })
        clone.sudo().write({'attachment_id': attachment.id})
        return clone

    def _copy_to_draft(self, draft):
        self.ensure_one()
        existing = self.sudo().search([('draft_id', '=', draft.id), ('logical_path', '=', self.logical_path)], limit=1)
        if existing:
            return existing
        source = self.attachment_id.sudo()
        clone = self.sudo().create({
            'draft_id': draft.id, 'logical_path': self.logical_path,
            'checksum': self.checksum, 'mime': self.mime, 'size': self.size,
        })
        attachment = self.env['ir.attachment'].sudo().create({
            'name': source.name, 'raw': source.raw, 'mimetype': source.mimetype,
            'res_model': clone._name, 'res_id': clone.id, 'public': False,
        })
        clone.sudo().write({'attachment_id': attachment.id})
        return clone

    accessible = fields.Boolean(compute='_compute_accessible', search='_search_accessible', compute_sudo=False)
    attachment_id = fields.Many2one('ir.attachment', ondelete='restrict', readonly=True)
    checksum = fields.Char(required=True, readonly=True)
    company_id = fields.Many2one('res.company', compute='_compute_company', store=True, readonly=True)
    draft_id = fields.Many2one('qing.knowledge.draft', ondelete='restrict', index=True)
    logical_path = fields.Char(required=True, readonly=True)
    mime = fields.Char(required=True, readonly=True)
    revision_id = fields.Many2one('qing.knowledge.revision', ondelete='restrict', index=True)
    size = fields.Integer(required=True, readonly=True)
    uuid = fields.Char(required=True, default=lambda self: str(uuid4()), readonly=True, index=True)

    @api.depends('draft_id.article_id.company_id', 'revision_id.article_id.company_id')
    def _compute_company(self):
        for asset in self:
            asset.company_id = (asset.draft_id.article_id or asset.revision_id.article_id).company_id

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(self.env._('Use the draft upload action.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su or set(vals) - {'attachment_id'} or any(asset.attachment_id for asset in self):
            raise AccessError(self.env._('Knowledge assets cannot be changed.'))
        return super().write(vals)

    def unlink(self):
        raise AccessError(self.env._('Knowledge assets cannot be deleted through RPC.'))

    @api.model
    def upload_to_draft(self, draft_uuid, filename, encoded_content):
        """Bind bytes to an author-owned draft; the returned path is safe in Markdown."""
        draft = self.env['qing.knowledge.draft'].search([('uuid', '=', draft_uuid)], limit=1)
        if not draft:
            raise AccessError(self.env._('Draft not found.'))
        draft.article_id.space_id._lock_policy()
        draft._require_author()
        if draft.state != 'editing':
            raise ValidationError(self.env._('Withdraw the submitted draft before uploading.'))
        try:
            raw = base64.b64decode(encoded_content, validate=True)
        except (ValueError, TypeError) as error:
            raise ValidationError(self.env._('Invalid asset encoding.')) from error
        if not raw or len(raw) > MAX_ASSET_BYTES:
            raise ValidationError(self.env._('Asset must be between 1 byte and 20 MiB.'))
        mime = False
        if raw.startswith(b'%PDF-'):
            mime = 'application/pdf'
        else:
            try:
                with Image.open(BytesIO(raw)) as image:
                    if image.width * image.height > 50_000_000:
                        raise ValidationError(self.env._('Image dimensions exceed the limit.'))
                    image.verify()
                    mime = MIMES.get(image.format.lower())
            except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
                pass
        if not mime:
            raise ValidationError(self.env._('Only PNG, JPEG, WebP, GIF and PDF files are supported.'))
        checksum = hashlib.sha256(raw).hexdigest()
        path = 'assets/%s.%s' % (checksum, EXTENSIONS[mime])
        existing = self.sudo().search([('draft_id', '=', draft.id), ('logical_path', '=', path)], limit=1)
        if existing:
            return {'path': path, 'asset_uuid': existing.uuid}
        asset = self.sudo().create({'draft_id': draft.id, 'logical_path': path,
                                    'checksum': checksum, 'mime': mime, 'size': len(raw)})
        attachment = self.env['ir.attachment'].sudo().create({
            'name': filename, 'raw': raw, 'mimetype': mime,
            'res_model': self._name, 'res_id': asset.id, 'public': False,
        })
        asset.sudo().write({'attachment_id': attachment.id})
        return {'path': path, 'asset_uuid': asset.uuid}
