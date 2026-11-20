from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Order = env['qing.channel.order']
    Delivery = env['qing.channel.delivery']
    domain = [('store_id.platform', '=', 'shopify'), ('stock_unverified', '=', True)]
    last_id = 0
    while True:
        batch = Order.search(domain + [('id', '>', last_id)], order='id', limit=250)
        if not batch:
            return
        for order in batch:
            snapshot = order.snapshot or {}
            if snapshot.get('transactions') or snapshot.get('refunds'):
                Delivery._receive(order.store_id, f'upgrade:stock-review:{order.id}:{order.snapshot_version}',
                                  order.external_id, 'orders/reconcile')
        last_id = batch[-1].id
        env.invalidate_all()
