from odoo.fields import Command
from odoo.addons.qing_demo_base.services.dataset import demo_environment, create_once, mark_ready


def post_init_hook(env):
    env = demo_environment(env)
    product = env.ref("qing_demo_cn_product.product_desk")
    components = [env.ref("qing_demo_cn_product.product_" + key) for key in ("desktop", "frame")]
    bom = create_once(env, "qing_demo_cn_mrp.bom_desk", "mrp.bom", {
        "product_tmpl_id": product.product_tmpl_id.id, "product_qty": 1, "type": "normal",
        "company_id": env.company.id, "code": "智能办公桌标准装配",
        "bom_line_ids": [Command.create({"product_id": component.id, "product_qty": 1}) for component in components],
    })
    create_once(env, "qing_demo_cn_mrp.production_desk", "mrp.production", {
        "product_id": product.id, "product_qty": 5, "uom_id": product.uom_id.id,
        "bom_id": bom.id, "company_id": env.company.id, "origin": "中文演示：智能办公桌装配",
    })
    mark_ready(env, "qing_demo_cn_mrp")
