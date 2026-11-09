import json
from uuid import uuid4

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class SmsSend(models.TransientModel):
    _name = 'qing.sms.send'
    _description = '发送国内短信'

    @api.onchange('binding_id')
    def _onchange_binding(self):
        if self.binding_id:
            self.parameter_ids = [fields.Command.clear()] + [fields.Command.create({'key': var.key, 'name': var.key})
                for var in self.binding_id.variable_ids if var.source == 'input']
            self.preview = False
            self.record_id = False

    def _payload(self):
        self.ensure_one()
        return {row.key: row.value or '' for row in self.parameter_ids}

    binding_id = fields.Many2one('qing.sms.binding', required=True, string='业务模板')
    event_key = fields.Char(default=lambda self: uuid4().hex, readonly=True)
    number = fields.Char(string='手机号码', required=True)
    parameter_ids = fields.One2many('qing.sms.send.value', 'wizard_id', string='短信参数')
    preview = fields.Text(string='正文预览', readonly=True)
    record_id = fields.Many2oneReference(string='业务单据', model_field='record_model')
    record_model = fields.Char(related='binding_id.template_id.model')

    def action_preview(self):
        self.ensure_one()
        number, parameters, body = self.binding_id._prepare_payload(self.number, self._payload(), self.record_id)
        self.preview = _('验证码内容已隐藏') if self.binding_id.remote_template_id.category == 'otp' else body
        return {'type': 'ir.actions.act_window', 'name': _('发送短信'), 'res_model': self._name, 'view_mode': 'form',
                'res_id': self.id, 'target': 'new'}

    def action_send(self):
        self.ensure_one()
        message = self.binding_id.send_sms(self.number, self._payload(), event_key=self.event_key, record_id=self.record_id)
        self.parameter_ids.unlink()
        return {'type': 'ir.actions.act_window', 'name': _('发送记录'), 'res_model': 'qing.sms.message', 'view_mode': 'form',
                'res_id': message.id, 'target': 'current'}
