import logging

from lxml import html
from markupsafe import Markup
from odoo import api, http
from odoo.http import request, Response
from odoo.http.session import authenticate, save_session
from odoo.modules import db
from odoo.modules.registry import Registry
from odoo.addons.web.controllers.database import Database, _render_template, verify_access

from ..services.profile_registry import discover_profiles, get_profile
from ..services.profile_installer import install_profile

_logger = logging.getLogger(__name__)


class QingDatabase(Database):
    def _render_template(self, **values):
        """Retain the native template and replace only the demo checkbox row."""
        page = html.document_fromstring(str(_render_template(**values)))
        for index, checkbox in enumerate(page.xpath('//input[@name="demo"]')):
            row = checkbox.getparent().getparent()
            replacement = html.fragment_fromstring(
                '<div class="row mb-3"><label for="qing_demo_profile" '
                'class="col-md-4 col-form-label">演示环境 / Demo Environment</label>'
                '<div class="col-md-8"><select id="qing_demo_profile" name="demo_profile" '
                'class="form-select"></select><small class="text-muted">'
                '标准演示将采用所选环境的语言、国家和币种；空库保留上方选择。'
                '</small></div></div>')
            select = replacement.xpath('.//select')[0]
            select.set("id", "qing_demo_profile_%s" % index)
            replacement.xpath('.//label')[0].set("for", select.get("id"))
            for profile in discover_profiles():
                option = html.Element("option", value=profile["code"])
                option.text = profile.get("name_zh", profile["name"])
                option.set("data-language", profile.get("language", ""))
                option.set("data-country", profile.get("country_code", ""))
                select.append(option)
            row.getparent().replace(row, replacement)
        script = html.Element("script", src="/qing_demo_server/static/src/public/profile.js?v=1.0.1")
        page.find("body").append(script)
        return Markup("<!DOCTYPE html>\n" + html.tostring(page, encoding="unicode", method="html"))

    @http.route()
    def manager(self, **kw):
        if request.db:
            request.env.cr.close()
        return self._render_template()

    @http.route()
    def selector(self, **kw):
        if request.db:
            request.env.cr.close()
        return self._render_template(manage=False)

    @http.route()
    def create(self, master_pwd, name, lang, password, **post):
        # Legacy integrations retain Odoo's checkbox behavior.
        if "demo_profile" not in post:
            return super().create(master_pwd, name, lang, password, **post)
        created = False
        ready = False
        profile_name = post["demo_profile"]
        try:
            profile = get_profile(post["demo_profile"])
            profile_name = profile.get("name_zh", profile["name"])
            verify_access(master_pwd)
            if not db.DB_NAME_RE.fullmatch(name):
                raise ValueError("数据库名称无效 / Invalid database name")
            # Same two native stages as db.create(). Keeping the ownership boundary
            # explicit lets us clean up initialization errors without dropping an
            # existing database on a concurrent/duplicate-name failure.
            db._create_empty_database(name)
            created = True
            db._initialize_db(
                name, demo=profile["native_demo"], lang=profile.get("language") or lang,
                user_password=password, user_login=post["login"],
                country_code=profile.get("country_code") or post.get("country_code") or False,
                phone=post.get("phone"),
            )
            install_profile(name, profile)
            ready = True
            credential = {"login": post["login"], "password": password, "type": "password"}
            with Registry(name).cursor() as cr:
                env = api.Environment(cr, None, {})
                authenticate(request.session, env, credential)
                save_session(request, env)
                request.session.db = name
            return request.redirect("/odoo")
        except Exception as exc:
            _logger.exception("Demo profile database creation failed for %s", name)
            cleanup = ""
            if created and not ready:
                try:
                    db.drop(name)
                    cleanup = "；已清理本次新建数据库。"
                except Exception:
                    _logger.exception("Failed to clean up new demo database %s", name)
                    cleanup = "；自动清理失败，请管理员检查该新建数据库。"
            if ready:
                cleanup = "；数据库已创建完成，请从登录页登录。"
            error = "演示环境 %s 创建失败：%s%s" % (profile_name, exc, cleanup)
            return Response(self._render_template(error=error), status=422)
