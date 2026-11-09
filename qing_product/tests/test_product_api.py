import datetime

from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.qing_api.api.openapi import build_openapi
from odoo.addons.qing_api.compatibility import get_api_key_scope


@tagged("post_install", "-at_install")
class TestProductApi(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(
            cls.env, login="qing-product-manager",
            groups="base.group_user,product.group_product_manager,base.group_system",
        )
        cls.reader = new_test_user(cls.env, login="qing-product-reader", groups="base.group_user")
        cls.keys = {}
        for user in (cls.manager, cls.reader):
            cls.keys[user.id] = user.with_user(user).env["res.users.apikeys"]._generate(
                get_api_key_scope(), "Qing Product test",
                datetime.datetime.now() + datetime.timedelta(hours=1),
            )

    def _request(self, user, method, path, payload=None):
        headers = {"Authorization": "Bearer %s" % self.keys[user.id]}
        return self.url_open(path, method=method, json=payload, headers=headers)

    def test_all_crud_operations_are_in_openapi(self):
        self.authenticate("qing-product-manager", "qing-product-manager")
        response = self.url_open("/api/openapi.json")
        self.assertEqual(response.status_code, 200)
        paths = response.json()["paths"]
        self.assertEqual(paths, build_openapi(installed_modules={"qing_api", "qing_product"})["paths"])
        for collection in ("products", "product-categories", "unit-categories", "units"):
            self.assertIn("post", paths["/api/v1/%s" % collection])
            self.assertIn("patch", paths["/api/v1/%s/{%s_id}" % (collection, {
                "products": "product", "product-categories": "category",
                "unit-categories": "category", "units": "unit",
            }[collection])])
            self.assertIn("delete", paths["/api/v1/%s/{%s_id}" % (collection, {
                "products": "product", "product-categories": "category",
                "unit-categories": "category", "units": "unit",
            }[collection])])

    def test_existing_records_have_unique_identifiers(self):
        for model_name in ("product.template", "product.category", "uom.uom"):
            records = self.env[model_name].with_context(active_test=False).search([])
            identifiers = records.mapped("qing_api_uid")
            self.assertTrue(all(identifiers))
            self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_create_update_delete_and_dependent_records(self):
        category = self._request(self.manager, "POST", "/api/v1/product-categories", {"name": "API category"})
        self.assertEqual(category.status_code, 201)
        category_id = category.json()["id"]

        unit_category = self._request(self.manager, "POST", "/api/v1/unit-categories", {"name": "API reference"})
        self.assertEqual(unit_category.status_code, 201)
        unit_category_id = unit_category.json()["id"]
        reference_unit_id = unit_category.json()["reference_unit_id"]

        unit = self._request(self.manager, "POST", "/api/v1/units", {
            "name": "API pack", "category_id": unit_category_id, "factor": "6",
        })
        self.assertEqual(unit.status_code, 201)
        unit_id = unit.json()["id"]

        product = self._request(self.manager, "POST", "/api/v1/products", {
            "name": "API product", "category_id": category_id,
            "unit_id": reference_unit_id, "list_price": "12.50",
        })
        self.assertEqual(product.status_code, 201)
        product_id = product.json()["id"]
        self.assertEqual(product.json()["list_price"], "12.5")

        blocked_category = self._request(self.manager, "DELETE", "/api/v1/product-categories/%s" % category_id)
        self.assertEqual(blocked_category.status_code, 409)
        blocked_unit_category = self._request(self.manager, "DELETE", "/api/v1/unit-categories/%s" % unit_category_id)
        self.assertEqual(blocked_unit_category.status_code, 409)

        updates = (
            ("products", product_id, {"name": "API product updated"}),
            ("product-categories", category_id, {"name": "API category updated"}),
            ("unit-categories", unit_category_id, {"name": "API reference updated"}),
            ("units", unit_id, {"name": "API box", "factor": "12"}),
        )
        for collection, public_id, values in updates:
            response = self._request(self.manager, "PATCH", "/api/v1/%s/%s" % (collection, public_id), values)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["name"], values["name"])
        for collection, public_id in (
            ("products", product_id), ("units", unit_id),
            ("product-categories", category_id), ("unit-categories", unit_category_id),
        ):
            response = self._request(self.manager, "DELETE", "/api/v1/%s/%s" % (collection, public_id))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["deleted"])

    def test_native_permissions_and_input_validation(self):
        denied = self._request(self.reader, "POST", "/api/v1/products", {"name": "Denied"})
        self.assertEqual(denied.status_code, 403)
        denied_unit = self._request(self.reader, "POST", "/api/v1/unit-categories", {"name": "Denied"})
        self.assertEqual(denied_unit.status_code, 403)
        invalid = self._request(self.manager, "POST", "/api/v1/units", {
            "name": "Invalid", "category_id": "uc_" + "0" * 32, "factor": "1",
        })
        self.assertEqual(invalid.status_code, 422)

    def test_nested_unit_factor_is_relative_to_reference_unit(self):
        Units = self.env["uom.uom"]
        root = Units.create({"name": "API root", "relative_factor": 1})
        parent = Units.create({"name": "API parent", "relative_uom_id": root.id, "relative_factor": 10})
        child = Units.create({"name": "API child", "relative_uom_id": parent.id, "relative_factor": 2})
        response = self._request(self.manager, "PATCH", "/api/v1/units/uom_%s" % child.qing_api_uid, {
            "factor": "30",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["category_id"], "uc_%s" % root.qing_api_uid)
        self.assertEqual(response.json()["factor"], "30.0")
        self.assertEqual(child.relative_uom_id, parent)
        self.assertEqual(child.relative_factor, 3)
