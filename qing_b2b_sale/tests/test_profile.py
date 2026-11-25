from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestB2BProfile(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.config.settings"].create({"group_product_pricelist": True}).execute()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        internal = [Command.set(cls.env.ref("base.group_user").ids)]
        cls.fallback = Users.create({"name": "Fallback Seller", "login": "profile-fallback", "group_ids": internal})
        cls.fallback.partner_id.write({"email": "fallback@example.invalid", "phone": "+34 900 000 001"})
        cls.owner = Users.create({"name": "Account Owner", "login": "profile-owner", "group_ids": internal})
        team = cls.env["crm.team"].create({"name": "Profile Team", "company_id": cls.env.company.id})
        values = {
            "name": "Profile Shop", "company_id": cls.env.company.id, "b2b_enabled": True,
            "b2b_sales_team_id": team.id, "b2b_salesperson_id": cls.fallback.id,
        }
        if "warehouse_id" in cls.env["website"]._fields:
            values["warehouse_id"] = cls.env["stock.warehouse"].search([("company_id", "=", cls.env.company.id)], limit=1).id
        cls.website = cls.env["website"].create(values)
        cls.customer = cls.env["res.partner"].create({"name": "Costa Norte SL", "is_company": True})
        contact = cls.env["res.partner"].create({
            "name": "Ana Buyer", "parent_id": cls.customer.id, "email": "ana@example.invalid", "phone": "+34 600 000 002",
        })
        cls.buyer = Users.create({
            "name": "Ana Buyer", "login": "profile-buyer", "partner_id": contact.id,
            "group_ids": [Command.set(cls.env.ref("base.group_portal").ids)], "b2b_website_id": cls.website.id,
        })

    def test_profile_describes_buyer_and_fallback_salesperson(self):
        self.assertEqual(self.buyer.with_user(self.buyer)._b2b_profile(self.website), {
            "name": "Ana Buyer", "company": "Costa Norte SL", "login": "profile-buyer",
            "email": "ana@example.invalid", "phone": "+34 600 000 002",
            "salesperson": {"name": "Fallback Seller", "email": "fallback@example.invalid", "phone": "+34 900 000 001"},
        })

    def test_customer_salesperson_comes_first(self):
        self.customer.user_id = self.owner
        profile = self.buyer.with_user(self.buyer)._b2b_profile(self.website)
        self.assertEqual(profile["salesperson"]["name"], "Account Owner")

    def test_profile_omits_salesperson_when_none_is_eligible(self):
        self.website.b2b_salesperson_id = False
        self.assertNotIn("salesperson", self.buyer.with_user(self.buyer)._b2b_profile(self.website))

    def test_profile_is_refused_on_another_website(self):
        other = self.env["website"].create({"name": "Other Shop", "company_id": self.env.company.id})
        with self.assertRaises(AccessError):
            self.buyer.with_user(self.buyer)._b2b_profile(other)
