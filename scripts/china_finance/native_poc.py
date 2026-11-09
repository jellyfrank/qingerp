"""Run with Odoo shell on a disposable qing_cnf_p0_* database; always rollback.

Synthetic, tax-free accounting mechanics only, not a tax-policy or client acceptance test.
"""
import json

from freezegun import freeze_time
from odoo import Command, fields
from odoo.exceptions import UserError

if not env.cr.dbname.startswith('qing_cnf_p0_'):
    raise RuntimeError('Use a disposable database named qing_cnf_p0_*')


def complete(pickings):
    for picking in pickings:
        picking.action_assign()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
        picking.button_validate()
        assert picking.state == 'done', picking.name


def snapshot(case_env, product, stage):
    moves = case_env['account.move'].search([
        ('company_id', '=', case_env.company.id), ('state', '=', 'posted'),
    ], order='id')
    for move in moves:
        assert case_env.company.currency_id.is_zero(sum(move.line_ids.mapped('balance')))
    return {
        'stage': stage, 'quantity': product.qty_available,
        'stock_value': product.total_value, 'unit_cost': product.standard_price,
        'posted_moves': [{
            'type': move.move_type, 'date': str(move.date), 'reference': move.ref,
            'lines': [{'code': line.account_id.code, 'balance': line.balance}
                      for line in move.line_ids if line.account_id],
        } for move in moves],
    }


def pay(case_env, invoice, amount):
    # Follow the native payment-register flow also used by qing_demo_cn_account.
    journal = case_env['account.journal'].search([
        ('company_id', '=', case_env.company.id), ('type', '=', 'bank'),
    ], limit=1)
    methods = (journal.inbound_payment_method_line_ids if invoice.is_sale_document()
               else journal.outbound_payment_method_line_ids)
    method = methods.filtered(lambda item: item.code == 'manual')[:1]
    assert method and journal.default_account_id
    # Only the synthetic company's journal is changed. Immediate bank settlement
    # deliberately excludes outstanding clearing/bank statement matching from this PoC.
    method.payment_account_id = journal.default_account_id
    wizard = case_env['account.payment.register'].with_context(
        active_model='account.move', active_ids=invoice.ids,
    ).create({'journal_id': journal.id, 'payment_method_line_id': method.id, 'amount': amount})
    wizard._create_payments()


def run_case(chart, valuation):
    # Select the chart explicitly, avoiding the native deferred country guess.
    company = env['res.company'].with_context(chart_template_load=True).create({
        'name': 'CNF synthetic %s %s' % (chart, valuation),
        'country_id': env.ref('base.cn').id,
        'currency_id': env.ref('base.CNY').id,
    })
    case_env = env(context=dict(env.context, allowed_company_ids=company.ids))
    company = company.with_env(case_env)
    case_env['account.chart.template'].try_loading(chart, company, install_demo=False)
    assert company.chart_template == chart
    company.write({'inventory_valuation': valuation, 'cost_method': 'average'})
    case_env['stock.warehouse'].create({
        'name': 'CNF synthetic warehouse', 'code': 'CNF', 'company_id': company.id,
    })
    vendor = case_env['res.partner'].create({'name': 'CNF synthetic vendor'})
    customer = case_env['res.partner'].create({'name': 'CNF synthetic customer'})
    product = case_env['product.product'].create({
        'name': 'CNF synthetic goods', 'is_storable': True, 'type': 'consu',
        'standard_price': 100, 'list_price': 200, 'invoice_policy': 'delivery',
        'purchase_method': 'receive', 'taxes_id': [Command.clear()],
        'supplier_taxes_id': [Command.clear()], 'company_id': company.id,
    })
    rows = []
    with freeze_time('2026-08-30 10:00:00'):
        purchase = case_env['purchase.order'].create({
            'partner_id': vendor.id, 'order_line': [Command.create({
                'product_id': product.id, 'product_qty': 10, 'price_unit': 100,
                'date_planned': fields.Datetime.now(), 'tax_ids': [Command.clear()],
            })],
        })
        purchase.button_confirm()
        complete(purchase.picking_ids)
        assert product.qty_available == 10
        rows.append(snapshot(case_env, product, 'received_no_bill'))
    with freeze_time('2026-08-31 18:00:00'):
        # Capture native missing-configuration/no-adjustment outcomes as audit gaps.
        try:
            with case_env.cr.savepoint():
                action = company.action_close_stock_valuation(at_date='2026-08-31', auto_post=True)
                closing = {'move_created': bool(action.get('res_id'))}
        except UserError as error:
            closing = {'native_user_error': str(error)}
        rows.append(snapshot(case_env, product, 'month_end_before_bill'))
    with freeze_time('2026-09-02 10:00:00'):
        purchase.action_create_invoice()
        bill = purchase.invoice_ids
        bill.ensure_one()
        bill.invoice_date = fields.Date.today()
        bill.invoice_line_ids.price_unit = 110
        bill.action_post()
        assert bill.amount_total == 1100
        rows.append(snapshot(case_env, product, 'bill_price_110'))
    with freeze_time('2026-09-03 10:00:00'):
        sale = case_env['sale.order'].create({
            'partner_id': customer.id, 'order_line': [Command.create({
                'product_id': product.id, 'product_uom_qty': 4, 'price_unit': 200,
                'tax_ids': [Command.clear()],
            })],
        })
        sale.action_confirm()
        complete(sale.picking_ids)
        rows.append(snapshot(case_env, product, 'delivered_before_invoice'))
        invoice = sale._create_invoices()
        invoice.invoice_date = fields.Date.today()
        invoice.action_post()
        assert invoice.amount_total == 800
        pay(case_env, invoice, 300)
        pay(case_env, bill, 500)
        assert invoice.amount_residual == 500
        assert bill.amount_residual == 600
        assert product.qty_available == 6
        rows.append(snapshot(case_env, product, 'invoiced_and_part_paid'))
    with freeze_time('2026-09-30 18:00:00'):
        company.action_close_stock_valuation(at_date='2026-09-30', auto_post=True)
        rows.append(snapshot(case_env, product, 'second_month_end'))
        stock_lines = case_env['account.move.line'].search([
            ('company_id', '=', company.id), ('parent_state', '=', 'posted'),
            ('account_id', '=', company.account_stock_valuation_id.id),
        ])
        assert company.currency_id.is_zero(sum(stock_lines.mapped('balance')) - product.total_value)
    assert company.chart_template == chart, (chart, company.chart_template)
    return {
        'chart': chart, 'actual_chart': company.chart_template, 'valuation': valuation, 'cost_method': product.cost_method,
        'currency': company.currency_id.name, 'closing': closing,
        'customer_residual': invoice.amount_residual, 'vendor_residual': bill.amount_residual,
        'snapshots': rows,
    }


try:
    results = []
    for chart in ('cn', 'cn_large_bis'):
        for valuation in ('periodic', 'real_time'):
            results.append(run_case(chart, valuation))
    print('CNF_P0_RESULT=' + json.dumps(results, ensure_ascii=False, default=str))
finally:
    env.cr.rollback()
