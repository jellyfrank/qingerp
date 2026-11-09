from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestThemePreferences(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.first = new_test_user(cls.env, login="qing_theme_first", groups="base.group_user")
        cls.second = new_test_user(cls.env, login="qing_theme_second", groups="base.group_user")
        cls.portal = new_test_user(cls.env, login="qing_theme_portal", groups="base.group_portal")

    def test_account_scope_and_partial_updates(self):
        Settings = self.env["res.users.settings"]
        Settings.with_user(self.first).set_qing_theme_preferences({"base": {"themeMode": "dark", "accentColor": "green"}})
        result = Settings.with_user(self.first).set_qing_theme_preferences({"base": {"sidebarState": "collapsed"}})
        self.assertEqual(result["base"], {"themeMode": "dark", "accentColor": "green", "sidebarState": "collapsed"})
        self.assertIsNone(Settings.with_user(self.second)._get_qing_theme_preferences()["base"])
        settings = Settings._find_or_create_for_user(self.first)
        with self.assertRaises(AccessError), self.cr.savepoint():
            settings.with_user(self.second).write({"qing_ui_preferences": {"themeMode": "light"}})
        with self.assertRaises(AccessError), self.cr.savepoint():
            settings.with_user(self.second).read(["qing_ui_preferences"])

    def test_bootstrap_does_not_create_settings(self):
        self.first.res_users_settings_ids.unlink()
        self.assertFalse(self.first.res_users_settings_ids)
        result = self.env["res.users.settings"].with_user(self.first)._get_qing_theme_preferences()
        self.assertIsNone(result["base"])
        self.assertFalse(self.first.res_users_settings_ids)

    def test_validation_and_portal_denial(self):
        Settings = self.env["res.users.settings"]
        with self.assertRaises(AccessError):
            Settings.with_user(self.portal).set_qing_theme_preferences({"base": {"themeMode": "dark"}})
        for value in ([], {}, {"user_id": self.second.id}, {"base": []}, {"base": {"bad": True}},
                      {"base": {"schema": True}}, {"base": {"themeMode": "invalid"}},
                      {"base": {"reducedMotion": 1}}, {"base": {"textSize": ["large"]}}):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                Settings.with_user(self.first).set_qing_theme_preferences(value)
        settings = Settings._find_or_create_for_user(self.first)
        for invalid in ({"themeMode": "bad"},):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                settings.write({"qing_ui_preferences": invalid})

    def test_chatter_position_accepts_automatic(self):
        Settings = self.env["res.users.settings"].with_user(self.first)
        for value in ("auto", "right", "bottom"):
            result = Settings.set_qing_theme_preferences({"base": {"schema": 4, "chatterPosition": value}})
            self.assertEqual(result["base"]["chatterPosition"], value)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            Settings.set_qing_theme_preferences({"base": {"chatterPosition": "left"}})

    def test_sidebar_style_accepts_known_layouts(self):
        Settings = self.env["res.users.settings"].with_user(self.first)
        for value in ("tree", "islands", "rail"):
            result = Settings.set_qing_theme_preferences({"base": {"sidebarStyle": value}})
            self.assertEqual(result["base"]["sidebarStyle"], value)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            Settings.set_qing_theme_preferences({"base": {"sidebarStyle": "cards"}})

    def test_defaults_replace_explicit_choices(self):
        Settings = self.env["res.users.settings"].with_user(self.first)
        Settings.set_qing_theme_preferences({"base": {"themeMode": "dark", "reducedMotion": True}})
        result = Settings.set_qing_theme_preferences({"base": {"themeMode": "system", "reducedMotion": False}})
        self.assertEqual(result["base"], {"themeMode": "system", "reducedMotion": False})
