"""Client action helpers shared by the WeCom modules.

Standalone replacements for the record helpers the Solutions module took
from its base dependency, kept as plain functions so no model is patched.
"""
from odoo import _
from odoo.exceptions import UserError


def notification_action(title, type="info", sticky=False, reload=False):
    """Toast notification; ``reload`` refreshes the current view afterwards."""
    action = {
        "type": "ir.actions.client",
        "tag": "display_notification",
        "params": {"title": title, "type": type, "sticky": sticky},
    }
    if reload:
        action["params"]["next"] = {"type": "ir.actions.client", "tag": "soft_reload"}
    return action


def message_action(title, content):
    """Warning that stays on screen until the user dismisses it."""
    return {
        "type": "ir.actions.client",
        "tag": "display_notification",
        "params": {"title": title, "message": content, "type": "warning", "sticky": True},
    }


def confirm_action(records, title, content, callback="_action_pops_up_confirm"):
    """Open a confirmation dialog that runs ``callback`` on ``records`` when confirmed."""
    wizard = records.env["wework.confirm.wizard"].create({
        "name": title,
        "content": content,
        "res_model": records._name,
        "res_ids": ",".join(str(record_id) for record_id in records.ids),
        "callback": callback,
    })
    return {
        "type": "ir.actions.act_window",
        "name": title,
        "res_model": wizard._name,
        "res_id": wizard.id,
        "view_mode": "form",
        "views": [(False, "form")],
        "target": "new",
    }


def view_action(records, view_mode=None):
    """Open ``records`` with their model's default window action."""
    window = records.env["ir.actions.act_window"].search([("res_model", "=", records._name)], limit=1)
    if not window:
        raise UserError(_("Model: %s has no valid action for this operation.", records._name))
    action = window._get_action_dict()
    if len(records) == 1:
        action.update(res_id=records.id, view_mode="form", views=[(False, "form")])
        return action
    mode = view_mode or action.get("view_mode") or "list,form"
    action.update(
        domain=[("id", "in", records.ids)],
        view_mode=mode,
        views=[(False, sub_mode.strip()) for sub_mode in mode.split(",")],
    )
    return action


def record_attachments(record):
    """All attachments linked to ``record``; sudo because callers upload them to WeCom."""
    return record.env["ir.attachment"].sudo().search([
        ("res_model", "=", record._name),
        ("res_id", "=", record.id),
    ])
