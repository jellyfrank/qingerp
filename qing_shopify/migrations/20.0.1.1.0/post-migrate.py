from odoo import SUPERUSER_ID, api
from odoo.addons.qing_base.tools.channel_queue import snapshot_digest


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Order = env['qing.channel.order']
    cursor = 0
    while True:
        batch = Order.search([('id', '>', cursor), ('store_id.platform', '=', 'shopify')],
                             order='id', limit=250)
        if not batch:
            break
        for order in batch:
            snapshot = order.snapshot
            if snapshot and 'customer_data_hash' not in snapshot:
                public, _customer = Order._split_shopify_customer_data(snapshot)
                digest = snapshot_digest({key: value for key, value in public.items() if key != 'updatedAt'})
                order.write({'snapshot': public, 'snapshot_hash': digest})
            sale = order.sale_id
            commercial = sale.shopify_commercial_data
            if commercial and 'shipping_address_hash' not in commercial:
                public, _customer = Order._split_shopify_customer_data(commercial)
                sale.shopify_commercial_data = order.store_id._commercial_data(public)
        cursor = batch[-1].id
        env.invalidate_all()
