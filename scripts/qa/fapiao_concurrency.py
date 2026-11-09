"""Run with Odoo shell on a disposable qing_cnf_* database only.
Creates synthetic records; never use on a tenant or production database.
"""
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
prefix = 'CONCURRENCY-' + uuid4().hex[:10]
company_id = env.company.id
partner = env['res.partner'].create({'name': prefix})
account = env['account.account'].search([('company_ids', 'in', [company_id]), ('account_type', '=', 'expense')], limit=1)
assert account
bill = env['account.move'].create({'move_type': 'in_invoice', 'partner_id': partner.id,
    'invoice_date': '2026-09-17', 'invoice_line_ids': [Command.create({'name': prefix,
    'quantity': 1, 'price_unit': 100, 'account_id': account.id, 'tax_ids': [Command.clear()]})]})
bill.action_post()
base = {'direction': 'in', 'partner_id': partner.id, 'buyer_name': prefix, 'seller_name': prefix,
        'amount_untaxed': 100, 'amount_tax': 0, 'source_filename': 'synthetic.txt',
        'source_file': BinaryBytes(b'Synthetic concurrency test, not a tax invoice.')}
tickets = env['qing.fapiao'].create([dict(base, number=prefix+'-A'), dict(base, number=prefix+'-B')])
tickets.action_register()
lines = env['qing.fapiao.allocation'].create([{'fapiao_id': t.id, 'move_id': bill.id,
    'amount_untaxed': 80, 'amount_tax': 0} for t in tickets])
reds = env['qing.fapiao'].create([dict(base, number=prefix+'-R'+str(i), color='red',
    original_id=tickets[0].id, original_number=tickets[0].number, amount_untaxed=60) for i in range(2)])
ids, red_ids, ticket_ids = lines.ids, reds.ids, tickets.ids
env.cr.commit()


def race(model, ids, method):
    barrier = Barrier(2)
    def run(record_id):
        for attempt in range(3):
            try:
                with registry.cursor() as cr:
                    local = api.Environment(cr, SUPERUSER_ID, {'allowed_company_ids': [company_id]})
                    record = local[model].browse(record_id)
                    record.read(['state'])  # Establish both repeatable-read snapshots before the race.
                    if attempt == 0:
                        barrier.wait(timeout=15)
                    getattr(record, method)()
                    cr.commit()
                    return 'confirmed'
            except SerializationFailure:
                continue
            except UserError:
                return 'rejected'
        return 'retry_exhausted'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ids))
    assert sorted(results) == ['confirmed', 'rejected'], results
    print('CONCURRENCY_OK', model, results)


try:
    race('qing.fapiao.allocation', ids, 'action_confirm')
    race('qing.fapiao', red_ids, 'action_register')
finally:
    env.cr.rollback()
    env.invalidate_all()
    active = env['qing.fapiao'].browse(red_ids + ticket_ids).filtered(lambda r: r.state == 'registered')
    # Cancel red registrations first; retain the synthetic audit trail in this disposable DB.
    for ticket in active.sorted(key=lambda r: r.color != 'red'):
        ticket.cancel_reason = 'Synthetic concurrency QA completed'
        ticket.action_cancel_registration()
    env.cr.commit()
