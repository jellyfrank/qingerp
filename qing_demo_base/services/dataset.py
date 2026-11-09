"""Small dataset primitives shared only by Qing demo addons."""
from odoo.exceptions import UserError


def demo_environment(env):
    """Reject accidental installation into a real or non-Chinese database."""
    modules = env["ir.module.module"]
    domain = [("name", "=", "base")]
    base = modules.search(domain, limit=1)
    company = env.ref("qing_demo_cn.company_cn", raise_if_not_found=False) or env.ref("base.main_company")
    if not base.demo or company.country_id.code != "CN" or company.currency_id.name != "CNY":
        raise UserError("中文演示模块仅可用于已启用原生演示数据、中国国家及人民币的数据库。")
    return env(context=dict(env.context, lang="zh_CN", allowed_company_ids=[company.id],
                            tracking_disable=True, mail_create_nosubscribe=True))


def create_once(env, xmlid, model, values):
    """Use native external IDs; never overwrite records edited after seeding."""
    record = env.ref(xmlid, raise_if_not_found=False)
    if record:
        return record
    record = env[model].create(values)
    module, name = xmlid.split(".", 1)
    env["ir.model.data"].create({
        "module": module, "name": name, "model": model,
        "res_id": record.id, "noupdate": True,
    })
    return record


def mark_ready(env, module):
    env["ir.config_parameter"].set_str("qing_demo.dataset." + module, "1.0")
