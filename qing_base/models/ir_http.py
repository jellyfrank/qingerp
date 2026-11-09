from odoo import models

from ..tools import mommy_base_loaded


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        result = super().session_info()
        if self.env.user._is_internal():
            module = self.env['ir.module.module']._get('qing_base')
            result['qing_base_version'] = module.latest_version or ''
            result['qing_legacy_mommy_base'] = mommy_base_loaded(self.env)
            result['qing_web_title'] = self.env['ir.config_parameter'].sudo().get_str(
                'qing_base.title', default='QingERP 20.0',
            )
        return result
