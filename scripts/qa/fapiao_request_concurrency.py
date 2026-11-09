"""Independent-transaction checks. Run via Odoo shell in a disposable qing_cnf_* DB only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from psycopg2.errors import SerializationFailure
from odoo import api, Command, SUPERUSER_ID
from odoo.exceptions import UserError
from odoo.modules.registry import Registry
from odoo.tools.binary import BinaryBytes

assert env.cr.dbname.startswith('qing_cnf_'), 'Disposable database required'
registry = Registry(env.cr.dbname)
company_id = env.company.id
prefix = 'REQUEST-QA-' + uuid4().hex[:8]
if not env.company.chart_template:
    env['account.chart.template'].try_loading('generic_coa', env.company, install_demo=False)
partner = env['res.partner'].create({'name': prefix})
account = env['account.account'].search([('company_ids', 'in', [company_id]), ('account_type', '=', 'income')], limit=1)
assert account


def bill():
    record = env['account.move'].create({'move_type': 'out_invoice', 'partner_id': partner.id,
        'invoice_date': '2026-09-19', 'invoice_line_ids': [Command.create({'name': prefix,
        'quantity': 10, 'price_unit': 10, 'account_id': account.id, 'tax_ids': [Command.clear()]})]})
    record.action_post()
    return record


def request(move, percent=80):
    record = env['qing.fapiao.request'].browse(move.action_create_fapiao_request()['res_id'])
    record.seller_tax_id = 'SYNTHETIC-SELLER'
    record.line_ids.write({'quantity': percent / 10, 'amount_untaxed': percent, 'tax_code': 'SYNTHETIC'})
    return record


def race(actions, expect):
    barrier = Barrier(2)
    def run(action):
        model, record_id, method = action
        for attempt in range(4):
            try:
                with registry.cursor() as cr:
                    local = api.Environment(cr, SUPERUSER_ID, {'allowed_company_ids': [company_id]})
                    record = local[model].browse(record_id)
                    record.read(['state'])
                    if not attempt:
                        barrier.wait(timeout=15)
                    getattr(record, method)()
                    result = record.fapiao_id.id if method == 'action_record_invoice' else 'ok'
                    cr.commit()
                    return result
            except SerializationFailure:
                continue
            except UserError:
                return 'rejected'
        raise AssertionError('Transaction retry exhausted')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, actions))
    if expect == 'one':
        assert sorted(results) == ['ok', 'rejected'], results
    else:
        assert isinstance(results[0], int) and results[0] == results[1], results
    print('REQUEST_CONCURRENCY_OK', [a[2] for a in actions], results)


first_bill = bill()
first, second = request(first_bill), request(first_bill)
second_bill = bill()
third = request(second_bill)
ticket = env['qing.fapiao'].create({'number': prefix, 'direction': 'out', 'partner_id': partner.id,
    'buyer_name': prefix, 'seller_name': prefix, 'amount_untaxed': 100, 'amount_tax': 0,
    'source_file': BinaryBytes(b'Synthetic only'), 'source_filename': 'synthetic.txt'})
ticket.action_register()
allocation = env['qing.fapiao.allocation'].create({'fapiao_id': ticket.id, 'move_id': second_bill.id,
    'amount_untaxed': 80, 'amount_tax': 0})
last = request(bill(), 100)
last.action_submit()
last.action_approve()
last.write({'result_number': prefix + '-DONE', 'result_date': '2026-09-19',
    'result_file': BinaryBytes(b'Synthetic only'), 'result_filename': 'synthetic.txt',
    'result_amount_untaxed': 100, 'result_amount_tax': 0, 'result_checked': True})
env.cr.commit()
# Leave synthetic audit records in this disposable DB for UI inspection; delete the DB after QA.
race([('qing.fapiao.request', r.id, 'action_submit') for r in (first, second)], 'one')
race([('qing.fapiao.request', third.id, 'action_submit'),
      ('qing.fapiao.allocation', allocation.id, 'action_confirm')], 'one')
race([('qing.fapiao.request', last.id, 'action_record_invoice')] * 2, 'same')
env.cr.rollback()
env.invalidate_all()
assert env['qing.fapiao'].search_count([('number', '=', prefix + '-DONE')]) == 1
print('REQUEST_CONCURRENCY_ALL_OK')
