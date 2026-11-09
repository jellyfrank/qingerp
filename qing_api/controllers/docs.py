import hashlib
import json
import logging

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request

from ..api.openapi import build_openapi
from ..compatibility import get_config_parameter


_logger = logging.getLogger(__name__)

DOCUMENTATION_UI_PARAMETER = "qing_api.documentation_ui"
DOCUMENTATION_UIS = {"redoc", "scalar", "swagger"}
DOCUMENTATION_ACCESS_PARAMETER = "qing_api.documentation_access"
DOCUMENTATION_ACCESS_MODES = {"authenticated", "public"}


class QingApiDocsController(http.Controller):
    @staticmethod
    def _get_parameter(name, default):
        return get_config_parameter(request.env, name, default)

    def _check_access(self):
        access_mode = self._get_parameter(
            DOCUMENTATION_ACCESS_PARAMETER,
            "authenticated",
        )
        if access_mode not in DOCUMENTATION_ACCESS_MODES:
            _logger.warning(
                "Unknown Qing API documentation access mode %r; requiring authentication",
                access_mode,
            )
            access_mode = "authenticated"
        if access_mode == "authenticated" and request.env.user._is_public():
            raise AccessError(request.env._("You are not allowed to view API documentation."))

    @http.route("/api/docs", type="http", auth="public", methods=["GET"], readonly=True)
    def docs(self, **kwargs):
        self._check_access()
        documentation_ui = self._get_parameter(DOCUMENTATION_UI_PARAMETER, "scalar")
        if documentation_ui not in DOCUMENTATION_UIS:
            _logger.warning("Unknown Qing API documentation UI %r; using Scalar", documentation_ui)
            documentation_ui = "scalar"
        html = getattr(self, "_render_%s" % documentation_ui)()
        return request.make_response(html, headers=self._documentation_headers())

    @staticmethod
    def _documentation_headers():
        return {
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": (
                "default-src 'self'; connect-src 'self'; font-src 'self' data:; "
                "img-src 'self' data: blob:; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; worker-src 'self' blob:"
            ),
            "Content-Type": "text/html; charset=utf-8",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        }

    @staticmethod
    def _render_scalar():
        return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>QingERP API</title>
</head>
<body>
  <div id="app"></div>
  <script src="/qing_api/static/lib/scalar/scalar.js"></script>
  <script>
    Scalar.createApiReference("#app", {
      url: "/api/openapi.json",
      hideClientButton: true,
      hideTestRequestButton: true,
      persistAuth: false,
      theme: "default",
      withDefaultFonts: false
    });
  </script>
</body>
</html>"""

    @staticmethod
    def _render_swagger():
        return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>QingERP API</title>
  <link rel="stylesheet" href="/qing_api/static/lib/swagger-ui/swagger-ui.css">
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="/qing_api/static/lib/swagger-ui/swagger-ui-bundle.js"></script>
  <script src="/qing_api/static/lib/swagger-ui/swagger-ui-standalone-preset.js"></script>
  <script>
    SwaggerUIBundle({
      url: "/api/openapi.json",
      dom_id: "#swagger-ui",
      deepLinking: true,
      displayRequestDuration: true,
      persistAuthorization: false,
      presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
      supportedSubmitMethods: [],
      layout: "StandaloneLayout"
    });
  </script>
</body>
</html>"""

    @staticmethod
    def _render_redoc():
        return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>QingERP API</title>
</head>
<body>
  <div id="redoc"></div>
  <script src="/qing_api/static/lib/redoc/redoc.standalone.js"></script>
  <script>
    Redoc.init("/api/openapi.json", {
      disableGoogleFont: true,
      hideDownloadButton: true
    }, document.getElementById("redoc"));
  </script>
</body>
</html>"""

    @http.route("/api/openapi.json", type="http", auth="public", methods=["GET"], readonly=True)
    def openapi(self, **kwargs):
        self._check_access()
        Module = request.env["ir.module.module"].sudo()
        installed_modules = set(Module.search([("state", "=", "installed")]).mapped("name"))
        document = build_openapi(installed_modules=installed_modules)
        payload = json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        etag = hashlib.sha256(payload.encode()).hexdigest()
        if request.httprequest.if_none_match.contains(etag):
            return request.make_response("", status=304, headers={"ETag": '"%s"' % etag})
        response = request.make_response(payload, headers={
            "Cache-Control": "private, max-age=300",
            "Content-Type": "application/json; charset=utf-8",
        })
        response.set_etag(etag)
        return response
