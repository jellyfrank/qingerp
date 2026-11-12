#!/usr/bin/python3
# @Time    : 2023-03-26
# @Author  : Kevin Kong (kfx2007@163.com)

from odoo import api, fields, models, _
from odoo.fields import Command
from odoo.exceptions import UserError
from odoo.tools.safe_eval import safe_eval
from .wework_message import MSG_TYPES


class wework_message_template(models.Model):
    _name = "wework.message.template"
    _description = "WeWork Message Template"

    @api.onchange("msg_type")
    def _onchange_msg_type(self):
        if self.msg_type not in ['text', 'card', 'file']:
            raise UserError(_("This type not available yet."))

    def _build_text_message(self, ctx):
        """
        Build Text Message from Template.

        :param ctx: context
        :return: data dict
        """
        safe_eval(self.content, ctx, mode="exec")
        result = ctx.get("result")
        data = {
            "msg_type": "text",
            "content": result
        }
        return data

    content = fields.Text("Text Template")
    button_text = fields.Char("Button Text")
    description = fields.Text("Description")
    employee_ids = fields.Many2many("hr.employee",string="Reciepts")
    media_id = fields.Many2one(
        "wework.media",
        string="File",
        domain="[('media_type', '=', 'file')]",
    )
    msg_type = fields.Selection(
        MSG_TYPES, string="Message Type", default='text')
    name = fields.Char("Name")
    need_authorized = fields.Boolean("Need Authorized", default=False)
    redirect_url = fields.Char("Redirect URL")
    title = fields.Char("Title")

    @api.model
    def build_message(self, records=None, receipt_ids=None, ctx=None):
        """
        Build Message from Template.

        :param records: recordset
        :param receipt_ids: recipient employee recordset
        :param ctx: context
        :return: wework.message record
        """
        source_record = records or self
        receipt_ids = receipt_ids | self.employee_ids if receipt_ids else self.employee_ids
        if not receipt_ids:
            raise UserError(_("Receipt user can't be empty."))

        wework_msg_obj = self.env['wework.message'].sudo()
        message_data = {}
        if not ctx:
            ctx = {
                "self": source_record
            }
        else:
            ctx.update({
                "self": source_record
            })
        # text message.
        if self.msg_type == "text":
            message_data = self._build_text_message(ctx)

        # text card message.
        elif self.msg_type == 'card':
            # content
            safe_eval(self.content, ctx, mode="exec")
            result = ctx.get("result")
            content = result
            # title
            safe_eval(self.title, ctx, mode="exec")
            result = ctx.get("result")
            title = result
            # Redirect URL.
            safe_eval(self.redirect_url, ctx, mode="exec")
            result = ctx.get("result")
            redirect_url = result
            data = {
                "msg_type":"card",
                "employee_ids": [Command.link(r.id) for r in receipt_ids],
                "content": content,
                "title": title,
                "need_authorized": self.need_authorized,
                "url": redirect_url,
                "button_text": self.button_text,
                "description": self.description,
            }
            message_data = data
        elif self.msg_type == "file":
            if (
                not self.media_id
                or self.media_id.media_type != "file"
                or not self.media_id.file
            ):
                raise UserError(_("Please select an uploaded WeWork file."))
            message_data = {
                "msg_type": "file",
                "employee_ids": [Command.link(record.id) for record in receipt_ids],
                "media_id": self.media_id.id,
            }
        else:
            return None
        company_id = (
            source_record.company_id.id
            if "company_id" in source_record._fields and source_record.company_id
            else self.env.company.id
        )
        message_data.update({"company_id": company_id})
        msg = wework_msg_obj.create(message_data)
        return msg
