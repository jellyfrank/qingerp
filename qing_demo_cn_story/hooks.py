from odoo import fields
from odoo.fields import Command
from odoo.addons.qing_demo_base.services.dataset import demo_environment, create_once, mark_ready
from odoo.addons.qing_demo_cn_account.services.payments import register_demo_payment


def _complete_pickings(pickings):
    for picking in pickings.filtered(lambda item: item.state not in ("done", "cancel")):
        picking.action_assign()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
        picking.button_validate()
        if picking.state != "done":
            raise ValueError("中文演示收发货未完成：%s" % picking.name)


def post_init_hook(env):
    env = demo_environment(env)
    product = env.ref("qing_demo_cn_product.product_monitor")
    vendor = env.ref("qing_demo_cn.partner_shenzhen_zhilian")
    customer = env.ref("qing_demo_cn.partner_qingdao_huachuang")
    purchase = create_once(env, "qing_demo_cn_story.purchase_office", "purchase.order", {
        "partner_id": vendor.id, "company_id": env.company.id, "currency_id": env.company.currency_id.id,
        "partner_ref": "华创办公室项目备货", "order_line": [Command.create({
            "product_id": product.id, "name": "27 寸显示器", "product_qty": 20,
            "uom_id": product.uom_id.id, "price_unit": 1050,
            "date_planned": fields.Datetime.now(), "tax_ids": [Command.clear()],
        })],
    })
    if purchase.state in ("draft", "sent"):
        purchase.button_confirm()
    _complete_pickings(purchase.picking_ids)
    order = create_once(env, "qing_demo_cn_story.sale_office", "sale.order", {
        "partner_id": customer.id, "company_id": env.company.id,
        "pricelist_id": env.ref("qing_demo_cn_product.pricelist_cny").id,
        "opportunity_id": env.ref("qing_demo_cn_crm.opportunity_office").id,
        "client_order_ref": "华创新办公室显示器交付",
        "order_line": [Command.create({"product_id": product.id, "product_uom_qty": 10,
                                       "price_unit": 1699, "tax_ids": [Command.clear()]})],
    })
    if order.state in ("draft", "sent"):
        order.action_confirm()
    _complete_pickings(order.picking_ids)
    invoice = env.ref("qing_demo_cn_story.invoice_office", raise_if_not_found=False)
    if not invoice:
        invoice = order._create_invoices()
        invoice.ensure_one()
        invoice.invoice_date = fields.Date.today()
        invoice.action_post()
        env["ir.model.data"]._update_xmlids([
            {"xml_id": "qing_demo_cn_story.invoice_office", "record": invoice, "noupdate": True},
        ])
    register_demo_payment(env, invoice, "qing_demo_cn_story.payment_office")
    mark_ready(env, "qing_demo_cn_story")
