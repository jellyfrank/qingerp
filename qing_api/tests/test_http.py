import datetime

from odoo.tests import HttpCase, new_test_user, tagged

from ..compatibility import get_api_key_scope, set_config_parameter


@tagged("post_install", "-at_install")
class TestQingApiHttp(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.api_user = new_test_user(cls.env, login="qing-api-test", groups="base.group_user")
        expiration = datetime.datetime.now() + datetime.timedelta(hours=1)
        cls.api_key = cls.api_user.with_user(cls.api_user).env["res.users.apikeys"]._generate(
            get_api_key_scope(),
            "Qing API test",
            expiration,
        )

    def test_bearer_ping_and_request_id(self):
        request_id = "test-request-id"
        response = self.url_open(
            "/api/v1/system/ping",
            headers={
                "Authorization": "Bearer %s" % self.api_key,
                "X-Request-Id": request_id,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-Id"], request_id)
        self.assertEqual(response.json(), {
            "company_id": self.api_user.company_id.id,
            "status": "ok",
            "user_id": self.api_user.id,
        })

    def test_invalid_scope_is_rejected(self):
        rpc_key = self.api_user.with_user(self.api_user).env["res.users.apikeys"]._generate(
            "rpc",
            "RPC test",
            datetime.datetime.now() + datetime.timedelta(hours=1),
        )
        response = self.url_open(
            "/api/v1/system/ping",
            headers={"Authorization": "Bearer %s" % rpc_key},
        )
        self.assertEqual(response.status_code, 401)

    def test_authenticated_session_does_not_replace_bearer_key(self):
        self.authenticate("qing-api-test", "qing-api-test")
        response = self.url_open(
            "/api/v1/system/ping",
            headers={
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
            },
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTHENTICATION_REQUIRED")


@tagged("post_install", "-at_install")
class TestQingApiDocs(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        new_test_user(cls.env, login="qing-api-docs", groups="base.group_user")

    def setUp(self):
        super().setUp()
        self._set_documentation_ui("scalar")
        self._set_documentation_access("authenticated")

    def _set_documentation_access(self, access_mode):
        set_config_parameter(
            self.env,
            "qing_api.documentation_access",
            access_mode,
        )

    def _set_documentation_ui(self, documentation_ui):
        set_config_parameter(
            self.env,
            "qing_api.documentation_ui",
            documentation_ui,
        )

    def test_docs_and_openapi_are_available_to_signed_in_users(self):
        self.authenticate("qing-api-docs", "qing-api-docs")
        docs_response = self.url_open("/api/docs")
        self.assertEqual(docs_response.status_code, 200)
        self.assertIn("/qing_api/static/lib/scalar/scalar.js", docs_response.text)
        self.assertIn("hideTestRequestButton: true", docs_response.text)
        self.assertNotIn("https://", docs_response.text)

        openapi_response = self.url_open("/api/openapi.json")
        self.assertEqual(openapi_response.status_code, 200)
        self.assertEqual(openapi_response.json()["openapi"], "3.0.3")
        self.assertIn("/api/v1/system/ping", openapi_response.json()["paths"])
        etag = openapi_response.headers["ETag"]

        cached_response = self.url_open(
            "/api/openapi.json",
            headers={"If-None-Match": etag},
        )
        self.assertEqual(cached_response.status_code, 304)

    def test_documentation_interface_can_be_changed(self):
        self.authenticate("qing-api-docs", "qing-api-docs")
        self._set_documentation_ui("swagger")
        swagger_response = self.url_open("/api/docs")
        self.assertEqual(swagger_response.status_code, 200)
        self.assertIn("/qing_api/static/lib/swagger-ui/swagger-ui.css", swagger_response.text)

        self._set_documentation_ui("redoc")
        redoc_response = self.url_open("/api/docs")
        self.assertEqual(redoc_response.status_code, 200)
        self.assertIn("/qing_api/static/lib/redoc/redoc.standalone.js", redoc_response.text)
        self.assertIn("disableGoogleFont: true", redoc_response.text)

    def test_authenticated_mode_rejects_public_users(self):
        docs_response = self.url_open("/api/docs")
        self.assertEqual(docs_response.status_code, 403)
        openapi_response = self.url_open("/api/openapi.json")
        self.assertEqual(openapi_response.status_code, 403)

    def test_public_mode_allows_public_users(self):
        self._set_documentation_access("public")
        docs_response = self.url_open("/api/docs")
        self.assertEqual(docs_response.status_code, 200)
        openapi_response = self.url_open("/api/openapi.json")
        self.assertEqual(openapi_response.status_code, 200)
