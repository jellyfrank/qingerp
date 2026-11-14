from odoo import api, fields, models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    @api.model
    def _qing_format_tracking_events(self, events):
        """Render carrier tracking events as one line per event."""
        lines = []
        for event in events:
            line = ' '.join(part for part in (event.get('time'), event.get('description')) if part)
            if event.get('location'):
                line = f"{line} ({event['location']})"
            if line:
                lines.append(line)
        return '\n'.join(lines)

    @api.depends('carrier_id', 'carrier_id.delivery_type')
    def _compute_qing_tracking_supported(self):
        for picking in self:
            picking.qing_tracking_supported = bool(picking.carrier_id) and picking.carrier_id._qing_tracking_supported()

    qing_carrier_order_ref = fields.Char(
        string='Carrier Order Reference', copy=False, readonly=True,
        help='Customer order number sent to the carrier when the shipment was created.',
    )
    qing_goods_name = fields.Char(
        string='Goods Description', copy=False,
        help='Overrides the default goods description of the delivery method.',
    )
    qing_tracking_date = fields.Datetime(string='Tracking Updated On', copy=False, readonly=True)
    qing_tracking_detail = fields.Text(string='Tracking Details', copy=False, readonly=True)
    qing_tracking_supported = fields.Boolean(compute='_compute_qing_tracking_supported')

    def action_qing_update_tracking(self):
        for picking in self.filtered(lambda p: p.carrier_id and p.carrier_tracking_ref):
            events = picking.carrier_id._qing_get_tracking_events(picking)
            picking.write({
                'qing_tracking_date': fields.Datetime.now(),
                'qing_tracking_detail': self._qing_format_tracking_events(events)
                or self.env._('No tracking events yet.'),
            })
        return True
