from odoo.addons.qing_demo_base.services.dataset import demo_environment, create_once, mark_ready

PRODUCTS = (
    ("monitor", "27 寸显示器", 1699, 1050, "consu"),
    ("chair", "人体工学椅", 2899, 1650, "consu"),
    ("laptop", "商务笔记本电脑", 8999, 6200, "consu"),
    ("installation", "设备安装服务", 2000, 800, "service"),
    ("implementation", "企业实施服务", 20000, 10000, "service"),
    ("support", "年度技术支持", 36000, 18000, "service"),
    ("desk", "智能办公桌", 3999, 2300, "consu"),
    ("desktop", "办公桌面板", 800, 500, "consu"),
    ("frame", "升降桌架", 1800, 1200, "consu"),
)


def post_init_hook(env):
    env = demo_environment(env)
    category = create_once(env, "qing_demo_cn_product.category_office", "product.category", {"name": "中文演示 / 办公设备"})
    for key, name, price, cost, kind in PRODUCTS:
        create_once(env, "qing_demo_cn_product.product_" + key, "product.product", {
            "name": name, "default_code": "CN-" + key.upper(), "type": kind,
            "list_price": price, "standard_price": cost, "categ_id": category.id,
        })
    for key, name in (("product.product_product_1", "办公桌（原生演示）"),
                       ("product.product_product_2", "办公桌组合（原生演示）")):
        product = env.ref(key, raise_if_not_found=False)
        if product:
            product.with_context(lang="en_US").name = name
            product.name = name
    create_once(env, "qing_demo_cn_product.pricelist_cny", "product.pricelist", {
        "name": "人民币标准价目表", "currency_id": env.ref("base.CNY").id, "company_id": env.company.id,
    })
    mark_ready(env, "qing_demo_cn_product")
