from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    qing_sms_delivery_binding_id = fields.Many2one(
        'qing.sms.binding', string='发货短信业务模板', check_company=True,
        domain="[('company_id', '=', id)]",
    )
