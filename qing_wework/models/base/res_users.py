#!/usr/bin/python3
# @Time    : 2022-01-23
# @Author  : Kevin Kong (kfx2007@163.com)

from odoo import api, fields, models, _


class res_users(models.Model):
    _inherit = "res.users"

    notify_wework = fields.Boolean("Notify on Wework", default=False)

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        if not self.env.context.get("skip_wework_contact_sync"):
            self.env["wework.contact.sync.job"].action_queue_users(users)
        return users

    def write(self, vals):
        result = super().write(vals)
        sync_fields = {"active", "company_id", "company_ids", "name"}
        if sync_fields.intersection(vals) and not self.env.context.get("skip_wework_contact_sync"):
            self.env["wework.contact.sync.job"].action_queue_users(self)
        return result
