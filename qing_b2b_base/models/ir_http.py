from odoo import models
from odoo.http import request
from werkzeug.exceptions import Forbidden, HTTPException

from ..constants import API_PATH


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _b2b_website(cls):
        sites = request.env["website"].sudo()
        return (request.env.website.sudo() or sites.browse(request.env.context.get("host_id"))
                or sites.get_all()[:1])

    @classmethod
    def _b2b_allowed_origin(cls):
        origin = request.httprequest.headers.get("Origin")
        if not origin:
            return None
        website = cls._b2b_website()
        allowed = (website.b2b_cors_origins or "").split()
        if origin == request.httprequest.host_url.rstrip("/") or origin in allowed:
            return origin
        return None

    @classmethod
    def _b2b_cors_headers(cls, origin):
        return [
            ("Access-Control-Allow-Origin", origin),
            ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
            ("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Request-Id, X-B2B-Language"),
            ("Vary", "Origin"),
        ]

    @classmethod
    def _authenticate(cls, endpoint):
        if request.httprequest.path.startswith(API_PATH + "/") and request.httprequest.headers.get("Origin"):
            origin = cls._b2b_allowed_origin()
            if not origin:
                raise Forbidden()
            # Odoo adds OPTIONS to HTTP routes automatically. Complete the allowed
            # preflight before native Bearer authentication, with no business dispatch.
            if request.httprequest.method == "OPTIONS":
                if request.httprequest.headers.get("Access-Control-Request-Method") not in ("GET", "POST"):
                    raise Forbidden()
                raise HTTPException(response=request.make_response("", headers=cls._b2b_cors_headers(origin), status=204))
        return super()._authenticate(endpoint)

    @classmethod
    def _pre_dispatch(cls, rule, args):
        super()._pre_dispatch(rule, args)
        path = request.httprequest.path
        if path.startswith(API_PATH + "/"):
            language = request.httprequest.headers.get("X-B2B-Language")
            code = {"en": "en_US", "zh": "zh_CN", "es": "es_ES"}.get(language)
            if code and code in dict(request.env["res.lang"].get_installed()):
                request.update_context(lang=code)
            return
        segments = set(path.split("/"))
        login_complete = path.rstrip("/") == "/web/login_successful"
        if request.session.uid and (login_complete or segments.intersection({"my", "shop"})):
            user = request.env["res.users"].sudo().browse(request.session.uid)
            if user.b2b_website_id and user._is_portal():
                destination = "/b2b?native=1" if login_complete else "/b2b"
                raise HTTPException(response=request.redirect(destination, code=303))

    @classmethod
    def _b2b_apply_cors_headers(cls, response):
        if request.httprequest.path.startswith(API_PATH + "/") and (origin := cls._b2b_allowed_origin()):
            response.headers.extend(cls._b2b_cors_headers(origin))
        return response

    @classmethod
    def _post_dispatch(cls, response):
        super()._post_dispatch(response)
        cls._b2b_apply_cors_headers(response)

    @classmethod
    def _handle_error(cls, exception):
        return cls._b2b_apply_cors_headers(super()._handle_error(exception))
