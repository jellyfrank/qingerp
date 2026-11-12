# -*- coding: utf-8 -*-

from odoo import fields, models

from .wework_contact_sync_constants import BINDING_STATES


class WeworkDepartmentBinding(models.Model):
    _name = "wework.department.binding"
    _description = "WeWork Department Binding"
    _rec_name = "department_id"

    _unique = models.Constraint(
        "UNIQUE(wework_id, department_id)",
        "A department can only have one binding per WeWork configuration.",
    )
    _id_unique = models.Constraint(
        "UNIQUE(wework_id, wework_department_id)",
        "A WeWork department can only be bound once per WeWork configuration.",
    )

    active = fields.Boolean(default=True)
    department_id = fields.Many2one("hr.department", required=True, ondelete="cascade")
    last_error = fields.Text(readonly=True)
    last_sync_at = fields.Datetime(readonly=True)
    last_sync_hash = fields.Char(readonly=True)
    state = fields.Selection(BINDING_STATES, default="active", required=True)
    wework_department_id = fields.Integer(required=True)
    wework_id = fields.Many2one("qing.wework", required=True, ondelete="cascade")
