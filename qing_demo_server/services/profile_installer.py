"""Bootstrap only the database just created by the authenticated database controller."""
from odoo import api
from odoo.api import SUPERUSER_ID
from odoo.modules.registry import Registry


def validate_database(env, profile):
    modules = env["ir.module.module"]
    domain = [("name", "=", "base"), ("state", "=", "installed")]
    base = modules.search(domain)
    if not base or bool(base.demo) != profile["native_demo"]:
        raise ValueError("基础模块或演示数据初始化不完整 / Base initialization incomplete")
    admin = env.ref("base.user_admin")
    company = admin.company_id
    if profile.get("language") and admin.lang != profile["language"]:
        raise ValueError("管理员语言初始化失败 / Administrator language mismatch")
    if profile.get("country_code") and company.country_id.code != profile["country_code"]:
        raise ValueError("公司国家初始化失败 / Company country mismatch")
    failure_model = env.get("ir.demo_failure")
    if failure_model is not None:
        domain = []
        failures = failure_model.search(domain)
        if failures:
            raise ValueError("演示数据加载失败 / Demo datasets failed: %s" %
                             ", ".join(failures.mapped("module_id.name")))


def install_profile(db_name, profile):
    registry = Registry(db_name)
    with registry.cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})
        validate_database(env, profile)
        if not profile.get("root_module"):
            return
        company = env.ref("base.main_company")
        currency_model = env["res.currency"].with_context(active_test=False)
        domain = [("name", "=", profile["currency_code"])]
        currency = currency_model.search(domain, limit=1)
        if not currency:
            raise ValueError("Profile currency is not available")
        currency.active = True
        company.currency_id = currency
        params = env["ir.config_parameter"]
        metadata = {
            "enabled": "True", "profile": profile["code"],
            "profile_version": profile["version"], "language": profile["language"],
            "country": profile["country_code"], "currency": profile["currency_code"],
            "native_demo": "True", "status": "installing",
        }
        for key, value in metadata.items():
            params.set_str("qing_demo." + key, value)
        modules = env["ir.module.module"]
        modules.update_list()
        domain = [("name", "=", profile["root_module"])]
        root = modules.search(domain)
        if not root or root.state == "uninstallable":
            raise ValueError("Profile root module is not installable")
        root.button_install()
        cr.commit()
    registry = Registry.new(db_name, update_module=True)
    with registry.cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})
        validate_database(env, profile)
        modules = env["ir.module.module"]
        domain = [("name", "=", profile["root_module"])]
        root = modules.search(domain)
        if root.state != "installed":
            raise ValueError("Profile root module failed to install")
        if env.ref("base.user_admin").company_id.currency_id.name != profile["currency_code"]:
            raise ValueError("Profile currency mismatch")
        env["ir.config_parameter"].set_str("qing_demo.status", "ready")
        cr.commit()
