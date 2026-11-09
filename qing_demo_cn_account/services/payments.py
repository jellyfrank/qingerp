from odoo.addons.qing_demo_base.services.dataset import create_once


def register_demo_payment(env, invoice, xmlid):
    existing = env.ref(xmlid, raise_if_not_found=False)
    if existing:
        return existing
    journal = create_once(env, "qing_demo_cn_account.journal_bank_cn", "account.journal", {
        "name": "人民币演示银行", "code": "CNDEM", "type": "bank",
        "company_id": env.company.id, "currency_id": env.company.currency_id.id,
    })
    # A dedicated demo journal records immediate bank receipts. Do not change
    # the outstanding-account setup of any pre-existing journal.
    method = journal.inbound_payment_method_line_ids.filtered(
        lambda line: line.payment_method_id.code == "manual"
    )[:1]
    if not method:
        raise ValueError("演示银行缺少手工收款方式")
    method.payment_account_id = journal.default_account_id
    wizard = env["account.payment.register"].with_context(
        active_model="account.move", active_ids=invoice.ids,
    ).create({"journal_id": journal.id, "payment_method_line_id": method.id,
              "amount": invoice.amount_residual})
    payment = wizard._create_payments()
    payment.ensure_one()
    env["ir.model.data"]._update_xmlids([{"xml_id": xmlid, "record": payment, "noupdate": True}])
    return payment
