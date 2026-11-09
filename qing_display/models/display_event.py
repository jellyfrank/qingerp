from datetime import timedelta

from odoo import api, fields, models


class QingDisplayEvent(models.Model):
    _name = 'qing.display.event'
    _description = 'Qing Display Event'
    _order = 'id desc'

    _unique_order_event = models.Constraint(
        'UNIQUE(order_id, event_type)',
        'A sales order can only create one display event of each type.',
    )

    @api.model
    def _cron_prune(self):
        cutoff = fields.Datetime.now() - timedelta(days=30)
        domain = [('occurred_at', '<', cutoff)]
        self.sudo().search(domain).unlink()

    amount_company = fields.Monetary(currency_field='currency_id', readonly=True)
    company_id = fields.Many2one('res.company', required=True, index=True, readonly=True)
    currency_id = fields.Many2one('res.currency', required=True, readonly=True)
    event_type = fields.Selection(
        [('sale_confirmed', 'Sales Order Confirmed')], required=True, readonly=True,
    )
    name = fields.Char(required=True, readonly=True)
    occurred_at = fields.Datetime(required=True, default=fields.Datetime.now, index=True, readonly=True)
    order_id = fields.Many2one('sale.order', required=True, ondelete='cascade', readonly=True)
    partner_name = fields.Char(readonly=True)
