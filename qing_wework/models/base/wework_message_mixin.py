#!/usr/bin/python3
# @Time    : 2026-01-26
# @Author  : Kevin Kong (kfx2007@163.com)

from odoo import api, fields, models, _
import logging

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
    ("miniprogram", 'Miniprogram Message')
]


class WeWorkMessageMixin(models.AbstractModel):
    _name = "wework.message.mixin"
    _description = "WeWork Message Mixin"

    def _set_from_user(self):
        """
        Set From User.

        :return: from_user string
        """
        self.ensure_one()
        company = self.env.company
        if company.wework_app_id and company.wework_app_id.agentid:
            from_user = company.wework_app_id.agentid
        else:
            from_user = company.name
        return from_user

    def _set_message_type(self, msg_type):
        """
        Set Message Type.

        :param msg_type: message type
        :return: msg_type string
        """
        if msg_type not in dict(MSG_TYPES):
            raise ValueError(_("Message type %s is not supported.") % msg_type)
        return msg_type

    def _prepare_wework_message(self, msg_type, content):
        """
        Prepare WeWork Message.

        :param msg_type: message type
        :param content: message content
        :return: data dict
        """
        self.ensure_one()
        data = {
            "from_user": self._set_from_user(),
            "msg_type": self._set_message_type(msg_type),
            "content": content
        }
        return data

    def _build_wework_message(self, msg_type, content):
        """
        Build WeWork Message.

        :param msg_type: message type
        :param content: message content
        :return: data dict
        """
        data = self._prepare_wework_message(msg_type, content)
        return data

    def send_wework_message(self, msg_type, content):
        """
        Send WeWork Message.

        :param msg_type: message type
        :param content: message content
        :return: response dict
        """
        self.ensure_one()
        data = self._build_wework_message(msg_type, content)
        _logger.debug(f"[QingERP WeCom]Send {msg_type} message: {data}")
        wework_message_id = self.env['wework.message'].sudo().create(data)
        wework_message_id.send()
