from odoo.fields import Command
from odoo.addons.qing_demo_base.services.dataset import demo_environment, create_once, mark_ready

PARTNERS = (
    ("partner_qingdao_huachuang", "青岛华创科技有限公司", "青岛", "266000", "软件园路 88 号", "huachuang@example.com"),
    ("partner_shanghai_boyuan", "上海博远贸易有限公司", "上海", "200000", "浦东大道 168 号", "boyuan@example.com"),
    ("partner_shenzhen_zhilian", "深圳智联电子有限公司", "深圳", "518000", "科技园路 66 号", "zhilian@example.com"),
)


def pre_init_hook(env):
    demo_environment(env)


def post_init_hook(env):
    env = demo_environment(env)
    # Odoo 20 account/demo/account_demo.xml always loads generic_coa on
    # base.main_company. Keep those native, potentially posted examples separate.
    company = create_once(env, "qing_demo_cn.company_cn", "res.company", {
        "name": "青岛欧姆演示科技有限公司", "country_id": env.ref("base.cn").id,
        "currency_id": env.ref("base.CNY").id, "city": "青岛", "street": "软件园路 1 号",
        "zip": "266000", "state_id": False,
    })
    # The native loader runs as root and assumes its default company is the
    # native main company (notably stock/MRP demo XML). Only the human
    # administrator switches to CN; dataset helpers set their own company.
    env.ref("base.user_root").write({"company_ids": [Command.link(company.id)]})
    env.ref("base.user_admin").write({
        "company_ids": [Command.link(company.id)], "company_id": company.id,
    })
    env = demo_environment(env)
    env.ref("base.main_company").write({
        "name": "Odoo 原生演示公司", "country_id": env.ref("base.us").id,
        "currency_id": env.ref("base.USD").id,
    })
    admin = env.ref("base.user_admin")
    admin.write({"name": "演示管理员", "lang": "zh_CN", "tz": "Asia/Shanghai"})
    country = env.ref("base.cn")
    for key, name, city, zipcode, street, email in PARTNERS:
        create_once(env, "qing_demo_cn." + key, "res.partner", {
            "name": name, "is_company": True, "country_id": country.id,
            "city": city, "zip": zipcode, "street": street, "email": email, "lang": "zh_CN",
        })
    create_once(env, "qing_demo_cn.contact_zhangwei", "res.partner", {
        "name": "张伟", "parent_id": env.ref("qing_demo_cn.partner_qingdao_huachuang").id,
        "email": "zhangwei@example.com", "lang": "zh_CN", "function": "采购负责人",
    })
    # Preserve native XML IDs and relations, skip missing records.
    for key, name, city in (("base.res_partner_2", "华创办公设备（演示）", "青岛"),
                            ("base.res_partner_12", "博远家居（演示）", "上海"),
                            ("base.res_partner_1", "智联家具制造（演示）", "深圳")):
        partner = env.ref(key, raise_if_not_found=False)
        if partner:
            partner.with_context(no_vat_validation=True).write({
                "name": name, "country_id": country.id, "state_id": False, "city": city,
                "street": "演示园区 1 号", "street2": False, "zip": "266000" if city == "青岛" else "200000" if city == "上海" else "518000",
                "vat": False, "phone": False, "website": False, "lang": "zh_CN",
            })
    params = env["ir.config_parameter"]
    for key, value in {"enabled": "True", "profile": "cn_standard", "profile_version": "1.0",
                       "language": "zh_CN", "country": "CN", "currency": "CNY", "native_demo": "True",
                       "company_id": str(company.id)}.items():
        params.set_str("qing_demo." + key, value)
    mark_ready(env, "qing_demo_cn")
