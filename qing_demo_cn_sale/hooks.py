from odoo.fields import Command
from odoo.addons.qing_demo_base.services.dataset import demo_environment, create_once, mark_ready


def post_init_hook(env):
    env = demo_environment(env)
    partner = env.ref("qing_demo_cn.partner_qingdao_huachuang")
    pricelist = env.ref("qing_demo_cn_product.pricelist_cny")
    partner.property_product_pricelist = pricelist
    lines = []
    for key, quantity, price in (("chair", 10, 2899), ("installation", 1, 2000)):
        product = env.ref("qing_demo_cn_product.product_" + key)
        lines.append(Command.create({"product_id": product.id, "product_uom_qty": quantity,
                                     "price_unit": price, "tax_ids": [Command.clear()]}))
    create_once(env, "qing_demo_cn_sale.quotation_office", "sale.order", {
        "partner_id": partner.id, "company_id": env.company.id, "pricelist_id": pricelist.id,
        "client_order_ref": "新办公室 IT 设备采购", "order_line": lines,
    })
    mark_ready(env, "qing_demo_cn_sale")
