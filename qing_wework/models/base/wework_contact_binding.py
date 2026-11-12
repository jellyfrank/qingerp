# -*- coding: utf-8 -*-

from odoo import fields, models

from .wework_contact_sync_constants import BINDING_STATES


class WeworkContactBinding(models.Model):
    _name = "wework.contact.binding"
    _description = "WeWork Contact Binding"
    _rec_name = "wework_user_id"

    _user_unique = models.Constraint(
        "UNIQUE(wework_id, user_id)",
        "A user can only have one binding per WeWork configuration.",
    )
    _userid_unique = models.Constraint(
        "UNIQUE(wework_id, wework_user_id)",
        "A WeWork user ID can only be bound once per WeWork configuration.",
    )

    active = fields.Boolean(default=True)
    employee_id = fields.Many2one("hr.employee", required=True, ondelete="restrict")
    last_error = fields.Text(readonly=True)
    last_sync_at = fields.Datetime(readonly=True)
    last_sync_hash = fields.Char(readonly=True)
    state = fields.Selection(BINDING_STATES, default="active", required=True)
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade")
    wework_id = fields.Many2one("qing.wework", required=True, ondelete="cascade")
    wework_user_id = fields.Char(required=True, readonly=True)
