from types import SimpleNamespace
from unittest.mock import patch

from odoo import api
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestDebugAccess(TransactionCase):
    def test_non_admin_url_and_existing_session_are_cleared(self):
        for login, group in (
            ('debug_ordinary', 'base.group_user'),
            ('debug_user_admin', 'base.group_erp_manager'),
            ('debug_portal', 'base.group_portal'),
        ):
            user = new_test_user(self.env, login=login, groups=group)
            for args in ({'debug': '1'}, {'debug': 'assets,tests'}, {}):
                request = SimpleNamespace(env=self.env(user=user),
                    session=SimpleNamespace(uid=user.id, debug='assets'),
                    httprequest=SimpleNamespace(args=args))
                with patch('odoo.addons.web.models.ir_http.request', request), \
                        patch('odoo.addons.qing_web_ui.models.ir_http.request', request):
                    self.env['ir.http']._handle_debug()
                self.assertEqual(request.session.debug, '')

    def test_system_admin_can_use_native_debug_modes(self):
        admin = new_test_user(self.env, login='debug_admin', groups='base.group_system')
        request = SimpleNamespace(env=self.env(user=admin),
            session=SimpleNamespace(uid=admin.id, debug=''),
            httprequest=SimpleNamespace(args={'debug': 'assets,tests'}))
        with patch('odoo.addons.web.models.ir_http.request', request), \
                patch('odoo.addons.qing_web_ui.models.ir_http.request', request):
            self.env['ir.http']._handle_debug()
        self.assertEqual(request.session.debug, 'assets,tests')

    def test_pre_authentication_environment_uses_session_identity(self):
        for login, group, expected in (
            ('debug_pre_auth_member', 'base.group_user', ''),
            ('debug_pre_auth_admin', 'base.group_system', 'assets'),
        ):
            user = new_test_user(self.env, login=login, groups=group)
            request = SimpleNamespace(env=api.Environment(self.env.cr, None, {}),
                session=SimpleNamespace(uid=user.id, debug=''),
                httprequest=SimpleNamespace(args={'debug': 'assets'}))
            self.assertFalse(request.env.user)
            with patch('odoo.addons.web.models.ir_http.request', request), \
                    patch('odoo.addons.qing_web_ui.models.ir_http.request', request):
                self.env['ir.http']._handle_debug()
            self.assertEqual(request.session.debug, expected)
