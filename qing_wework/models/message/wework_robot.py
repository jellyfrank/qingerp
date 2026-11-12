#!/usr/bin/python3
# @Time    : 2023-07-18
# @Author  : Kevin Kong (kfx2007@163.com)

import base64
import logging
from functools import wraps
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import BinaryValue
from ...tools.actions import view_action
import requests

_logger = logging.getLogger(__name__)

MSG_TYPES = [
    ('text', 'Text Message'),
    ('markdown', 'Markdown Message'),
    ('markdown_v2', 'Markdown V2 Message'),
    ('image', 'Image'),
    ('news', 'News'),
    ('file', 'File'),
    ('template_card', 'Template Card')
]


STATES = [
    ('draft', 'Draft'),
    ('success', 'Success'),
    ('failed', 'Fail')
]


class wework_robot(models.Model):
    _name = "wework.robot"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = "WeWork Robot."

    @api.depends("robot_message_ids")
    def _compute_message_count(self):
        """Compute Message Count."""
        for robot in self:
            robot.robot_message_count = len(robot.robot_message_ids)

    active = fields.Boolean("Active", default=True)
    name = fields.Char("Name")
    robot_message_count = fields.Integer(
        "Message Count", compute="_compute_message_count")
    robot_message_ids = fields.One2many(
        "wework.robot.message", "robot_id", string="Robot Messages")
    webhook_url = fields.Char("Webhook URL")

    def action_view_messages(self):
        """Action View Messages."""
        return view_action(self.robot_message_ids)


class wework_robot_message(models.Model):
    _name = "wework.robot.message"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = "Wework Robot Message"

    def _get_file_upload_url(self):
        """Build the robot-specific file upload endpoint from its webhook URL."""
        self.ensure_one()
        parsed_url = urlparse(self.robot_id.webhook_url or "")
        webhook_key = parse_qs(parsed_url.query).get("key", [False])[0]
        if not webhook_key:
            raise UserError(_("The robot webhook URL is invalid."))

        return urlunparse(parsed_url._replace(
            path="/cgi-bin/webhook/upload_media",
            params="",
            query=urlencode({"key": webhook_key, "type": "file"}),
        ))

    def _upload_file(self):
        """Upload the file through the robot webhook and return its media ID."""
        self.ensure_one()
        if not self.file:
            raise UserError(_("Please select a file before sending the robot message."))

        file_name = self.file_name or self.name or "file"
        file_content = self.file
        if isinstance(file_content, BinaryValue):
            file_content = file_content.content
        else:
            file_content = base64.b64decode(file_content)
        try:
            response = requests.post(
                self._get_file_upload_url(),
                files={"media": (file_name, file_content)},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as error:
            raise UserError(_("Unable to upload the robot file: %s") % error) from error

        media_id = data.get("media_id")
        if data.get("errcode") or not media_id:
            raise UserError(_("Unable to upload the robot file: %s") % data.get("errmsg"))
        return media_id

    def robot_message_send(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            data = func(self, *args, **kwargs)
            _logger.debug(f"[Wework]Robot:{self.name} send message：{data}")
            res = requests.post(self.robot_id.webhook_url, json=data, headers={
                "Content-Type": "application/json"
            })
            return res
        return wrapper

    @robot_message_send
    def _send_text(self):
        """send robot text message."""
        mentioned_list = ["@all"] if self.mention_all else [
            emp.wework_user_id for emp in self.employee_ids if emp.wework_user_id
        ]
        return {
            "msgtype": self.type,
            "text": {
                "content": self.content,
                "mentioned_list": mentioned_list,
            }
        }

    @robot_message_send
    def _send_markdown(self):
        """send robot markdown message."""
        return {
            "msgtype": self.type,
            "markdown": {
                "content": self.content,
            }
        }

    @robot_message_send
    def _send_markdown_v2(self):
        """Send enhanced Markdown with table support to a group robot."""
        return {
            "msgtype": self.type,
            "markdown_v2": {
                "content": self.content,
            }
        }

    @robot_message_send
    def _send_file(self):
        """Upload and send a file through the robot webhook."""
        return {
            "msgtype": "file",
            "file": {"media_id": self._upload_file()},
        }

    content = fields.Text("Content",readonly=True)
    employee_ids = fields.Many2many(
        "hr.employee", domain=[('wework_user_id', '!=', False)],readonly=True)
    error_message = fields.Text("Error Message", readonly=True)
    file = fields.Binary("File", readonly=True)
    file_name = fields.Char("File Name", readonly=True)
    mention_all = fields.Boolean("Mention All", readonly=True)
    name = fields.Char("Name",readonly=True)
    res_id = fields.Integer("Resource ID", readonly=True)
    res_model = fields.Char("Resource Model", readonly=True)
    robot_id = fields.Many2one("wework.robot", string="Robot",readonly=True)
    state = fields.Selection(STATES, string="Status", default='draft',readonly=True)
    template_id = fields.Many2one("wework.robot.template", string="Template", readonly=True)
    type = fields.Selection(MSG_TYPES, string="Message Type",readonly=True)

    def send(self):
        """Send Robot Message."""
        for message in self:
            _call_func = getattr(message, f"_send_{message.type}", None)
            if not _call_func:
                raise UserError(_("Robot message type %s is not supported yet.") % message.type)
            res = _call_func()
            _logger.debug(f"[Wework] send robot message result:{res.content}")
            data = res.json()
            if data.get('errcode') == 0:
                message.state = 'success'
                message.error_message = False
                continue
            message.state = 'failed'
            message.error_message = data.get("errmsg") or str(data)

    def reset(self):
        """Reset Robot Message."""
        self.state = 'draft'

    def unlink(self):
        """Delete"""
        for message in self:
            if message.state != 'draft':
                raise UserError(_(f"Can not delete this message."))
        return super().unlink()
