"""Run via odoo shell only in a disposable bgwy_2444_2448_* database."""
from odoo import api, Command
from odoo.exceptions import LockError, UserError
from psycopg2.errors import SerializationFailure


def run(env):
    assert env.cr.dbname.startswith('bgwy_2444_2448_'), 'Disposable test database only'
    customer, vendor = env['res.partner'].create([
        {'name': '2447 concurrency customer'}, {'name': '2447 concurrency vendor'},
    ])
    product = env['product.product'].create({'name': '2447 concurrency service',
        'type': 'service', 'purchase_ok': True})
    repair = env['repair.order'].create({'partner_id': customer.id, 'qing_pricing_mode': 'direct',
        'repair_service_line_ids': [Command.create({'product_id': product.id, 'quantity': 100})]})
    wizards = env['qing.repair.purchase.wizard']
    for _ in range(2):
        wizard = wizards.browse(repair.action_qing_create_purchase()['res_id'])
        wizard.supplier_id = vendor
        wizard.line_ids.write({'quantity': 70, 'price_unit': 5})
        wizards |= wizard
    env.cr.commit()
    with env.registry.cursor() as cr1, env.registry.cursor() as cr2, env.registry.cursor() as stale_cr:
        one, two, stale = [api.Environment(cr, env.uid, {}) for cr in (cr1, cr2, stale_cr)]
        stale['repair.order'].browse(repair.id).read(['state'])
        one['qing.repair.purchase.wizard'].browse(wizards[0].id).action_create_purchase()
        try:
            two['qing.repair.purchase.wizard'].browse(wizards[1].id).action_create_purchase()
            raise AssertionError('Concurrent repair allocation was accepted')
        except LockError:
            cr2.rollback()
        cr1.commit()
        try:
            stale['qing.repair.purchase.wizard'].browse(wizards[1].id).action_create_purchase()
            raise AssertionError('Stale snapshot was accepted')
        except SerializationFailure:
            stale_cr.rollback()
    with env.registry.cursor() as retry_cr:
        retry = api.Environment(retry_cr, env.uid, {})
        try:
            retry['qing.repair.purchase.wizard'].browse(wizards[1].id).action_create_purchase()
            raise AssertionError('Excess quantity was accepted after retry')
        except UserError:
            retry_cr.rollback()
    env.cr.rollback()
    env.invalidate_all()
    assert len(repair.qing_purchase_order_ids) == 1
    assert repair.qing_purchase_order_ids.order_line.product_qty == 70
    print('REPAIR_CONCURRENCY_OK: lock, stale snapshot, fresh retry, one allocation')


run(env)
