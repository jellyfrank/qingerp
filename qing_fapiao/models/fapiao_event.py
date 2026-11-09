from odoo import _, api, fields, models
from odoo.exceptions import AccessError

EVENT_KINDS = [
    ('stopped', 'Automatic Processing Cancelled'), ('resumed', 'Result Queries Resumed'),
    ('queued', 'Queued'), ('attempt', 'Attempt Started'), ('result', 'Provider Result'),
    ('notification', 'Verified Notification'), ('ignored', 'Outdated Result Ignored'),
    ('registration', 'Business Registration'), ('cancelled', 'Cancelled Before Sending'), ('error', 'Processing Error'),
]


class FapiaoEvent(models.Model):
    _name = 'qing.fapiao.event'
    _description = 'Invoice Interface Audit Event'
    _order = 'id desc'
    _check_company_auto = True

    _notification_unique = models.Constraint('UNIQUE(order_id, notification_key)', 'Duplicate invoice notification.')

    @api.model
    def _append(self, order, kind, payload=None, notification_key=None):
        values = {
            'order_id': order.id, 'company_id': order.company_id.id, 'kind': kind,
            'payload': payload or {}, 'notification_key': notification_key,
            'processed': kind != 'notification',
        }
        clean = self.with_context({key: value for key, value in self.env.context.items() if not key.startswith('default_')})
        return super(FapiaoEvent, clean).create(values)

    def _mark_processed(self):
        return super().write({'processed': True})

    company_id = fields.Many2one('res.company', required=True, readonly=True, index=True)
    kind = fields.Selection(EVENT_KINDS, required=True, readonly=True)
    notification_key = fields.Char(readonly=True, index=True)
    order_id = fields.Many2one('qing.fapiao.order', required=True, readonly=True, ondelete='cascade', check_company=True, index=True)
    payload = fields.Json(readonly=True)
    processed = fields.Boolean(readonly=True, default=True, index=True)

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_('Invoice audit events can only be created by the interface workflow.'))

    def write(self, vals):
        raise AccessError(_('Invoice audit events are immutable.'))

    def unlink(self):
        raise AccessError(_('Invoice audit events are immutable.'))
