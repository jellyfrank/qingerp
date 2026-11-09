import re
from uuid import uuid4

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


PLACEHOLDER = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')
KEY = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


class SmsBlueprint(models.Model):
    _name = 'qing.sms.blueprint'
    _description = 'Domestic SMS blueprint'
    _check_company_auto = True

    @api.constrains('content', 'variable_schema')
    def _check_schema(self):
        for blueprint in self:
            schema = blueprint.variable_schema or []
            if not isinstance(schema, list) or len(schema) > 30:
                raise ValidationError(_('Blueprint variables must be an ordered list of at most 30 items.'))
            keys = []
            for item in schema:
                if not isinstance(item, dict) or not KEY.fullmatch(str(item.get('key', ''))):
                    raise ValidationError(_('A blueprint variable has an invalid key.'))
                if item.get('type', 'text') not in ('text', 'integer', 'float', 'date'):
                    raise ValidationError(_('A blueprint variable has an unsupported type.'))
                if not isinstance(item.get('max_length', 30), int) or not 0 < item.get('max_length', 30) <= 100:
                    raise ValidationError(_('A blueprint variable has an invalid maximum length.'))
                keys.append(item['key'])
            if len(set(keys)) != len(keys) or set(keys) != set(PLACEHOLDER.findall(blueprint.content or '')):
                raise ValidationError(_('Blueprint placeholders and variable schema must match exactly.'))
            if re.search(r'[\x00-\x1f]', blueprint.content or ''):
                raise ValidationError(_('Blueprint content contains control characters.'))

    active = fields.Boolean(default=True)
    application_remark = fields.Text()
    category = fields.Selection([('notification', 'Notification'), ('otp', 'One-time code')], required=True, default='notification')
    code = fields.Char(required=True, index=True)
    company_id = fields.Many2one('res.company', index=True, ondelete='cascade')
    content = fields.Text(required=True)
    event_code = fields.Char(index=True)
    model_id = fields.Many2one('ir.model')
    name = fields.Char(required=True)
    source_blueprint_id = fields.Many2one('qing.sms.blueprint', ondelete='restrict')
    variable_schema = fields.Json(default=list)
    variant_code = fields.Char(required=True, default='default')
    version = fields.Integer(required=True, default=1)

    _identity_unique = models.Constraint('unique(code, version, variant_code)', 'Blueprint version already exists.')

    def action_clone_for_company(self):
        self.ensure_one()
        if not self.env.user.has_group('qing_sms.group_sms_manager'):
            raise AccessError(_('Only SMS managers can clone blueprints.'))
        company = self.env.company
        clone = self.copy({'code': f'{self.code}.{company.id}.{uuid4().hex[:8]}',
                           'company_id': company.id, 'source_blueprint_id': self.id})
        return {'type': 'ir.actions.act_window', 'res_model': self._name,
                'res_id': clone.id, 'view_mode': 'form', 'target': 'current'}

    def action_prepare_application(self):
        self.ensure_one()
        if not self.env.user.has_group('qing_sms.group_sms_manager'):
            raise AccessError(_('Only SMS managers can prepare template applications.'))
        return {'type': 'ir.actions.act_window', 'res_model': 'qing.sms.template.application',
                'view_mode': 'form', 'target': 'current', 'context': {
                    'default_blueprint_id': self.id, 'default_company_id': self.env.company.id,
                    'default_name': self.name, 'default_content': self.content,
                    'default_category': self.category, 'default_variable_schema': self.variable_schema,
                    'default_application_remark': self.application_remark}}

    @api.model_create_multi
    def create(self, vals_list):
        if any(not vals.get('company_id') for vals in vals_list) and not self.env.su:
            raise AccessError(_('Only module installation can create public blueprints.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and any(not record.company_id for record in self):
            raise AccessError(_('Public blueprints are immutable. Clone one for your company.'))
        return super().write(vals)

    def unlink(self):
        if not self.env.su and any(not record.company_id for record in self):
            raise AccessError(_('Public blueprints cannot be deleted.'))
        return super().unlink()
