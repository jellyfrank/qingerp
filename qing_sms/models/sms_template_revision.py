from odoo import fields, models, _
from odoo.exceptions import AccessError


class SmsTemplateRevision(models.Model):
    _name = 'qing.sms.template.revision'
    _description = '云端短信模板历史版本'
    _order = 'id desc'

    audit_state = fields.Char(readonly=True)
    category = fields.Char(readonly=True)
    company_id = fields.Many2one(related='template_id.company_id', store=True, index=True)
    content = fields.Text(readonly=True, string='审核正文')
    fingerprint = fields.Char(readonly=True)
    sign_names = fields.Json(readonly=True)
    template_id = fields.Many2one('qing.sms.remote.template', required=True, ondelete='restrict')
    variable_rules = fields.Json(readonly=True)
    variables = fields.Json(readonly=True)

    def write(self, vals):
        raise AccessError(_('历史模板版本不可修改。'))

    def unlink(self):
        raise AccessError(_('历史模板版本不可删除。'))
