"""Run through `odoo shell` against the disposable qing_purchase_test database only."""
from odoo import api, Command
from odoo.exceptions import LockError, UserError
from psycopg2.errors import SerializationFailure


def run(env):
    assert env.cr.dbname == 'qing_purchase_test', 'Disposable test database only'
    customer, vendor = env['res.partner'].create([
        {'name': 'Concurrency customer'}, {'name': 'Concurrency vendor'},
    ])
    product = env['product.product'].create({'name': 'Concurrency goods', 'type': 'consu'})
    order = env['sale.order'].create({
        'partner_id': customer.id,
        'order_line': [Command.create({'product_id': product.id, 'product_uom_qty': 100})],
    })
    order.action_confirm()
    wizards = env['qing.sale.purchase.wizard']
    for _ in range(2):
        wizard = wizards.browse(order.action_qing_create_purchase()['res_id'])
        wizard.user_id = env.ref('base.user_admin')
        wizard.line_ids.write({'supplier_id': vendor.id, 'quantity': 70,
                               'price_unit': 5})
        wizards |= wizard
    env.cr.commit()
    with env.registry.cursor() as cr1, env.registry.cursor() as cr2, env.registry.cursor() as stale_cr:
        one, two, stale = [api.Environment(cr, env.uid, {}) for cr in (cr1, cr2, stale_cr)]
        stale['sale.order.line'].browse(order.order_line.id).read(['product_uom_qty'])
        one['qing.sale.purchase.wizard'].browse(wizards[0].id).action_create_purchase()
        try:
            two['qing.sale.purchase.wizard'].browse(wizards[1].id).action_create_purchase()
            raise AssertionError('Concurrent lock was not enforced')
        except LockError:
            cr2.rollback()
        cr1.commit()
        try:
            stale['qing.sale.purchase.wizard'].browse(wizards[1].id).action_create_purchase()
            raise AssertionError('Stale snapshot was not rejected')
        except SerializationFailure:
            stale_cr.rollback()
    with env.registry.cursor() as retry_cr:
        retry = api.Environment(retry_cr, env.uid, {})
        try:
            retry['qing.sale.purchase.wizard'].browse(wizards[1].id).action_create_purchase()
            raise AssertionError('Over-allocation was accepted after retry')
        except UserError:
            retry_cr.rollback()
    env.cr.rollback()
    env.invalidate_all()
    assert order.order_line.qing_purchase_qty == 70
    assert len(order.order_line.purchase_line_ids) == 1
    print('QING_CONCURRENCY_OK lock, stale snapshot, fresh retry, single allocation')


run(env)
