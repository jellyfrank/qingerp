import json
from html import escape

from odoo import _, http
from odoo.exceptions import AccessDenied, UserError
from odoo.http import request
from odoo.http.session import logout
from odoo.tools import file_open
from odoo.addons.qing_api.api import ApiError, ApiSchema, Boolean, List, Object, String, api_route

from ..constants import API_PATH, API_VERSION, B2B_AUTH

TAGS = ["B2B Ordering"]
SETTINGS_META = b'<meta name="qing-b2b-settings">'


def current_website():
    return request.env["ir.http"]._b2b_website()


def buyer_website():
    website = current_website()
    request.env.user._b2b_check_buyer(website)
    return website


class B2BSettingsResponse(ApiSchema):
    version = String(required=True)
    name = String(required=True)
    logo = String(required=True)
    language = String(required=True)
    delivery = String(required=True)
    payment = String(required=True)
    capabilities = List(String(), required=True)
    color = String()
    banner = String()
    theme = String()


class B2BLoginRequest(ApiSchema):
    login = String(required=True, min_length=1, max_length=254)
    password = String(required=True, min_length=1, max_length=512, write_only=True)
    remember = Boolean(default=False)
    device = String(default="Mobile ordering", max_length=100)
    totp = String(pattern=r"^[0-9]{6}$", write_only=True)


class B2BLoginResponse(ApiSchema):
    token = String(required=True)
    expires_at = String(required=True)


class B2BContact(ApiSchema):
    name = String(required=True)
    email = String(required=True)
    phone = String(required=True)


class B2BProfileResponse(ApiSchema):
    name = String(required=True)
    company = String(required=True)
    login = String(required=True)
    email = String(required=True)
    phone = String(required=True)
    salesperson = Object(B2BContact)


class B2BEmptyResponse(ApiSchema):
    ok = Boolean(required=True)


class B2BPasswordRequest(ApiSchema):
    current_password = String(required=True, min_length=1, max_length=512, write_only=True)
    new_password = String(required=True, min_length=1, max_length=512, write_only=True)


class B2BOrderingController(http.Controller):
    @http.route("/b2b", type="http", auth="public", website=True, sitemap=False, methods=["GET"])
    def home(self):
        website = current_website()
        if not website.b2b_enabled:
            return request.not_found()
        with file_open("qing_b2b_base/static/app/index.html", "rb") as entry:
            body = entry.read()
        if SETTINGS_META in body:
            settings = B2BSettingsResponse.dump(self._website_settings(website))
            content = escape(json.dumps(settings, ensure_ascii=False), quote=True)
            meta = f'<meta name="qing-b2b-settings" content="{content}">'
            body = body.replace(SETTINGS_META, meta.encode(), 1)
        return request.make_response(body, [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Cache-Control", "no-store"),
            ("Referrer-Policy", "no-referrer"),
        ])

    @api_route(API_PATH + "/settings", methods=["GET"], operation_id="getB2BSettings",
               tags=TAGS, summary="Read ordering website information and capabilities",
               auth="public", save_session=False, response_model=B2BSettingsResponse)
    def settings(self):
        website = current_website()
        if not website.b2b_enabled:
            raise ApiError("B2B_DISABLED", _("Ordering is unavailable on this website."), 404)
        return self._website_settings(website)

    def _website_settings(self, website):
        return {
            "version": API_VERSION,
            "name": website.name,
            "logo": "/web/image/website/%s/logo" % website.id if website.logo else "",
            "language": website.default_lang_id.code,
            "delivery": website.b2b_delivery_note or "",
            "payment": website.b2b_payment_note or "",
            "capabilities": website._b2b_capabilities(),
            **website._b2b_theme(),
        }

    @api_route(API_PATH + "/login", methods=["POST"], operation_id="loginB2BBuyer",
               tags=TAGS, summary="Authenticate a buyer and create an ordering device",
               auth="public", save_session=False, max_body_bytes=4096,
               request_model=B2BLoginRequest, response_model=B2BLoginResponse)
    def login(self, data):
        website = current_website()
        credential = {"type": "password", "login": data["login"], "password": data["password"]}
        user_agent = {"interactive": True, "REMOTE_ADDR": request.httprequest.remote_addr,
                      "HTTP_HOST": request.httprequest.host}
        try:
            auth_info = request.env["res.users"].with_context(website_id=website.id).authenticate(
                credential, user_agent,
            )
            buyer = request.env["res.users"].browse(auth_info["uid"])
            buyer._b2b_check_buyer(website)
            if auth_info.get("mfa") != "skip" and buyer.sudo()._mfa_url():
                if buyer.sudo()._mfa_type() != "totp":
                    raise ApiError("NATIVE_LOGIN_REQUIRED", _("Use the website sign-in for this account."), 401)
                if not data.get("totp"):
                    raise ApiError("MFA_REQUIRED", _("Enter your authentication code."), 401)
                with buyer._assert_can_auth(user=buyer.id):
                    buyer.with_user(buyer).sudo()._check_credentials(
                        {"type": "totp", "token": int(data["totp"])}, user_agent,
                    )
            return buyer._b2b_issue_device(website, data["remember"], data["device"].strip() or "Mobile ordering")
        except (AccessDenied, UserError) as error:
            raise ApiError("LOGIN_FAILED", _("Unable to sign in with these credentials."), 401) from error

    @http.route("/b2b/native/device", methods=["POST"], type="http", auth="user")
    def native_device(self, csrf_token=None, remember=None):
        website = buyer_website()
        device = request.env.user._b2b_issue_device(website, remember in ("true", "1"), "Mobile ordering")
        logout(request.session, keep_db=True)
        return request.make_json_response(device, headers=[("Cache-Control", "no-store")])

    @http.route("/b2b/native/session", methods=["GET"], type="http", auth="user")
    def native_session(self):
        buyer_website()
        return request.make_json_response({"csrf_token": request.csrf_token()}, headers=[
            ("Cache-Control", "no-store"),
        ])

    @api_route(API_PATH + "/profile", methods=["GET"], operation_id="getB2BProfile",
               tags=TAGS, summary="Read the signed-in buyer's contact details",
               response_model=B2BProfileResponse, **B2B_AUTH)
    def profile(self):
        return request.env.user._b2b_profile(current_website())

    @api_route(API_PATH + "/logout", methods=["POST"], operation_id="logoutB2BDevice",
               tags=TAGS, summary="Revoke this ordering device", response_model=B2BEmptyResponse, **B2B_AUTH)
    def logout(self):
        buyer_website()
        token = request.httprequest.headers["Authorization"].split(None, 1)[1]
        request.env["res.users.apikeys"]._b2b_current_device(token)._remove()
        return {"ok": True}

    @api_route(API_PATH + "/password", methods=["POST"], operation_id="changeB2BPassword",
               tags=TAGS, summary="Change the buyer password and revoke ordering devices",
               request_model=B2BPasswordRequest, response_model=B2BEmptyResponse,
               max_body_bytes=4096, **B2B_AUTH)
    def password(self, data):
        buyer_website()
        try:
            if not request.env.user.change_password(data["current_password"], data["new_password"]):
                raise AccessDenied()
        except (AccessDenied, UserError) as error:
            raise ApiError("PASSWORD_REJECTED", _("The password could not be changed."), 422) from error
        return {"ok": True}
