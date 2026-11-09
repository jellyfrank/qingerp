from odoo import fields
from odoo.fields import Command
from odoo.addons.qing_demo_base.services.dataset import demo_environment, create_once, mark_ready


def post_init_hook(env):
    env = demo_environment(env)
    vendor = env.ref("qing_demo_cn.partner_shenzhen_zhilian")
    product = env.ref("qing_demo_cn_product.product_monitor")
    create_once(env, "qing_demo_cn_purchase.supplier_monitor", "product.supplierinfo", {
        "partner_id": vendor.id, "product_tmpl_id": product.product_tmpl_id.id,
        "price": 1050, "min_qty": 10, "currency_id": env.company.currency_id.id,
        "company_id": env.company.id,
    })
    create_once(env, "qing_demo_cn_purchase.rfq_monitors", "purchase.order", {
        "partner_id": vendor.id, "company_id": env.company.id, "currency_id": env.company.currency_id.id,
        "partner_ref": "新办公室显示器采购", "order_line": [Command.create({
            "product_id": product.id, "name": product.name, "product_qty": 20,
            "uom_id": product.uom_id.id, "price_unit": 1050,
            "date_planned": fields.Datetime.now(), "tax_ids": [Command.clear()],
        })],
    })
    mark_ready(env, "qing_demo_cn_purchase")
