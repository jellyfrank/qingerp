from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

SALE_SOURCES = [('customer', 'Customer name'), ('order', 'Quotation number'), ('total', 'Total amount'),
                ('currency', 'Currency'), ('company', 'Company name'), ('portal_url', 'Order portal URL')]


class WhatsappTemplateVariable(models.Model):
    _name = 'qing.whatsapp.template.variable'
    _description = 'WhatsApp Sales Template Variable'
    _order = 'component, position'

    _slot_unique = models.Constraint('unique(template_id, component, position)', 'Each placeholder needs one mapping.')

    @api.constrains('position', 'component')
    def _check_position(self):
        for variable in self:
            if variable.position < 1 or (variable.component == 'header' and variable.position != 1):
                raise ValidationError(_('Use a positive placeholder number; text headers have one placeholder.'))

    company_id = fields.Many2one(related='template_id.company_id', store=True)
    component = fields.Selection([('body', 'Body'), ('header', 'Text header')], default='body', required=True)
    position = fields.Integer(default=1, required=True)
    source = fields.Selection(SALE_SOURCES, required=True)
    template_id = fields.Many2one('qing.whatsapp.template', required=True, ondelete='cascade')
