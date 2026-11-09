from odoo.tests import TransactionCase, tagged

from ..api.openapi import build_openapi


@tagged("at_install", "-post_install")
class TestOpenApi(TransactionCase):
    def test_system_endpoint_is_generated(self):
        document = build_openapi(installed_modules={"qing_api"})
        operation = document["paths"]["/api/v1/system/ping"]["get"]
        self.assertEqual(document["openapi"], "3.0.3")
        self.assertEqual(document["servers"], [{"url": "/"}])
        self.assertEqual(operation["operationId"], "pingSystem")
        self.assertEqual(operation["security"], [{"bearerAuth": []}])
        self.assertIn("SystemPingResponse", document["components"]["schemas"])
        self.assertIn("ApiErrorResponse", document["components"]["schemas"])
