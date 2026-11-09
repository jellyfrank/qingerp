from odoo import models


class AccountChartTemplate(models.AbstractModel):
    _inherit = 'account.chart.template'

    def _load(self, template_code, company, install_demo, force_create=True):
        result = super()._load(template_code, company, install_demo, force_create=force_create)
        module = self.env['ir.module.module']._get('qing_account_reports')
        if template_code == 'cn' and module.state == 'installed':
            self.env['qing.financial.report.scheme'].with_company(company)._bootstrap_company(company)
        return result
