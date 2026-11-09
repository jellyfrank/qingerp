"""Enforce the company IP whitelist and record sign-in attempts."""
import logging

from odoo import SUPERUSER_ID, api, models
from odoo.exceptions import AccessDenied
from odoo.http import request

from ..tools import mommy_base_loaded

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = 'res.users'

    def _check_credentials(self, credential, env):
        auth_info = super()._check_credentials(credential, env)
        if request and not self.company_id._qing_is_login_ip_allowed(self, request.httprequest.remote_addr):
            raise AccessDenied(self.env._('Your IP address is not allowed to sign in.'))
        return auth_info

    def _login(self, credential, user_agent_env):
        try:
            auth_info = super()._login(credential, user_agent_env)
        except AccessDenied as error:
            self._qing_record_login(credential, False, str(error) or self.env._('Authentication failed.'))
            raise
        self._qing_record_login(credential, auth_info.get('uid'), False)
        return auth_info

    @api.model
    def _qing_record_login(self, credential, user_id, failure_reason):
        """Record an interactive password sign-in in its own transaction.

        A failed attempt rolls the request back, so the log uses a separate cursor.
        """
        if not request or credential.get('type') != 'password' or mommy_base_loaded(self.env):
            return
        values = {
            'failure_reason': failure_reason or False,
            'ip_address': request.httprequest.remote_addr or 'n/a',
            'login': (credential.get('login') or '')[:256],
            'success': bool(user_id),
            'user_id': user_id or False,
        }
        try:
            with self.env.registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                if user_id:
                    values['company_id'] = env['res.users'].browse(user_id).company_id.id
                env['qing.login.log'].create(values)
        except Exception:
            _logger.exception('Could not record the sign-in attempt.')
