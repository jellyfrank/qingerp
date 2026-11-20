from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Refund = env['qing.channel.refund']
    last_id = 0
    while True:
        batch = Refund.search(
            [('id', '>', last_id), ('accounted', '=', True), ('store_id.platform', '=', 'shopify')],
            order='id', limit=250,
        )
        if not batch:
            return
        for refund in batch:
            store = refund.store_id
            snapshot = refund.snapshot
            if store._refund_financial_digest(snapshot, legacy=True) == refund.accounted_digest:
                refund.accounted_digest = store._refund_financial_digest(snapshot)
        last_id = batch[-1].id
        env.invalidate_all()
