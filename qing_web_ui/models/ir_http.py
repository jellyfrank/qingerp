from odoo import models
from odoo.http import request


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _handle_debug(cls):
        super()._handle_debug()
        # Pre-dispatch runs before request.env has its authenticated user.
        session_user = request.env['res.users'].browse(request.session.uid).sudo()
        if request.session.uid and (not session_user.exists() or not session_user.has_group('base.group_system')):
            # Clear both URL activation and an existing debug session after role changes.
            request.session.debug = ''

    def session_info(self):
        result = super().session_info()
        if self.env.user._is_internal():
            # Company logos are related to the company partner, not the user.
            # Reuse the native allowed-company scope; no image data in session.
            companies = self.env["res.company"].browse(
                list(result["user_companies"]["allowed_companies"])
            ).sudo()
            result["qing_company_logo_versions"] = {
                company.id: f"logo-{company.partner_id.id}-{company.partner_id.write_date}"
                for company in companies
            }
            # The stock "Your logo" placeholder is noise next to the company name.
            result["qing_company_default_logo_ids"] = companies.filtered("uses_default_logo").ids
            result["qing_theme_preferences"] = self.env["res.users.settings"]._get_qing_theme_preferences()
            # Read only this user's preferences; do not create settings on page load.
            result["qing_app_menu_order"] = (
                self.env.user.sudo().res_users_settings_ids.qing_app_menu_order or []
            )
        return result
