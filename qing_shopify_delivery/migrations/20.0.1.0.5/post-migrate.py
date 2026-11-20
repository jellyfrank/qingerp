from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Action = env['qing.channel.action']
    Shipment = env['qing.shopify.shipment']
    scope = [('store_id.platform', '=', 'shopify'), ('kind', 'in', ('fulfillment', 'tracking')),
             ('snapshot_version', '>', 0), ('state', 'in', ('pending', 'waiting', 'review', 'obsolete'))]
    last_id = 0
    while True:
        batch = Action.search(scope + [('id', '>', last_id)], order='id', limit=250)
        if not batch:
            return
        for action in batch:
            if action.state != 'obsolete':
                action.snapshot_version = 0
                continue
            if action.request_ids or action.snapshot_version == action.order_id.snapshot_version:
                continue
            data = action.data or {}
            shipment = Shipment.browse(data.get('shipment')).exists()
            if not shipment or shipment.order_id != action.order_id or shipment.version != data.get('version'):
                continue
            store = action.store_id
            if not store._shopify_shipment_eligible(shipment):
                continue
            if action.kind == 'tracking' and not shipment.remote_id:
                continue
            if action.kind == 'fulfillment' and (shipment.remote_id or store._shopify_shipment_before_cutover(shipment.picking_id)):
                continue
            action.write({'snapshot_version': 0, 'state': 'pending', 'tries': 0, 'retry_at': False})
        last_id = batch[-1].id
        env.invalidate_all()
