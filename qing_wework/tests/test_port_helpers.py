import datetime

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from ..tools.actions import confirm_action, notification_action
from ..tools.date_helpers import FormulaDateUtil


@tagged("post_install", "-at_install")
class TestWeworkPortHelpers(TransactionCase):
    def test_confirm_dialog_runs_only_allowed_callbacks(self):
        departments = self.env["hr.department"]
        action = confirm_action(departments, "Sync", "Sure?", callback="_sync_employees")
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        self.assertEqual((action["target"], wizard.res_model, wizard.callback), ("new", "hr.department", "_sync_employees"))

        wizard.callback = "unlink"
        with self.assertRaises(UserError):
            wizard.action_confirm()

    def test_reload_notification_refreshes_the_view(self):
        action = notification_action("Done", type="success", reload=True)
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["next"]["tag"], "soft_reload")
        self.assertNotIn("next", notification_action("Done")["params"])

    def test_formula_date_helpers_keep_solutions_results(self):
        first, last = FormulaDateUtil.get_first_and_last_day(2024, 2)
        self.assertEqual((first, last), (datetime.date(2024, 2, 1), datetime.date(2024, 2, 29)))
        monday, sunday = FormulaDateUtil.get_week_range(datetime.date(2026, 11, 11))
        self.assertEqual((monday, sunday), (datetime.date(2026, 11, 9), datetime.date(2026, 11, 15)))
        start, end = FormulaDateUtil.get_season_range()
        self.assertTrue(start.endswith("-01") and end[5:7] in ("03", "06", "09", "12"))
