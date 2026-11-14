#!/usr/bin/python3
# @Time    : 2022-01-23
# @Author  : Kevin Kong (kfx2007@163.com)

from odoo import api, fields, models, _
from odoo.fields import Command
from odoo.tools import BinaryBytes
from datetime import datetime
from odoo.exceptions import UserError
import logging
import requests

_logger = logging.getLogger(__name__)


class hr_employee(models.Model):

    _inherit = "hr.employee"

    ticket_expire = fields.Datetime("Ticket Expired")
    user_ticket = fields.Char("User Ticket")
    wework_user_id = fields.Char("Wework User Id")

    @api.model_create_multi
    def create(self, vals_list):
        employees = super().create(vals_list)
        if not self.env.context.get("skip_wework_contact_sync"):
            self.env["wework.contact.sync.job"].action_queue_users(employees.user_id)
        return employees

    def write(self, vals):
        result = super().write(vals)
        sync_fields = {
            "active", "department_id", "job_title", "mobile_phone", "name",
            "user_id", "work_email", "work_phone",
        }
        if sync_fields.intersection(vals) and not self.env.context.get("skip_wework_contact_sync"):
            self.env["wework.contact.sync.job"].action_queue_users(self.user_id)
        return result

    @api.model
    def action_invite_autorization(self):
        """
        sync wework users.
        """
        context = self.env.context
        if context.get("active_model") != self._name:
            return
        self.browse(context.get("active_ids") or context.get("active_id") or []).action_oauth_sync()

    def action_oauth_sync(self):
        """
        send the oauth message to wework user.
        """
        cfg_obj = self.env['ir.config_parameter'].sudo()
        state_obj = self.env['wework.temp.state'].sudo()
        message_obj = self.env['wework.message'].sudo()
        host = cfg_obj.get_str("web.base.url", default="")
        redirect_uri = f"{host}/qing_wework/authorize"
        agent_id = self.env.company.wework_id.agent_id
        state = state_obj.get_state()
        _logger.debug(f"[WeWork]open oauth url:{redirect_uri} {agent_id}")
        url = self.env.company.weworkclient.oauth.authorize_url(
            redirect_uri, state=state, agent_id=agent_id, scope="snsapi_privateinfo")
        message_data = {
            "employee_ids": [Command.set(self.filtered(lambda e:e.wework_user_id).ids)],
            "msg_type":"card",
            "description":_("Please click this card to authorize Odoo to sync your infomation."),
            "title":_("User Authorization"),
            "need_authorized": False,
            "url": url
        }
        message = message_obj.create(message_data)
        message.send()

    def sync_wework(self):
        """
        sync info from wework.
        """
        try:
            self.ensure_one()
            now = datetime.now()
            if not self.user_ticket or now > self.ticket_expire:
                return self._qing_notify(
                    _("User ticket has expried, please authorized in wework app, then try again."),
                    title=_("Warning"), notification_type="warning", sticky=True)
            res = self.env.company.weworkclient.oauth.get_user_detail(self.user_ticket)
            _logger.debug(f"[WeWork]result from wework:{res}")
            if not res.get("errcode") == 0:
                raise UserError(_("Sync from wework error, message:%s") % res['errmsg'])
            # [TODO] get avatar & qrcode
            if res.get("biz_mail"):
                self.write({
                    "mobile_phone": res['mobile'],
                    "work_email": res['biz_mail'],
                    "image_1920": BinaryBytes(requests.get(res['avatar']).content)
                })
                self.user_partner_id.email = res['biz_mail']
        except Exception as err:
            _logger.exception("[QingERP WeCom]Getting privat info error")
            raise UserError(err)

    @api.model
    def get_user_from_wework(self, user_id):
        """get user according wework user id."""
        try:
            _logger.debug(f"[QingERP WeCom]get user from wework:{user_id}")
            employee = self.sudo().search(
                [('wework_user_id', '=', user_id)], limit=1)
            return employee.user_id
        except Exception as err:
            _logger.exception("[QingERP WeCom]Getting user from wework error")
            raise UserError(err)


class hr_employee_public(models.Model):

    _inherit = "hr.employee.public"

    wework_user_id = fields.Char("Wework User Id")
    ticket_expire = fields.Datetime("Ticket Expired")
    user_ticket = fields.Char("User Ticket")
