from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestMenuOrder(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.first = new_test_user(cls.env, login="qing_order_first", groups="base.group_user")
        cls.second = new_test_user(cls.env, login="qing_order_second", groups="base.group_user")
        cls.portal = new_test_user(cls.env, login="qing_order_portal", groups="base.group_portal")
        action = cls.env["ir.actions.act_window"].create({"name": "Menu order test", "res_model": "res.partner", "view_mode": "list,form"})
        cls.menus = cls.env["ir.ui.menu"].create([
            {"name": name, "action": f"ir.actions.act_window,{action.id}", "sequence": sequence,
             "group_ids": [Command.set(cls.env.ref("base.group_user").ids)]}
            for name, sequence in [("Order A", 101), ("Order B", 102)]
        ])

    def test_order_is_personal_and_does_not_write_shared_menu(self):
        Settings = self.env["res.users.settings"]
        reverse = list(reversed(self.menus.ids))
        sequence = self.menus.mapped("sequence")
        self.assertEqual(Settings.with_user(self.first).set_qing_app_menu_order(reverse), reverse)
        first_settings = Settings._find_or_create_for_user(self.first)
        second_settings = Settings._find_or_create_for_user(self.second)
        self.assertEqual(first_settings.qing_app_menu_order, reverse)
        self.assertFalse(second_settings.qing_app_menu_order)
        self.assertEqual(self.menus.mapped("sequence"), sequence)
        with self.assertRaises(AccessError), self.cr.savepoint():
            first_settings.with_user(self.second).write({"qing_app_menu_order": []})
        Settings.with_user(self.first).set_qing_app_menu_order([])
        self.assertFalse(first_settings.qing_app_menu_order)

    def test_permissions_and_payload_validation(self):
        Settings = self.env["res.users.settings"]
        with self.assertRaises(AccessError):
            Settings.with_user(self.portal).set_qing_app_menu_order(self.menus.ids)
        for value in ({}, [True], ["1"], [-1], [1] * 1001):
            with self.assertRaises(ValidationError):
                Settings.with_user(self.first).set_qing_app_menu_order(value)
        restricted = self.env["ir.ui.menu"].create({
            "name": "Restricted order", "action": f"ir.actions.act_window,{self.menus[0].action.id}",
            "group_ids": [Command.set(self.env.ref("base.group_system").ids)],
        })
        result = Settings.with_user(self.first).set_qing_app_menu_order([self.menus[0].id, restricted.id, self.menus[0].id, 999999999])
        self.assertEqual(result, self.menus[:1].ids)
