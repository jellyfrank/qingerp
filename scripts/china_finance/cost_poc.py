"""CNF-06 native accounting probe. Odoo shell, disposable DB only, rollback.

Install l10n_cn, sale_stock, purchase_stock, stock_landed_costs first.
No tax assumptions; no client data. Each scenario uses a new synthetic company.
"""
import json
from freezegun import freeze_time
from odoo import Command, fields
from odoo.exceptions import UserError

if not env.cr.dbname.startswith('qing_cnf_p0_'):
    raise RuntimeError('Disposable qing_cnf_p0_* database required')


def complete(picking):
    picking.action_assign()
    picking.move_ids.quantity = picking.move_ids.product_uom_qty
    picking.move_ids.picked = True
    picking.button_validate()
    assert picking.state == 'done'


def run_case(valuation, method, freight_amount):
    company = env['res.company'].with_context(chart_template_load=True).create({
        'name': 'CNF06 synthetic %s %s %s' % (valuation, method, freight_amount),
        'country_id': env.ref('base.cn').id, 'currency_id': env.ref('base.CNY').id,
    })
    e = env(context=dict(env.context, allowed_company_ids=company.ids))
    company = company.with_env(e)
    e['account.chart.template'].try_loading('cn', company, install_demo=False)
    company.write({'inventory_valuation': valuation, 'cost_method': method})
    e['stock.warehouse'].create({'name': 'CNF06 synthetic', 'code': 'C06', 'company_id': company.id})
    vendor = e['res.partner'].create({'name': 'CNF06 synthetic vendor'})
    product = e['product.product'].create({
        'name': 'CNF06 synthetic goods', 'type': 'consu', 'is_storable': True,
        'standard_price': 100, 'purchase_method': 'receive', 'company_id': company.id,
        'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()],
    })
    rows = []

    def snap(stage, quantity):
        moves = e['account.move'].search([('company_id', '=', company.id), ('state', '=', 'posted')])
        assert product.qty_available == quantity, (stage, product.qty_available)
        for move in moves:
            assert company.currency_id.is_zero(sum(move.line_ids.mapped('balance')))
        stock_balance = sum(moves.line_ids.filtered(
            lambda line: line.account_id == company.account_stock_valuation_id).mapped('balance'))
        rows.append({'stage': stage, 'quantity': product.qty_available, 'value': product.total_value,
                     'unit_cost': product.standard_price, 'stock_balance': stock_balance,
                     'difference': company.currency_id.round(stock_balance - product.total_value),
                     'purchase_moves': [{'quantity': sm.quantity, 'value': sm.value,
                                         'is_in': sm.is_in, 'is_out': sm.is_out}
                                        for sm in po.order_line.move_ids if sm.state == 'done'],
                     'entries': [{'date': str(m.date), 'type': m.move_type,
                                  'lines': [{'code': l.account_id.code, 'balance': l.balance}
                                            for l in m.line_ids if l.account_id]} for m in moves]})

    with freeze_time('2026-08-30 10:00:00'):
        po = e['purchase.order'].create({'partner_id': vendor.id, 'order_line': [Command.create({
            'product_id': product.id, 'product_qty': 10, 'price_unit': 100,
            'date_planned': fields.Datetime.now(), 'tax_ids': [Command.clear()],
        })]})
        po.button_confirm()
        receipt = po.picking_ids
        complete(receipt)
        snap('receipt', 10)
    with freeze_time('2026-08-31 18:00:00'):
        company.action_close_stock_valuation(at_date='2026-08-31', auto_post=True)
        snap('august_close', 10)
    with freeze_time('2026-09-02 10:00:00'):
        po.action_create_invoice()
        first = po.invoice_ids
        first.invoice_date = fields.Date.today()
        first.invoice_line_ids.write({'quantity': 6, 'price_unit': 110})
        first.action_post()
        assert first.amount_total == 660
        snap('partial_bill_6_at_110', 10)
        po.action_create_invoice()
        second = po.invoice_ids - first
        second.invoice_date = fields.Date.today()
        second.invoice_line_ids.price_unit = 110
        second.action_post()
        assert second.amount_total == 440
        snap('remaining_bill_4_at_110', 10)
        freight = e['product.product'].create({'name': 'CNF06 synthetic freight', 'type': 'service',
            'landed_cost_ok': True, 'supplier_taxes_id': [Command.clear()]})
        freight_bill = e['account.move'].create({'move_type': 'in_invoice', 'partner_id': vendor.id,
            'invoice_date': fields.Date.today(), 'invoice_line_ids': [Command.create({
                'product_id': freight.id, 'quantity': 1, 'price_unit': freight_amount, 'tax_ids': [Command.clear()],
            })]})
        freight_bill.action_post()
        cost = e['stock.landed.cost'].create({'picking_ids': [Command.set(receipt.ids)],
            'vendor_bill_id': freight_bill.id, 'account_journal_id': company.account_stock_journal_id.id,
            'cost_lines': [Command.create({'product_id': freight.id, 'name': 'Freight',
                'price_unit': freight_amount, 'split_method': 'by_quantity',
                'account_id': freight_bill.invoice_line_ids.account_id.id})]})
        cost.compute_landed_cost()
        assert sum(cost.valuation_adjustment_lines.mapped('additional_landed_cost')) == freight_amount
        cost.button_validate()
        assert cost.state == 'done'
        try:
            with e.cr.savepoint():
                cost.button_validate()
        except UserError:
            pass
        else:
            raise AssertionError('Duplicate landed cost posting was accepted')
        snap('freight_allocated', 10)
        assert company.currency_id.is_zero(product.total_value - 1100 - freight_amount)
    with freeze_time('2026-09-30 18:00:00'):
        company.action_close_stock_valuation(at_date='2026-09-30', auto_post=True)
        snap('september_close', 10)
        assert company.currency_id.is_zero(rows[-1]['difference'])
    with freeze_time('2026-10-02 10:00:00'):
        returned = receipt._create_return()
        returned.move_ids.product_uom_qty = 2
        complete(returned)
        snap('return_2_before_credit', 8)
        assert po.order_line.qty_received == 8
        credit = first._reverse_moves(default_values_list=[{'date': fields.Date.today(), 'invoice_date': fields.Date.today()}])
        credit.invoice_line_ids.quantity = 2
        credit.action_post()
        assert credit.amount_total == 220
        assert po.order_line.qty_invoiced == 8
        snap('vendor_credit_220', 8)
        if hasattr(receipt.move_ids, '_qing_is_physical_return_refund'):
            assert company.currency_id.is_zero(rows[-1]['value'] - rows[-2]['value'])
            assert company.currency_id.is_zero(product.total_value - (1100 + freight_amount) * 0.8)
    with freeze_time('2026-10-31 18:00:00'):
        # A corrected no-freight perpetual case can already be balanced.
        if not company.currency_id.is_zero(rows[-1]['difference']):
            company.action_close_stock_valuation(at_date='2026-10-31', auto_post=True)
        snap('october_close', 8)
        assert company.currency_id.is_zero(rows[-1]['difference'])
    assert company.chart_template == 'cn'
    return {'valuation': valuation, 'method': method, 'freight': freight_amount,
            'credit_valuation_change': company.currency_id.round(rows[-2]['value'] - rows[-3]['value']),
            'snapshots': rows}


try:
    results = [run_case(v, m, f) for v in ('periodic', 'real_time') for m in ('average', 'fifo') for f in (0, 100)]
    print('CNF06_RESULT=' + json.dumps(results, ensure_ascii=False))
finally:
    env.cr.rollback()
