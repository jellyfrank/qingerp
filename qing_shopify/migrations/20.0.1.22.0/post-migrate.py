from odoo import SUPERUSER_ID, api
from odoo.addons.qing_shopify.models.store_order import PAYMENT_CHECK_KINDS, PAYMENT_PENDING_STATUSES


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Transaction = env['qing.channel.transaction']
    domain = [
        ('store_id.platform', '=', 'shopify'),
        ('kind', 'in', sorted(PAYMENT_CHECK_KINDS)),
        ('status', 'in', sorted(PAYMENT_PENDING_STATUSES)),
        '|', ('order_id.historical', '=', False), ('order_id.shopify_finance_requested', '=', True),
    ]
    last_id = 0
    while True:
        batch = Transaction.search(domain + [('id', '>', last_id)], order='id', limit=250)
        if not batch:
            return
        for order in batch.order_id:
            order.store_id._enqueue_shopify_payment_verification(order)
        last_id = batch[-1].id
        env.invalidate_all()
