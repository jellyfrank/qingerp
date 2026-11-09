import json
import re

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError
from ..services.cloud_api import CloudError

TEMPLATE_CATEGORIES = [('MARKETING', 'Marketing'), ('UTILITY', 'Utility'), ('AUTHENTICATION', 'Authentication')]


class WhatsappTemplate(models.Model):
    _name = 'qing.whatsapp.template'

    _description = 'WhatsApp Template'

    _inherit = ['mail.thread']

    _template_unique = models.Constraint('unique(account_id, name, language)', 'This template language exists.')

    @api.model
    def _sync_remote(self, account, item):
        domain = [('account_id', '=', account.id), ('name', '=', item['name']), ('language', '=', item['language'])]
        record = self.search(domain, limit=1)
        values = {'account_id': account.id, 'name': item['name'], 'language': item['language'],
                  'remote_uid': item['id'], 'status': item.get('status', 'UNKNOWN'),
                  'category': item.get('category', 'UTILITY'),
                  'components_json': json.dumps(item.get('components', []), ensure_ascii=False),
                  'quality': (item.get('quality_score') or {}).get('score', 'UNKNOWN')}
        if record:
            record.write(values)
            return record
        return self.create(values)

    def _prepare_payload(self, components):
        self.ensure_one()
        if self.status != 'APPROVED' or self.quality == 'RED':
            raise ValidationError(_('The template is not approved or has poor quality.'))
        if not isinstance(components, list) or any(not isinstance(c, dict) for c in components):
            raise ValidationError(_('Template parameters must be a JSON list.'))
        return {'name': self.name, 'language': {'code': self.language}, 'components': components}

    @api.constrains('name', 'components_json')
    def _check_definition(self):
        for template in self:
            if not re.fullmatch(r'[a-z0-9_]+', template.name or ''):
                raise ValidationError(_('Use lowercase letters, numbers and underscores in template names.'))
            try:
                components = json.loads(template.components_json or '[]')
                if not isinstance(components, list):
                    raise ValueError()
            except (ValueError, TypeError) as exc:
                raise ValidationError(_('Components must be a JSON list.')) from exc

    account_id = fields.Many2one('qing.whatsapp.account', required=True, ondelete='restrict')

    category = fields.Selection(TEMPLATE_CATEGORIES, default='UTILITY', required=True)

    company_id = fields.Many2one(related='account_id.company_id', store=True, index=True)

    components_json = fields.Text(default='[]', required=True)

    language = fields.Char(default='en_US', required=True)

    name = fields.Char(required=True)

    quality = fields.Char(default='UNKNOWN', readonly=True)

    remote_uid = fields.Char(readonly=True)

    status = fields.Char(default='DRAFT', readonly=True, tracking=True)

    def write(self, vals):
        if not self.env.su and set(vals) & {'status', 'quality', 'remote_uid'}:
            raise AccessError(_('Template approval is managed by Meta.'))
        if not self.env.su and set(vals) & {'name', 'language', 'category', 'components_json', 'account_id'}:
            vals = dict(vals, status='DRAFT')
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(set(v) & {'status', 'quality', 'remote_uid'} for v in vals_list):
            raise AccessError(_('Template approval is managed by Meta.'))
        return super().create(vals_list)

    def action_submit(self):
        self.ensure_one()
        self.check_access('write')
        self.account_id._assert_operator()
        if not self.env.user.has_group('qing_whatsapp.group_manager'):
            raise AccessError(_('Only managers may submit templates.'))
        try:
            data = self.account_id._client().request('POST', self.account_id.waba_uid + '/message_templates',
                payload={'name': self.name, 'language': self.language, 'category': self.category,
                         'components': json.loads(self.components_json)})
        except CloudError as exc:
            raise ValidationError(_('Template submission result is unavailable (code %s). Synchronize before submitting again.', exc.code)) from exc
        self.sudo().write({'remote_uid': data.get('id'), 'status': data.get('status', 'PENDING')})
