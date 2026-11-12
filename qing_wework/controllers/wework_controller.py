# -*- coding: utf-8 -*-
#!/usr/bin/python3
# @Time    : 2022-09-07
# @Author  : Kevin Kong (kfx2007@163.com)

from odoo import http, _, SUPERUSER_ID
from odoo.http import request
from odoo.http.session import finalize, logout
from odoo.addons.web.controllers.utils import _get_login_redirect_url
from datetime import datetime
import logging
from dateutil.relativedelta import relativedelta
from wechatpy.work.crypto import WeChatCrypto
from wechatpy.work import parse_message
from wechatpy.exceptions import InvalidSignatureException,WeChatClientException
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

VERIFY_PREFIX = "WW_verify_"


class WeworkOdoo(http.Controller):

    def _get_company_id(self):
        """
        getting the wework client
        """
        company_id = request.env.company.sudo()
        if not company_id.wework_id:
            company_ids = request.env["res.company"].sudo().search([])
            company_id = company_ids.filtered(lambda c: c.wework_id)
            if not company_id:
                raise UserError(
                    _(
                        "Sorry, no company has correct wework configuration, please contact your admin."
                    )
                )
        return company_id

    def _handler_msg(self, msg):
        """
        handler the callback msg.
        """
        msg_obj = request.env["wework.message"].sudo()
        emp_obj = request.env["hr.employee"].sudo()
        company_id = self._get_company_id()
        if msg.type == "text":
            # Find the proper message log
            domain = [
                ("wework_user_id", "=", msg.source),
                ("company_id", "=", company_id.id),
            ]
            employee_id = emp_obj.with_company(company_id.id).search(domain, limit=1)
            if not employee_id:
                return
            domain = [
                ("employee_ids", "in", employee_id.id),
                ("company_id", "=", company_id.id),
            ]
            last_msg_id = msg_obj.search(domain, order="create_date desc", limit=1)
            data = {
                "msg_type": "text",
                "content": msg.content,
                "from_user": msg.source,
                "to_user": msg.target,
                "company_id": company_id.id,
                "res_model": last_msg_id.res_model,
                "res_id": last_msg_id.res_id,
            }
            msg_id = msg_obj.create(data)
            msg_id.send()

    def _login(self, user, redirect="/web"):
        """Complete a WeCom login through Odoo's normal session and MFA flow."""
        if not user or not user.active:
            _logger.warning("WeCom login failed: no active linked user.")
            return request.redirect_query("/web/login?oauth_error=5")
        user.ensure_one()
        logout(request.session, keep_db=True)
        request.session.update({"pre_login": user.login, "pre_uid": user.id})
        if not user._mfa_url():
            finalize(request.session, request.env)
            request.update_env(user=user, context=request.session.context)
        return request.redirect(_get_login_redirect_url(user.id, redirect=redirect))

    @http.route("/qing_wework/signin", auth="none")
    def wework_login(self, **kw):
        """Login via wework."""
        state, code, company_id = kw.get("state"), kw.get("code"), kw.get("company_id")
        # First, we validate the state used or not.
        if not request.env["wework.temp.state"].validate(state):
            _logger.warning("[Wework] wrong state for oauth login.")
            return request.redirect_query("/web/login?oauth_error=3")
        # Second, get the user id according the code returned by wework
        company_id = request.env["res.company"].sudo().browse(int(company_id))
        if not company_id:
            company_id = self._get_company_id()
        try:
            res = company_id.weworkclient.oauth.get_user_info(code)
            if res.get("errcode"):
                _logger.error(f"[Wework]oauth login failed; detail:{res.get('errmsg')}")
                return request.redirect_query("/web/login?oauth_error=4")
            user_id = res.get("UserId")
            # third, we use the corresponed user to login
            employee_id = request.env["hr.employee"].with_company(company_id).with_user(SUPERUSER_ID).search([('wework_user_id', '=', user_id)], limit=1)
            if not employee_id or not employee_id.user_id:
                _logger.error(
                    f"[Wework]oauth login failed; detail:wework user {employee_id.user_id} don't have related user."
                )
                return request.redirect_query("/web/login?oauth_error=5")
            return self._login(employee_id.user_id)
        except WeChatClientException as err:
            _logger.error(f"[Wework]login failed; detail:{err}")
            return request.redirect_query("/web/login?oauth_error=6&err=%s" % err)
        except UserError as err:
            _logger.error(f"[Wework]login failed; detail:{err}")
            return request.redirect_query("/web/login?oauth_error=5")

    @http.route(f"/{VERIFY_PREFIX}<id>", csrf=False, auth="public")
    def wework_verify(self, id):
        """verify domain settings"""
        file_name = f"{VERIFY_PREFIX}{id}"
        if file_name != request.env.company.sudo().wework_id.file_name:
            return
        return request.env.company.sudo().wework_id.file_content

    @http.route("/qing_wework/oauth", csrf=False, auth="public")
    def wework_oauth_url(self):
        """get oauth url"""
        param_obj, state_obj = (
            request.env["ir.config_parameter"],
            request.env["wework.temp.state"],
        )
        host = param_obj.sudo().get_str("web.base.url", default="")
        callback_url = f"{host}/qing_wework/authorize"
        state = state_obj.sudo().get_state()
        redirect_url = request.env.company.weworkclient.oauth.authorize_url(callback_url, state)
        return request.redirect(redirect_url, local=False)

    @http.route("/qing_wework/authorize", csrf=False, auth="public")
    def wework_authorize(self, code, state):
        """login with wework id"""
        state_obj, employee_obj = (
            request.env["wework.temp.state"].sudo(),
            request.env["hr.employee"].sudo(),
        )
        company_id = self._get_company_id()
        if not state_obj.validate(state):
            return _("Sorry, You don't have permission to login.")
        userinfo = company_id.weworkclient.oauth.get_user_info(code)
        _logger.debug(f"[WeWork]user info:{userinfo}")
        user_id = userinfo["UserId"]
        domain = [("wework_user_id", "=", user_id)]
        employee = employee_obj.with_company(company_id.id).search(domain, limit=1)
        if not employee:
            return _(
                "Sorry, we can't find proper employee infomation that match with you, please contact your administrator."
            )
        # update user private info if user ticket exists.
        if userinfo.get("user_ticket"):
            employee.user_ticket = userinfo["user_ticket"]
            employee.ticket_expire = datetime.now() + relativedelta(
                seconds=userinfo["expires_in"]
            )
            # auto sync once
            employee.sync_wework()
        return self._login(employee.user_id)

    @http.route("/qing_wework/opening_url", csrf=False, auth="public")
    def wechat_redirect_center(self, url, code=None, state=None):
        """check the access authorization and redirect target URL."""

        company_id = self._get_company_id()
        res = company_id.weworkclient.oauth.get_user_info(code)
        if res["errcode"] != 0:
            # something wrong during the process.
            return request.redirect("/web")
        wework_user_id = res["UserId"]

        # peer internal use and login ,then redirect to the target URL.
        employee_id = (
            request.env["hr.employee"]
            .sudo()
            .search([("wework_user_id", "=", wework_user_id)], limit=1)
        )
        if not employee_id.user_id:
            _logger.debug("no proper user found, redirect to login.")
            return request.redirect("/web/login")
        return self._login(employee_id.user_id, redirect=url)

    @http.route("/wework/message", csrf=False, auth="public")
    def message_callback(self, **kw):
        """
        Message callback center.
        """
        _logger.debug("[QingERP WeCom]Receive data from wework:%s" % kw)
        signature = request.params.get("msg_signature", None)
        timestamp = request.params.get("timestamp", None)
        echostr = request.params.get("echostr", None)
        nonce = request.params.get("nonce", None)

        company_id = self._get_company_id()
        wework_id = company_id.sudo().wework_id
        token = wework_id.token
        encoding_aes_key = wework_id.encoding_aes_key
        corpid = wework_id.company_id

        crypto = WeChatCrypto(token, encoding_aes_key, corpid)

        try:
            if echostr:
                return crypto.check_signature(signature, timestamp, nonce, echostr)

            data = request.httprequest.data.decode("utf-8")
            decrypted_xml = crypto.decrypt_message(data, signature, timestamp, nonce)
            msg = parse_message(decrypted_xml)
            _logger.info("[QingERP WeCom]:Data parsed from Wework:%s" % msg)
            self._handler_msg(msg)
        except InvalidSignatureException as ex:
            _logger.exception("[QingERP WeCom]Validate Error")
        except Exception as err:
            _logger.exception("[QingERP WeCom]Exception")
