from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _get_translation_frontend_modules_name(cls):
        # Ship this module's frontend terms too: it fills Chinese for native
        # portal strings Odoo leaves untranslated (e.g. the chatter's
        # "No messages yet."), found through the global fallback.
        return super()._get_translation_frontend_modules_name() + ['qing_portal']
