from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestPortalApiKeyDuration(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("portal.allow_api_keys", "True")
        cls.portal_user = new_test_user(
            cls.env,
            login="qing-api-portal-key-test",
            groups="base.group_portal",
        )
        cls.internal_user = new_test_user(
            cls.env,
            login="qing-api-internal-key-test",
            groups="base.group_user,base.group_user_regular",
        )

    def test_portal_default_is_one_year(self):
        self.assertEqual(self.env.ref("base.group_portal").api_key_duration, 365)

        wizard_model = self.env["res.users.apikeys.description"].with_user(self.portal_user)
        durations = dict(wizard_model._selection_duration())
        self.assertIn("1", durations)
        self.assertIn("30", durations)
        self.assertIn("365", durations)
        self.assertEqual(wizard_model._default_duration(), "365")
        self.assertEqual(wizard_model.default_get(["duration"])["duration"], "365")

        wizard = wizard_model.create({"name": "Portal integration", "scope": "qing_api"})
        self.assertEqual(wizard.duration, "365")
        self.assertEqual(
            wizard.expiration_date.date(),
            fields.Date.today() + timedelta(days=365),
        )
        self.env["res.users.apikeys"].with_user(self.portal_user)._check_expiration_date(
            wizard.expiration_date
        )

        with self.assertRaises(ValidationError):
            self.env["res.users.apikeys"].with_user(self.portal_user)._check_expiration_date(
                fields.Datetime.now() + timedelta(days=366)
            )

    def test_internal_default_stays_native(self):
        wizard_model = self.env["res.users.apikeys.description"].with_user(self.internal_user)
        self.assertEqual(wizard_model._default_duration(), "30")
