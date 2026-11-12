#!/usr/bin/python3
# @Time    : 2024-09-02
# @Author  : Kevin Kong (kfx2007@163.com)

from odoo import api, fields, models, _
import logging

_logger = logging.getLogger(__name__)

class mail_thread(models.AbstractModel):
    _inherit="mail.thread"

    def _get_company_id(self):
        """
        getting the wework client
        """
        if self.env.company.wework_id:
            return self.env.company

        company_ids = self.env["res.company"].sudo().search([])
        company_id = company_ids.filtered(lambda c: c.wework_id)
        if not company_id:
            _logger.warning(_("No company has configured the WeCom app."))
        return None

    def _send_wework_message(self, employee_ids, message, msg_body):
        """
        send wework message
        """
        if not msg_body:
            return
        cfg_obj = self.env['ir.config_parameter'].sudo()
        base_url = cfg_obj.get_str("web.base.url", default="")
        if "> " in msg_body:
            msg_body = msg_body.split('> ')[1]
        company_id = self._get_company_id()
        platform = company_id.wework_id.company_id
        wework_message_obj = self.env['wework.message'].sudo()
        msg_type = company_id.wework_id.message_type
        message_body = {
            "msg_type": msg_type,
            "url": f"{base_url}/web#id={self.id}&model={self._name}&view_type=form",
            "employee_ids": employee_ids,
            "from_user": platform,
            "company_id": company_id.id,
            "res_model": self._name,
            "res_id": self.id,
            "need_authorized": True
        }
        wework_message = wework_message_obj.create(message_body)
        if msg_type == 'text':
            msg_body =f"<a href='{wework_message.redirect_url}'>@{message.record_name}</a> {msg_body}"
            wework_message.content = msg_body
        if msg_type == 'card':
            wework_message.title = f"{message.create_uid.display_name} on {message.record_name}"
            wework_message.description = msg_body
        wework_message.send()


    def message_post(self, *, body='', subject=None, message_type='notification',
                     email_from=None, author_id=None, parent_id=False,
                     subtype_xmlid=None, subtype_id=False, partner_ids=None,
                     attachments=None, attachment_ids=None, body_is_html=False,
                     **kwargs):
        message = super().message_post(
                     body=body, subject=subject, message_type=message_type,
                     email_from=email_from, author_id=author_id, parent_id=parent_id,
                     subtype_xmlid=subtype_xmlid, subtype_id=subtype_id, partner_ids=partner_ids,
                     attachments=attachments, attachment_ids=attachment_ids, body_is_html=body_is_html,
                     **kwargs)
        _logger.debug("[QingERP WeCom]: message post context:%s" % self.env.context)
        if self.env.context.get("no_reply"):
            return message
        company_id = self._get_company_id()
        if company_id:
            partners = message.notification_ids.res_partner_id
            employee_ids = partners.with_company(company_id.id).sudo().employee_ids.filtered(lambda e: e.wework_user_id and e.user_id.notify_wework)
            _logger.debug("[QingERP WeCom]: message to wework employees[%s] :%s" % (company_id.name,employee_ids.mapped("name")))
            if employee_ids:
                self._send_wework_message(employee_ids, message, kwargs.get("body"))
        return message
