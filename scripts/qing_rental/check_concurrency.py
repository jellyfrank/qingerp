"""Run through ``odoo shell`` against a qing_rental_* synthetic test database.

Creates an independent product pool and two simultaneous six-unit reservations
against ten available units. Each thread owns its cursor and environment; the
loser retries PostgreSQL serialization failures as a fresh request would.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

from psycopg2.errors import SerializationFailure

from odoo import Command, SUPERUSER_ID, api, fields
from odoo.exceptions import UserError
from odoo.tools import config


if not env.cr.dbname.startswith('qing_rental_') or config['db_host'] != 'qing-rental-db':
    raise RuntimeError('This script requires a qing_rental_* database on the dedicated qing-rental-db server.')

marker = uuid4().hex[:8]
company = env.company
unit = env.ref('uom.product_uom_unit')
warehouse = env['stock.warehouse'].create({
    'name': 'Concurrency ' + marker, 'code': marker[:5],
    'company_id': company.id,
})
warehouse.action_qing_setup_rental()
service = env['product.product'].create({
    'name': 'Concurrency rental fee ' + marker, 'type': 'service',
    'invoice_policy': 'order', 'uom_id': unit.id,
    'taxes_id': [Command.clear()],
})
product = env['product.product'].create({
    'name': 'Concurrency equipment ' + marker, 'type': 'consu',
    'is_storable': True, 'uom_id': unit.id, 'qing_rental_ok': True,
    'qing_rental_service_id': service.id, 'qing_rental_rate': 10,
    'taxes_id': [Command.clear()],
})
env['stock.quant']._update_available_quantity(product, warehouse.qing_rental_stock_location_id, 10)
customer = env['res.partner'].create({'name': 'Concurrency customer ' + marker})
start = fields.Datetime.now().replace(microsecond=0) + timedelta(days=365)
orders = env['sale.order'].create([{
    'partner_id': customer.id, 'warehouse_id': warehouse.id,
    'is_qing_rental': True, 'qing_rental_start': start,
    'qing_rental_end': start + timedelta(days=1),
    'qing_rental_line_ids': [Command.create({'product_id': product.id, 'quantity': 6})],
} for unused in range(2)])
order_ids = orders.ids
company_id = company.id
registry = env.registry
env.cr.commit()
barrier = Barrier(2)


def confirm(order_id):
    retries = 0
    initial_pid = None
    for attempt in range(3):
        with registry.cursor() as cr:
            cr.execute("SELECT pg_backend_pid(), current_setting('transaction_isolation')")
            pid, isolation = cr.fetchone()
            assert isolation == 'repeatable read', isolation
            if initial_pid is None:
                initial_pid = pid
            current = api.Environment(cr, SUPERUSER_ID, {'allowed_company_ids': [company_id], 'lang': 'en_US'})
            order = current['sale.order'].browse(order_id)
            # Both first attempts read the same committed snapshot before either writes.
            order.read(['state'])
            if attempt == 0:
                barrier.wait(timeout=30)
            try:
                order.action_confirm()
                cr.commit()
                return {'order_id': order_id, 'result': 'confirmed', 'serialization_retries': retries, 'initial_backend_pid': initial_pid}
            except SerializationFailure:
                cr.rollback()
                retries += 1
            except UserError as error:
                cr.rollback()
                if 'Not enough rental capacity' not in str(error):
                    raise
                return {'order_id': order_id, 'result': 'capacity_refused', 'serialization_retries': retries, 'initial_backend_pid': initial_pid, 'reason': str(error)}
    raise AssertionError('Serialization retries did not converge.')


with ThreadPoolExecutor(max_workers=2) as executor:
    results = list(executor.map(confirm, order_ids))

with registry.cursor() as verification_cursor:
    verification = api.Environment(verification_cursor, SUPERUSER_ID, {'allowed_company_ids': [company_id]})
    confirmed = verification['sale.order'].browse(order_ids).filtered(lambda order: order.state == 'sale')
    assert len(confirmed) == 1, results
    assert sum(confirmed.qing_rental_line_ids.mapped('quantity')) == 6, results
assert len({item['initial_backend_pid'] for item in results}) == 2, results
assert sorted(item['result'] for item in results) == ['capacity_refused', 'confirmed'], results
print('RENTAL_CONCURRENCY_RESULT ' + json.dumps(results, ensure_ascii=False))
