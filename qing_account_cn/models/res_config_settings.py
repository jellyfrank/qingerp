from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    qing_cn_chart_template = fields.Selection(
        related='company_id.chart_template', string='Current Chart Template', readonly=True,
        groups='account.group_account_manager',
    )
    qing_cn_taxpayer_type = fields.Selection(
        related='company_id.qing_cn_taxpayer_type', readonly=False,
        groups='account.group_account_manager',
    )

    qing_cn_voucher_style = fields.Selection(
        related='company_id.qing_cn_voucher_style', readonly=False,
        groups='account.group_account_manager',
    )

    def action_qing_cn_check_configuration(self):
        self.ensure_one()
        return self.company_id.action_qing_cn_check_configuration()
