from types import SimpleNamespace
from unittest.mock import Mock, patch

from odoo.tests import TransactionCase, tagged

from ..api import ApiError, ApiSchema, String, api_route
from ..api.request import parse_body


class SmallRequest(ApiSchema):
    value = String(required=True)


@tagged("at_install", "-post_install")
class TestQingApiRequest(TransactionCase):
    def _parse_body(self, *, body, content_length, max_body_bytes):
        http_request = Mock()
        http_request.mimetype = "application/json"
        http_request.content_length = content_length
        http_request.get_data.return_value = body
        http_request.get_json.return_value = {"value": "ok"}
        endpoint = SimpleNamespace(
            max_body_bytes=max_body_bytes,
            request_model=SmallRequest,
        )
        fake_request = SimpleNamespace(httprequest=http_request)
        with patch("odoo.addons.qing_api.api.request.request", fake_request):
            return parse_body(endpoint), http_request

    def test_body_limit_rejects_declared_oversize_before_read(self):
        with self.assertRaises(ApiError) as error:
            self._parse_body(body=b"", content_length=5, max_body_bytes=4)
        self.assertEqual(error.exception.status, 413)
        self.assertEqual(error.exception.code, "REQUEST_TOO_LARGE")

    def test_body_limit_checks_actual_size_when_length_is_unknown(self):
        with self.assertRaises(ApiError) as error:
            self._parse_body(body=b"12345", content_length=None, max_body_bytes=4)
        self.assertEqual(error.exception.status, 413)
        self.assertEqual(error.exception.code, "REQUEST_TOO_LARGE")

    def test_api_route_can_disable_session_saving(self):
        with patch("odoo.addons.qing_api.api.route.API_REGISTRY.register") as registry:
            with patch(
                "odoo.addons.qing_api.api.route.http.route",
                side_effect=lambda path, **options: lambda function: function,
            ) as route:
                api_route(
                    "/api/v1/test-session-option",
                    methods=["GET"],
                    operation_id="testSessionOption",
                    tags=["Tests"],
                    summary="Test session option",
                    response_model=SmallRequest,
                    auth="public",
                    save_session=False,
                    max_body_bytes=4096,
                    security_scheme="testBearerAuth",
                    security_description="Test customer token.",
                )(lambda controller: {"value": "ok"})

        self.assertIs(route.call_args[1]["save_session"], False)
        self.assertEqual(
            registry.call_args[0][0].max_body_bytes,
            4096,
        )
        self.assertEqual(
            registry.call_args[0][0].security_scheme,
            "testBearerAuth",
        )
