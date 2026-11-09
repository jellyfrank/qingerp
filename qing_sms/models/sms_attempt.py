from odoo import fields, models
from odoo.exceptions import AccessError


class SmsAttempt(models.Model):
    _name = 'qing.sms.attempt'
    _description = '短信发送与回执记录'
    _order = 'id desc'

    company_id = fields.Many2one(related='message_id.company_id', store=True, index=True)
    code = fields.Char(string='结果代码', readonly=True)
    kind = fields.Selection([('submit', 'Submit'), ('query', 'Query'), ('cancel', 'Cancel')], required=True, string='Operation')
    message_id = fields.Many2one('qing.sms.message', required=True, ondelete='restrict', index=True)
    request_id = fields.Char(string='请求编号', readonly=True)
    state = fields.Char(string='状态', readonly=True)

    def write(self, vals):
        raise AccessError('发送记录不可修改。')

    def unlink(self):
        raise AccessError('发送记录不可删除。')
