from odoo import api, fields, models, _
from odoo.exceptions import AccessError
from .constants import AUDIT_STATES, CATEGORIES, fingerprint


class SmsRemoteTemplate(models.Model):
    _name = 'qing.sms.remote.template'
    _description = '云端短信模板'
    _check_company_auto = True

    def _fingerprint(self):
        self.ensure_one()
        return fingerprint([self.remote_id, self.content, self.normalized_content, self.variables,
                            self.variable_rules, self.sign_names, self.audit_state, self.category, self.active])

    account_id = fields.Many2one('qing.sms.account', string='短信账户', required=True, ondelete='restrict', index=True)
    active = fields.Boolean(string='启用', default=True)
    audit_state = fields.Selection(AUDIT_STATES, default='unknown', required=True, string='Audit Status')
    category = fields.Selection(CATEGORIES, default='notification', required=True, string='SMS Category')
    company_id = fields.Many2one(string='公司', related='account_id.company_id', store=True, index=True)
    content = fields.Text(string='云端正文', readonly=True)
    last_sync_at = fields.Datetime(string='同步时间', readonly=True)
    name = fields.Char(string='名称', required=True)
    normalized_content = fields.Text(readonly=True)
    raw_status = fields.Char(readonly=True)
    reject_reason = fields.Text(string='驳回原因', readonly=True)
    remote_id = fields.Char(string='模板编号', required=True)
    revision_ids = fields.One2many('qing.sms.template.revision', 'template_id', string='同步历史', readonly=True)
    sign_names = fields.Json(string='适用签名', readonly=True, default=list)
    variable_rules = fields.Json(string='厂商变量规则', readonly=True, default=dict)
    variables = fields.Json(string='云端变量', readonly=True, default=list)

    _remote_unique = models.Constraint('unique(account_id, remote_id)', '同一账户不能重复登记模板。')

    @api.model
    def _create_synced(self, values):
        record = super(SmsRemoteTemplate, self).create(values)
        record._record_revision()
        return record

    def _write_synced(self, values):
        result = super().write(values)
        self._record_revision()
        return result

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if any(key in values for key in ['content', 'normalized_content', 'variables', 'variable_rules', 'sign_names', 'audit_state', 'raw_status', 'reject_reason', 'last_sync_at', 'category']):
                raise AccessError(_('云端审核信息只能通过同步写入。'))
        return super().create(vals_list)

    def write(self, values):
        if any(key in values for key in ['name', 'account_id', 'remote_id', 'content', 'normalized_content', 'variables', 'variable_rules', 'sign_names', 'audit_state', 'raw_status', 'reject_reason', 'last_sync_at', 'category']):
            raise AccessError(_('云端审核信息只能通过同步写入。'))
        return super().write(values)

    def _record_revision(self):
        for template in self:
            digest = template._fingerprint()
            if template.revision_ids[:1].fingerprint == digest:
                continue
            self.env['qing.sms.template.revision'].sudo().create({
                'template_id': template.id, 'content': template.content, 'variables': template.variables,
                'variable_rules': template.variable_rules, 'sign_names': template.sign_names,
                'audit_state': template.audit_state, 'category': template.category, 'fingerprint': digest})
