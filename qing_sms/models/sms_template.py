from odoo import fields, models


class SmsTemplate(models.Model):
    _inherit = 'sms.template'

    domestic_binding_ids = fields.One2many('qing.sms.binding', 'template_id', string='国内短信绑定')
