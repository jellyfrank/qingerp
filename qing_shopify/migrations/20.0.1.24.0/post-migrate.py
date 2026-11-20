from odoo import Command, SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Refund = env['qing.channel.refund']
    Action = env['qing.channel.action']
    domain = [
        ('store_id.platform', '=', 'shopify'), ('accounted', '=', False),
        '|', ('order_id.historical', '=', False), ('order_id.shopify_finance_requested', '=', True),
    ]
    last_id = 0
    while True:
        batch = Refund.search(domain + [('id', '>', last_id)], order='id', limit=250)
        if not batch:
            return
        for refund in batch:
            store = refund.store_id
            verification = store._ensure_shopify_refund_verification(refund)
            posting = Action.search([
                ('store_id', '=', store.id), ('kind', '=', 'refund'),
                ('key', '=', f'refund:{refund.id}:{refund.order_id.snapshot_version}'),
                ('state', 'in', ('pending', 'waiting', 'review')),
            ], limit=1)
            if posting:
                posting.dependency_ids = [Command.link(verification.id)]
        last_id = batch[-1].id
        env.invalidate_all()
