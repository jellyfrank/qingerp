from odoo import api, fields, models
from odoo.addons.qing_base.tools.channel_queue import snapshot_digest


class ChannelOrder(models.Model):
    _name = 'qing.channel.order'
    _description = 'Channel Order'
    _check_company_auto = True

    @api.model
    def _upsert_snapshot(self, store, external_id, name, updated_at, snapshot, historical=False):
        domain = [('store_id', '=', store.id), ('external_id', '=', external_id)]
        order = self.search(domain, limit=1)
        if order and order.external_updated_at and updated_at < order.external_updated_at:
            return order
        digest = snapshot_digest({key: value for key, value in snapshot.items() if key != 'updatedAt'})
        vals = {'external_name': name, 'external_updated_at': updated_at}
        if digest != order.snapshot_hash:
            vals.update(snapshot=snapshot, snapshot_hash=digest, snapshot_version=order.snapshot_version + 1)
        if order:
            order.write(vals)
            return order
        return self.create(dict(vals, store_id=store.id, external_id=external_id, historical=historical))

    company_id = fields.Many2one(related='store_id.company_id', store=True, index=True, precompute=True)
    external_id = fields.Char(required=True)
    external_name = fields.Char()
    external_updated_at = fields.Datetime()
    historical = fields.Boolean()
    line_ids = fields.One2many('qing.channel.order.line', 'order_id')
    name = fields.Char(related='external_name', store=True)
    sale_id = fields.Many2one('sale.order', check_company=True, copy=False, ondelete='restrict', index=True)
    snapshot = fields.Json(copy=False, groups='qing_channel.group_channel_manager')
    snapshot_hash = fields.Char(copy=False)
    snapshot_version = fields.Integer(default=0, copy=False)
    stock_unverified = fields.Boolean(copy=False)
    store_id = fields.Many2one('qing.channel.store', required=True, ondelete='restrict', index=True)

    _external_unique = models.UniqueIndex('(store_id, external_id)')
    _sale_unique = models.UniqueIndex('(sale_id)')
