#!/usr/bin/python3
# @Time    : 2022-11-03
# @Author  : Kevin Kong (kfx2007@163.com)

import logging
from urllib.parse import quote

from odoo import _, fields, models
from odoo.exceptions import UserError
from ...tools.actions import notification_action
from wechatpy.exceptions import WeChatClientException

try:
    from wechatpy.work.client import WeChatClient as WeworkClient
except ImportError:
    from wechatpy.enterprise.client import WeChatClient as WeworkClient


_logger = logging.getLogger(__name__)

AUZHTORIZED_URL = "https://open.weixin.qq.com/connect/oauth2/authorize?appid=%s&redirect_uri=%s&response_type=code&scope=snsapi_base&state=STATE#wechat_redirect"

EMP_SYNC_PLOCIES = [
    ("name", "Name"),
    ("phone", "Phone"),
    ("email", "Email"),
    ("email_or_phone", "Email or Phone"),
    ("any", "Any one condition"),
]

MESSAGE_TYPES = [
    ("text", "Text"),
    ("card", "Card"),
]

USER_BIND_POLICIES = [
    ("name", "By Name"),
    ("email", "By Email"),
]


class QingWework(models.Model):
    _name = "qing.wework"
    _description = "WeWork Client"

    def _compute_contact_sync_job_count(self):
        counts = {}
        if self.ids:
            Job = self.env["wework.contact.sync.job"].sudo()
            grouped_jobs = Job._read_group(
                [("wework_id", "in", self.ids)],
                groupby=["wework_id"],
                aggregates=["__count"],
            )
            counts = {
                wework.id: count
                for wework, count in grouped_jobs
                if wework
            }
        for wework in self:
            wework.contact_sync_job_count = counts.get(wework.id, 0)

    def _get_config_company(self):
        self.ensure_one()
        Company = self.env["res.company"].sudo()
        company_domain = [("wework_id", "=", self.id)]
        company = Company.search(company_domain, limit=1)
        if not company:
            raise UserError(_("Please link this WeWork app to an Odoo company first."))
        return company

    agent_id = fields.Char("Wework Agent Id")
    auto_bind_policy = fields.Selection(USER_BIND_POLICIES, string="User Bind Policy", default="name")
    auto_bind_user = fields.Boolean("Auto Bind User by Name", default=False)
    company_id = fields.Char("Wework Company Id")
    contact_sync_batch_size = fields.Integer("Contact Sync Batch Size", default=50)
    contact_sync_enabled = fields.Boolean("Synchronize Organization and Users")
    contact_sync_job_count = fields.Integer(compute="_compute_contact_sync_job_count")
    contact_sync_secret = fields.Char("Contact Sync Secret")
    encoding_aes_key = fields.Char("EncodingAESKey")
    emp_sync_policy = fields.Selection(EMP_SYNC_PLOCIES, string="Employee Sync Policy", default="name")
    emp_user_create = fields.Boolean("Create User", default=False)
    file_content = fields.Char("Verify File Content")
    file_name = fields.Char("Verify File Name")
    message_type = fields.Selection(MESSAGE_TYPES, string="Message Type", default="text")
    name = fields.Char("Name")
    secret = fields.Char("Wework Secret")
    token = fields.Char("Token")
    top_department_id = fields.Many2one("hr.department", string="Top Department")

    def action_open_contact_sync_jobs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("WeWork Contact Sync Jobs"),
            "res_model": "wework.contact.sync.job",
            "view_mode": "list,form",
            "domain": [("wework_id", "=", self.id)],
        }

    def action_queue_contact_sync(self):
        self.ensure_one()
        job_model = self.env["wework.contact.sync.job"]
        department_count = job_model.action_queue_department_tree(self)
        company = self._get_config_company()
        user_domain = [("company_ids", "in", company.id), ("share", "=", False)]
        users = self.env["res.users"].sudo().search(user_domain)
        job_model.action_queue_users(users, self)
        return notification_action(
            _("Queued %s department records and their eligible users for synchronization.") % department_count,
            type="success",
        )

    def action_queue_departments_from_wework(self):
        self.ensure_one()
        company = self._get_config_company()
        self.env["wework.contact.sync.job"].action_queue_departments_from_wework(company)
        return notification_action(_("Department synchronization has been queued."), type="success")

    def action_queue_users_from_wework(self):
        self.ensure_one()
        company = self._get_config_company()
        jobs = self.env["wework.contact.sync.job"].action_queue_users_from_wework(company)
        return notification_action(
            _("Queued %s employee synchronization jobs.") % len(jobs),
            type="success",
        )

    def action_test(self):
        """Test the configured contact client through the department endpoint."""
        self.ensure_one()
        try:
            self.get_contact_client().department.list()
        except WeChatClientException as error:
            _logger.exception("[QingERP WeCom] Department connection test failed.")
            raise UserError(error)
        return notification_action(_("The WeWork connection is valid."), type="success")

    def get_authorized_url(self, url):
        """Build a WeWork OAuth URL that returns through Odoo's authorization route."""
        self.ensure_one()
        base_url = self.env["ir.config_parameter"].sudo().get_str(
            "web.base.url",
            default="",
        )
        redirect_url = f"{base_url}/qing_wework/opening_url?url={quote(url)}"
        return AUZHTORIZED_URL % (self.company_id, redirect_url)

    def get_contact_client(self):
        self.ensure_one()
        secret = self.contact_sync_secret or self.secret
        if not secret:
            raise UserError(_("Please configure a contact synchronization Secret."))
        return WeworkClient(self.company_id, secret)
