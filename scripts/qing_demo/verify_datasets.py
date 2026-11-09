"""Database assertions after install_apps.py, followed by rollback."""
from odoo.addons.qing_demo_server.services.profile_installer import validate_database
from odoo.addons.qing_demo_server.services.profile_registry import get_profile

assert env.cr.dbname.startswith("qing_demo_")
validate_database(env, get_profile("cn_standard"))
assert env.ref("base.user_admin").lang == "zh_CN"
assert env.ref("base.user_admin").tz == "Asia/Shanghai"
assert env.ref("base.user_admin").company_id == env.ref("qing_demo_cn.company_cn")
assert env.ref("qing_demo_cn.company_cn").currency_id.name == "CNY"
assert env.ref("qing_demo_cn.partner_qingdao_huachuang").country_id.code == "CN"
quotation = env.ref("qing_demo_cn_sale.quotation_office")
assert quotation.currency_id.name == "CNY" and quotation.amount_total == 30990
purchase = env.ref("qing_demo_cn_purchase.rfq_monitors")
assert purchase.amount_total == 21000 and purchase.currency_id.name == "CNY"
receipt = env.ref("qing_demo_cn_stock.receipt_monitors")
assert receipt.state == "done"
assert sum(receipt.move_ids.mapped("quantity")) == 20
invoice = env.ref("qing_demo_cn_account.out_invoice")
bill = env.ref("qing_demo_cn_account.in_invoice")
assert invoice.state == bill.state == "posted"
assert invoice.amount_total == 11300 and bill.amount_total == 5650
assert invoice.amount_residual == 0
payment = env.ref("qing_demo_cn_account.payment_customer")
assert payment.amount == 11300 and payment.state in ("paid", "reconciled")
assert abs(sum(invoice.line_ids.mapped("balance"))) < 0.01
assert abs(sum(bill.line_ids.mapped("balance"))) < 0.01
assert len(env.ref("qing_demo_cn_mrp.bom_desk").bom_line_ids) == 2
assert env.ref("qing_demo_cn_mrp.production_desk").product_qty == 5
assert env.ref("qing_demo_cn_crm.opportunity_office").expected_revenue == 30990
story_order = env.ref("qing_demo_cn_story.sale_office")
story_purchase = env.ref("qing_demo_cn_story.purchase_office")
story_invoice = env.ref("qing_demo_cn_story.invoice_office")
assert story_order.opportunity_id == env.ref("qing_demo_cn_crm.opportunity_office")
assert story_order.state == "sale" and story_order.amount_total == 16990
assert story_purchase.state == "purchase" and story_purchase.amount_total == 21000
assert story_order.picking_ids and all(p.state == "done" for p in story_order.picking_ids)
assert story_purchase.picking_ids and all(p.state == "done" for p in story_purchase.picking_ids)
assert story_invoice in story_order.invoice_ids
assert story_invoice.amount_total == 16990 and story_invoice.amount_residual == 0
for module in ("qing_demo_cn", "qing_demo_cn_product", "qing_demo_cn_sale", "qing_demo_cn_purchase",
               "qing_demo_cn_stock", "qing_demo_cn_account", "qing_demo_cn_crm", "qing_demo_cn_mrp", "qing_demo_cn_story"):
    assert env["ir.config_parameter"].get_str("qing_demo.dataset." + module) == "1.0"
print("PASS company, language, partners, prices, sales, purchase, receipt, accounting, CRM and MRP", flush=True)
env.cr.rollback()
