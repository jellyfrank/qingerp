from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests import HttpCase, tagged
from odoo.tests.common import new_test_user

from ..controllers.wework_controller import WeworkOdoo


@tagged("-at_install", "post_install")
class TestWeworkSession(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.wecom_user = new_test_user(cls.env, login="wecom_session_test", groups="base.group_user")
        cls.env["hr.employee"].create({
            "name": "WeCom session test",
            "user_id": cls.wecom_user.id,
            "wework_user_id": "wecom-session-user",
        })

    def _callback(self, mfa=False):
        self.url_open("/web/login")
        previous_sid = self.opener.cookies.get("session_id")
        state = self.env["wework.temp.state"].get_state()
        client = SimpleNamespace(oauth=SimpleNamespace(get_user_info=lambda code: {"UserId": "wecom-session-user"}))
        company = SimpleNamespace(id=self.env.company.id, weworkclient=client)
        user_type = type(self.env["res.users"])
        with patch.object(WeworkOdoo, "_get_company_id", return_value=company), patch.object(
            user_type, "_mfa_url", return_value="/web/login/totp" if mfa else None
        ):
            response = self.url_open("/qing_wework/authorize", params={"code": "test-code", "state": state}, allow_redirects=False)
        return response, previous_sid, self.opener.cookies.get("session_id")

    def test_callback_rotates_session_and_authenticates_linked_user(self):
        response, previous_sid, new_sid = self._callback()
        self.assertEqual(response.status_code, 303)
        self.assertNotEqual(previous_sid, new_sid)
        info = self.url_open("/web/session/get_session_info", json={"jsonrpc": "2.0", "params": {}}).json()
        self.assertEqual(info["result"]["uid"], self.wecom_user.id)
        self.assertEqual(info["result"]["username"], self.wecom_user.login)

    def test_callback_keeps_mfa_user_in_partial_session(self):
        response, previous_sid, new_sid = self._callback(mfa=True)
        self.assertEqual(response.status_code, 303)
        self.assertIn("/web/login/totp", response.headers["Location"])
        self.assertNotEqual(previous_sid, new_sid)
        info = self.url_open("/web/session/get_session_info", json={"jsonrpc": "2.0", "params": {}}).json()
        self.assertNotEqual(info.get("result", {}).get("uid"), self.wecom_user.id)

    def test_browser_callback_opens_web_client_as_linked_user(self):
        state = self.env["wework.temp.state"].get_state()
        client = SimpleNamespace(oauth=SimpleNamespace(get_user_info=lambda code: {"UserId": "wecom-session-user"}))
        company = SimpleNamespace(id=self.env.company.id, weworkclient=client)
        with patch.object(WeworkOdoo, "_get_company_id", return_value=company):
            self.browser_js(
                "/qing_wework/authorize?code=test-code&state=" + state,
                "if (!document.querySelector('.o_web_client')) { throw new Error('Web client did not load'); } console.log('test successful');",
                ready="!!document.querySelector('.o_web_client')",
                login=None,
                timeout=90,
            )
