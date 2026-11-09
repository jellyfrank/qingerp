from odoo import fields
from odoo.fields import Command
from odoo.addons.qing_demo_base.services.dataset import demo_environment, create_once, mark_ready
from .services.payments import register_demo_payment


def post_init_hook(env):
    env = demo_environment(env)
    if not env.company.chart_template:
        env["account.chart.template"].try_loading("cn", env.company, install_demo=False)
    if env.company.chart_template not in ("cn", "cn_large_bis"):
        raise ValueError("中国演示财务需要中国会计科目表，不自动改写已有科目表。")
    taxes = env["account.tax"]
    for kind, partner_key, price, tax_use in (
        ("out_invoice", "partner_qingdao_huachuang", 10000, "sale"),
        ("in_invoice", "partner_shenzhen_zhilian", 5000, "purchase"),
    ):
        domain = [("company_id", "=", env.company.id), ("type_tax_use", "=", tax_use),
                  ("amount_type", "=", "percent"), ("amount", "=", 13), ("price_include", "=", False)]
        tax = taxes.search(domain, limit=1)
        if not tax:
            raise ValueError("未找到中国科目表的 13% 增值税：%s" % tax_use)
        move = create_once(env, "qing_demo_cn_account." + kind, "account.move", {
            "move_type": kind, "partner_id": env.ref("qing_demo_cn." + partner_key).id,
            "company_id": env.company.id, "currency_id": env.company.currency_id.id,
            "invoice_date": fields.Date.today(), "ref": "中文演示：办公设备结算",
            "invoice_line_ids": [Command.create({
                "product_id": env.ref("qing_demo_cn_product.product_monitor").id,
                "name": "办公设备结算演示", "quantity": 1, "price_unit": price,
                "tax_ids": [Command.set(tax.ids)],
            })],
        })
        if move.state == "draft":
            move.action_post()
        if kind == "out_invoice":
            register_demo_payment(env, move, "qing_demo_cn_account.payment_customer")
    mark_ready(env, "qing_demo_cn_account")
