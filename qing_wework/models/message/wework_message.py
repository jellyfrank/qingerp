#!/usr/bin/python3
# @Time    : 2022-01-23
# @Author  : Kevin Kong (kfx2007@163.com)

from odoo import api, fields, models, _
from odoo.tools.safe_eval import safe_eval as eval
from odoo.exceptions import UserError
from functools import wraps
import logging
import json
from urllib.parse import quote

_logger = logging.getLogger(__name__)

MSG_TYPES = [
    ("text", "Text Message"),
    ("image", "Image Message"),
    ("voice", "Voice"),
    ("video", "Video"),
    ("file", "File"),
    ("card", "Text Card"),
    ("news", ' External Article'),
    ("mpnews", "Internal Article"),
    ("markdown", "Markdown"),
    ("miniprogram", '小程序消息')
]

STATES = [
    ("draft", "Draft"),
    ("success", "Success"),
    ("fail", "Fail")
]



class wework_message(models.Model):

    _name = "wework.message"
    _description = "Wework Message"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    def _get_receivers(self):
        self.ensure_one()
        if not self.to_all:
            user_ids = "|".join(
                [str(user_id) for user_id in self.employee_ids.mapped("wework_user_id")])
        else:
            user_ids = "@all"

        department_ids = "|".join(
            [str(department_id) for department_id in self.department_ids.mapped("wework_department_id")])
        return user_ids, department_ids

    def wework_message_send(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            user_ids, department_ids = self._get_receivers()
            msg = func(self, *args, **kwargs)
            _logger.debug(
                f"[QingERP WeCom]send {msg['msgtype']} message to users:{user_ids}, departments:{department_ids}, messages:{msg}")
            res = self.env.company.weworkclient.message.send(
                self.env.company.wework_id.agent_id, user_ids, department_ids, msg=msg)
            if res.get("errmsg", None) == 'ok':
                _logger.debug("[QingERP WeCom]Message Send Scuess.")
                self.state = 'success'
            else:
                _logger.error(f"[QingERP WeCom]Message Send Fail. Detail:{res}")
                self.state = 'fail'
                self.error_message = res['errmsg']
        return wrapper

    @wework_message_send
    def _send_text(self, content):
        """send text message."""
        return {
            "msgtype": self.msg_type,
            self.msg_type: {
                "content": self.content
            }
        }

    @wework_message_send
    def _send_media(self, msg_type, media_id):
        """"send image message."""
        if not media_id:
            raise UserError(_("Please select a WeCom media file before sending it."))
        media_id = media_id.with_company(self.company_id)._get_media_id_for_send()
        data = {
            "msgtype": f"{msg_type}",
            f"{msg_type}": {
                "media_id": media_id
            }
        }
        if msg_type == "video":
            data[msg_type]['title'] = self.media_id.video_title
            data[msg_type]['description'] = self.media_id.video_description
        return data

    @wework_message_send
    def _send_card_message(self):
        """send card message."""
        return {
            "msgtype": "textcard",
            "textcard": {
                "title": self.title,
                "description": self.description,
                "url": self.redirect_url,
                "btntext": self.button_text
            }
        }

    @wework_message_send
    def _send_news(self):
        """send news"""
        msgtype = "news" if self.articles[0].type == "news" else "mpnews"
        return {
            "msgtype": msgtype,
            msgtype: {
                "articles": [article.to_json() for article in self.articles]
            }
        }

    @api.depends("need_authorized")
    def _compute_redirect_url(self):
        """Compute the redirect URL."""
        for message in self:
            message.redirect_url = self.company_id.wework_id.get_authorized_url(
                message.url) if message.need_authorized else message.url

    # [TODO] Tags
    articles = fields.Many2many("wework.article", string="Articles")
    button_text = fields.Char("Button Text")
    content = fields.Text("Message Body")
    company_id = fields.Many2one("res.company", string="Company")
    description = fields.Text("Description")
    department_ids = fields.Many2many("hr.department", string="Departments")
    employee_ids = fields.Many2many("hr.employee", string="Receive Employees")
    error_message = fields.Text("Error Message.")
    from_user = fields.Char("From")
    need_authorized = fields.Boolean("Need Authorized", default=True)
    media_id = fields.Many2one("wework.media", string="Media")
    msg_type = fields.Selection(MSG_TYPES, string="Message Type")
    url = fields.Char("Redirect URL")
    redirect_url = fields.Char("Final Redirect URL", compute="_compute_redirect_url")
    res_model = fields.Char("Model")
    res_id = fields.Integer("Res Id")
    state = fields.Selection(STATES, string="State", default="draft")
    title = fields.Char("Title", size=128)
    to_user = fields.Char("To User")
    to_all = fields.Boolean("To ALL", default=False)

    def reset(self):
        """Reset message state"""
        for message in self:
            message.state = 'draft'

    def send(self):
        """
        send message  to receivers
        or
        receive message from wework then create message to related record.
        """
        for msg in self:
            # direction
            platform = msg.company_id.wework_id.company_id
            if msg.from_user != platform and msg.to_user == platform:
                # receive message from wework.
                record = self.env[msg.res_model].with_company(msg.company_id.id).browse(msg.res_id)
                employee_obj = self.env['hr.employee'].with_company(msg.company_id.id).sudo()
                domain = [('wework_user_id','=', msg.from_user),('company_id','=', msg.company_id.id)]
                employee_id = employee_obj.search(domain)
                _logger.debug("[QingERP WeCom]: %s reply message to record: %s" % (employee_id.name, record.display_name))
                record.with_context({"no_reply":True}).sudo().message_post(body=msg.content, author_id=employee_id.user_partner_id.id)
                msg.state = 'success'
            else:
                _logger.debug("[QingERP WeCom]: send message to wework user: %s" % msg.employee_ids.mapped("name"))
                # [TODO] MiniProgam
                if msg.msg_type in ("text", "markdown"):
                    msg._send_text(msg.content)
                elif msg.msg_type in ("image", "voice", "video", "file"):
                    msg._send_media(msg.msg_type, msg.media_id)
                elif msg.msg_type in ("cardtext"):
                    msg._send_card_message()
                else:
                    msg._send_news()

    def unlink(self):
        if self.state == 'success':
            raise UserError(_("can't delete sent message."))
        return super().unlink()
