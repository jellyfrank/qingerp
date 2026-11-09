from lxml import etree

from odoo.tests import TransactionCase, tagged

from ..api import ApiSchema, Integer, List, SchemaValidationError, String
from ..models.res_config_settings import (
    DOCUMENTATION_ACCESS_SELECTION,
    DOCUMENTATION_UI_SELECTION,
)


class ChildSchema(ApiSchema):
    name = String(required=True, min_length=2)


class ParentSchema(ApiSchema):
    count = Integer(required=True, minimum=1)
    labels = List(String(), required=True, min_items=1)


@tagged("at_install", "-post_install")
class TestApiSchema(TransactionCase):
    def test_documentation_settings_extend_advanced_settings(self):
        settings_view = self.env.ref("qing_api.view_res_config_settings_form")
        arch = etree.fromstring(settings_view.get_combined_arch().encode())
        advanced_settings = arch.xpath("//app[@name='qing_advanced_settings']")

        self.assertEqual(len(advanced_settings), 1)
        self.assertFalse(arch.xpath("//app[@name='qing_api']"))
        self.assertEqual(
            advanced_settings[0].xpath(
                "./block[@name='qing_api_documentation']/setting/field/@name"
            ),
            ["qing_api_documentation_access", "qing_api_documentation_ui"],
        )

    def test_scalar_is_default_documentation_interface(self):
        defaults = self.env["res.config.settings"].default_get(["qing_api_documentation_ui"])
        self.assertEqual(defaults["qing_api_documentation_ui"], "scalar")
        self.assertEqual([value for value, _label in DOCUMENTATION_UI_SELECTION], [
            "scalar",
            "swagger",
            "redoc",
        ])

    def test_authenticated_is_default_documentation_access(self):
        defaults = self.env["res.config.settings"].default_get([
            "qing_api_documentation_access",
        ])
        self.assertEqual(defaults["qing_api_documentation_access"], "authenticated")
        self.assertEqual(
            [value for value, _label in DOCUMENTATION_ACCESS_SELECTION],
            ["authenticated", "public"],
        )

    def test_load_and_reject_unknown_fields(self):
        values = ParentSchema.load({"count": "2", "labels": ["one"]})
        self.assertEqual(values, {"count": 2, "labels": ["one"]})
        with self.assertRaises(SchemaValidationError) as error:
            ParentSchema.load({"count": 0, "labels": [], "other": True})
        codes = {detail["code"] for detail in error.exception.details}
        self.assertEqual(codes, {"MINIMUM", "MIN_ITEMS", "UNKNOWN_FIELD"})

    def test_dump_rejects_missing_required_response_field(self):
        with self.assertRaises(SchemaValidationError) as error:
            ParentSchema.dump({"count": 1})
        self.assertEqual(error.exception.message, "Response validation failed")
        self.assertEqual(error.exception.details[0]["field"], "$.labels")
