import ast

ACTION_CONTEXTS = {
    'account.res_partner_action_customer': (
        {'search_default_qing_customer': 1, 'res_partner_search_mode': 'customer', 'default_is_company': True},
        {'search_default_customer': 1, 'res_partner_search_mode': 'customer', 'default_is_company': True, 'default_customer_rank': 1},
    ),
    'account.res_partner_action_supplier': (
        {'search_default_qing_supplier': 1, 'res_partner_search_mode': 'supplier', 'default_is_company': True},
        {'search_default_supplier': 1, 'res_partner_search_mode': 'supplier', 'default_is_company': True, 'default_supplier_rank': 1},
    ),
}


def uninstall_hook(env):
    """Restore only our untouched default filters, preserving later customizations."""
    for xmlid, (installed, original) in ACTION_CONTEXTS.items():
        action = env.ref(xmlid, raise_if_not_found=False)
        if not action:
            continue
        try:
            context = ast.literal_eval(action.context or '{}')
        except (ValueError, SyntaxError):
            continue
        if context == installed:
            action.context = repr(original)
