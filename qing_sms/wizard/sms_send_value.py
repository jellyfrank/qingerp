from odoo import fields, models


class SmsSendValue(models.TransientModel):
    _name = 'qing.sms.send.value'
    _description = '短信参数输入'

    composer_id = fields.Many2one('sms.composer', ondelete='cascade')
    key = fields.Char(required=True, readonly=True)
    name = fields.Char(readonly=True, string='变量')
    value = fields.Char(string='值')
    wizard_id = fields.Many2one('qing.sms.send', ondelete='cascade')
