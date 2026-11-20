from odoo import fields, models
from odoo.fields import Domain


class ChannelStore(models.Model):
    _name = 'qing.channel.store'
    _description = 'Sales Channel Store'
    _check_company_auto = True

    def _import_delivery(self, delivery):
        raise NotImplementedError(self.platform)

    def _execute_channel_action(self, action):
        raise NotImplementedError(action.kind)

    def _execute_channel_request(self, channel_request, recovering):
        raise NotImplementedError(channel_request.operation)

    def _channel_worker_domain(self):
        return Domain('active', '=', True)

    def _channel_worker_ready(self):
        return bool(self.filtered_domain(self._channel_worker_domain()))

    def _channel_operation_failed(self, code):
        """Return whether work can continue after the platform handles the failure."""
        return self._channel_worker_ready()

    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    cutover_at = fields.Datetime(copy=False)
    name = fields.Char(required=True)
    onboarded = fields.Boolean(default=False, copy=False)
    platform = fields.Selection(selection=[('manual', 'Manual')], required=True, default='manual')
    warehouse_id = fields.Many2one('stock.warehouse', required=True, check_company=True)
