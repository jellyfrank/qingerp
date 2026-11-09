from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    aliyun_sms_config_id = fields.Many2one(
        "qing.sms.aliyun.config", string="Aliyun SMS Config",
        groups="base.group_system", copy=False,
    )

    aliyun_delivery_template_id = fields.Many2one(
        'qing.sms.aliyun.template', string='旧发货短信模板', groups='base.group_system', copy=False,
    )
