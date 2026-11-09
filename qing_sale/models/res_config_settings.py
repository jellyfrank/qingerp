from odoo import api, fields, models


QUOTATION_DOMAIN = "[('state', 'in', ('draft', 'sent'))]"
ORDER_DOMAIN = "[('state', '=', 'sale')]"
SETTING_KEY = 'qing_sale.show_only_quotations'
MENU_ACTIONS = {
    'quotation': ('sale.action_quotations_with_onboarding', QUOTATION_DOMAIN),
    'order': ('sale.action_orders', ORDER_DOMAIN),
}


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    qing_show_only_quotations = fields.Boolean(
        string='Show Only Quotations',
        help='Show only draft and sent quotations in Quotations, and confirmed orders in Sales Orders. '
             'Disable to restore the standard menu filters.',
        config_parameter=SETTING_KEY,
        default=True,
    )

    @api.model
    def _qing_sale_menu_action(self, menu_kind):
        """Apply the optional fixed domain only to actions opened from these menus."""
        native_xmlid, domain = MENU_ACTIONS[menu_kind]
        action = self.env['ir.actions.actions']._for_xml_id(native_xmlid)
        if self.env['ir.config_parameter'].sudo().get_bool(SETTING_KEY, default=True):
            action['domain'] = domain
        return action
