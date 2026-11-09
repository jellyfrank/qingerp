from .models.res_config_settings import SETTING_KEY


def uninstall_hook(env):
    """Return the two sale menus to their native actions before removing ours."""
    for menu_xmlid, own_action_xmlid, native_action_xmlid in (
        ('sale.menu_sale_quotations', 'qing_sale.action_sale_quotations_menu',
         'sale.action_quotations_with_onboarding'),
        ('sale.menu_sale_order', 'qing_sale.action_sale_orders_menu', 'sale.action_orders'),
    ):
        menu = env.ref(menu_xmlid, raise_if_not_found=False)
        own_action = env.ref(own_action_xmlid, raise_if_not_found=False)
        if menu and own_action and menu.action == own_action:
            menu.sudo().action = env.ref(native_action_xmlid)
    env['ir.config_parameter'].sudo().search([('key', '=', SETTING_KEY)]).unlink()
