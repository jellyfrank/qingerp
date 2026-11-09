from math import isfinite

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


RENTAL_PERIODS = [('day', 'Day'), ('week', 'Week'), ('month', 'Month (30 days)')]


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    @api.constrains('qing_rental_ok', 'is_storable', 'uom_id', 'qing_rental_service_id',
                    'qing_rental_rate', 'qing_rental_padding_hours', 'company_id')
    def _check_qing_rental_configuration(self):
        unit = self.env.ref('uom.product_uom_unit')
        for product in self:
            if not isfinite(product.qing_rental_rate) or product.qing_rental_rate < 0:
                raise ValidationError(_('Rental rates must be finite and non-negative.'))
            if not isfinite(product.qing_rental_padding_hours) or product.qing_rental_padding_hours < 0:
                raise ValidationError(_('Rental turnaround time must be finite and non-negative.'))
            if not product.qing_rental_ok:
                continue
            if not product.is_storable or product.uom_id != unit:
                raise ValidationError(_('Rental equipment must track inventory and use Units as its unit.'))
            service = product.qing_rental_service_id
            if not service or service.type != 'service' or service.invoice_policy != 'order':
                raise ValidationError(_('Select a rental service invoiced on ordered quantities.'))
            if service.company_id and service.company_id != product.company_id:
                raise ValidationError(_('The rental service must be shared or belong to the equipment company.'))

    qing_rental_ok = fields.Boolean(string='Can be Rented')
    qing_rental_padding_hours = fields.Float(string='Turnaround Hours', default=0.0)
    qing_rental_period = fields.Selection(RENTAL_PERIODS, string='Rental Price Period', default='day', required=True)
    qing_rental_rate = fields.Float(string='Rental Rate', digits='Product Price', default=0.0)
    qing_rental_service_id = fields.Many2one(
        'product.product', string='Rental Billing Service', check_company=True, ondelete='restrict',
        domain="[('type', '=', 'service'), ('invoice_policy', '=', 'order')]",
    )
