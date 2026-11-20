from odoo import SUPERUSER_ID, api
from odoo.addons.qing_channel.errors import ChannelReviewRequired


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Claim = env['qing.channel.stock.claim']
    last_id = 0
    while True:
        batch = Claim.search([
            ('id', '>', last_id), ('store_id.platform', '=', 'shopify'), ('location_uncertain', '=', False),
        ], order='id', limit=250)
        if not batch:
            return
        uncertain = Claim.browse()
        for claim in batch:
            line = claim.line_id
            order = line.order_id
            if not (order.snapshot or {}).get('refunds'):
                continue
            try:
                restocks = claim.store_id._shopify_line_restocks(order, line)
            except (ChannelReviewRequired, KeyError, TypeError, ValueError, OverflowError):
                uncertain |= claim
                continue
            if any(quantity and not location for location, quantity in restocks.values()):
                uncertain |= claim
        uncertain.location_uncertain = True
        last_id = batch[-1].id
        env.invalidate_all()
