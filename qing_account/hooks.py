from odoo import Command


def uninstall_hook(env):
    """Remove the additional action and menu groups while preserving native groups."""
    action = env.ref('account.accountant_reset_to_draft_entries_action', raise_if_not_found=False)
    billing_group = env.ref('account.group_account_invoice', raise_if_not_found=False)
    if action and billing_group:
        action.group_ids = [Command.unlink(billing_group.id)]

    manager_group = env.ref('account.group_account_manager', raise_if_not_found=False)
    if manager_group:
        for xmlid in (
            'account.menu_finance_entries',
            'account.account_transactions_menu',
            'account.menu_action_move_journal_line_form',
            'account.menu_action_account_form',
        ):
            menu = env.ref(xmlid, raise_if_not_found=False)
            if menu:
                menu.group_ids = [Command.unlink(manager_group.id)]
