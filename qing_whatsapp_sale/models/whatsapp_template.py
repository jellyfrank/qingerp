from odoo import fields, models


class WhatsappTemplate(models.Model):
    _inherit = 'qing.whatsapp.template'

    sale_variable_ids = fields.One2many('qing.whatsapp.template.variable', 'template_id', string='Sales variables')
