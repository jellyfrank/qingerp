from odoo.fields import Command
from odoo.addons.qing_demo_base.services.dataset import demo_environment, create_once, mark_ready


def post_init_hook(env):
    env = demo_environment(env)
    warehouses = env["stock.warehouse"]
    domain = [("company_id", "=", env.company.id)]
    warehouse = warehouses.search(domain, limit=1)
    if not warehouse:
        warehouse = create_once(env, "qing_demo_cn_stock.warehouse_qingdao", "stock.warehouse", {
            "name": "青岛演示仓", "code": "CNQD", "company_id": env.company.id,
            "partner_id": env.company.partner_id.id,
        })
    warehouse.name = "青岛演示仓"
    for key in ("monitor", "chair", "laptop", "desk", "desktop", "frame"):
        env.ref("qing_demo_cn_product.product_" + key).is_storable = True
    product = env.ref("qing_demo_cn_product.product_monitor")
    picking = create_once(env, "qing_demo_cn_stock.receipt_monitors", "stock.picking", {
        "partner_id": env.ref("qing_demo_cn.partner_shenzhen_zhilian").id,
        "picking_type_id": warehouse.in_type_id.id, "company_id": env.company.id,
        "location_id": env.ref("stock.stock_location_suppliers").id,
        "location_dest_id": warehouse.lot_stock_id.id, "origin": "中文演示：显示器到货",
        "move_ids": [Command.create({"product_id": product.id,
            "product_uom_qty": 20, "uom_id": product.uom_id.id,
            "location_id": env.ref("stock.stock_location_suppliers").id,
            "location_dest_id": warehouse.lot_stock_id.id})],
    })
    if picking.state == "draft":
        picking.action_confirm()
        picking.move_ids.quantity = 20
        picking.button_validate()
    if picking.state != "done":
        raise ValueError("中文演示收货未完成：%s" % picking.name)
    mark_ready(env, "qing_demo_cn_stock")
