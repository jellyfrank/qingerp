#!/usr/bin/python3
# @Time    : 2022-09-07
# @Author  : Kevin Kong (kfx2007@163.com)

from odoo import http, _
from odoo.http import request
from odoo.exceptions import AccessDenied
from wechatpy.oauth import WeChatOAuth
from odoo.addons.auth_oauth.controllers.main import OAuthLogin, fragment_to_query_string
from odoo.addons.web.controllers.utils import ensure_db
import json
import werkzeug
import logging

_logger = logging.getLogger(__name__)

class OAuthWeWork(OAuthLogin):

    def _get_company_id(self):
        """get default company id"""
        return request.env['res.company'].sudo().search([('wework_id','!=',False)],order="id",limit=1)

    def get_state(self, provider):
        state = super().get_state(provider)
        if provider['name'] == "Wework OAuth Provider":
            state = request.env['wework.temp.state'].get_state()
        return state

    def list_providers(self):
        """Add Wework provider to login Page."""
        providers = super().list_providers()
        # try:
        #     providers = request.env['auth.oauth.provider'].sudo().search_read([('enabled', '=', True)])
        # except Exception:
        #     providers = []
        for provider in providers:
            if provider['name'] == "Wework OAuth Provider":
                company_id = self._get_company_id()
                return_url = request.httprequest.url_root + f'qing_wework/signin?company_id={company_id.id}'
                state = self.get_state(provider)
                params = dict(
                    appid=provider['corp_id'],
                    agentid=provider['client_id'],
                    redirect_uri=return_url,
                    state=state,
                )
                provider['auth_link'] = "%s?%s" % (
                    provider['auth_endpoint'], werkzeug.urls.url_encode(params))
        return providers

    @http.route()
    def web_login(self, *args, **kw):
        ensure_db()
        if request.httprequest.method == 'GET' and request.session.uid and request.params.get('redirect'):
            # Redirect if already logged in and redirect param is present
            return request.redirect(request.params.get('redirect'))
        providers = self.list_providers()

        response = super().web_login(*args, **kw)
        if response.is_qweb:
            error = request.params.get('oauth_error')
            msg = request.params.get('err')
            if error == '1':
                error = _("Sign up is not allowed on this database.")
            elif error == '2':
                error = _("Access Denied")
            elif error == '3':
                error = _("You do not have access to this database or your invitation has expired. Please ask for an invitation and be sure to follow the link in your invitation email.")
            elif error == '4':
                error = _('Someting was wrong when talking to wechat server, please try again later.')
            elif error == '5':
                error = _('You do not have a related user to login.')
            elif error == '6':
                error = msg
            else:
                error = None

            response.qcontext['providers'] = providers
            if error:
                response.qcontext['error'] = error

        return response
