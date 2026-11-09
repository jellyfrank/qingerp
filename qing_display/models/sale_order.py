from odoo import models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        pending = self.filtered(lambda order: order.state in ('draft', 'sent'))
        result = super().action_confirm()
        Event = self.env['qing.display.event'].sudo()
        confirmed = pending.filtered(lambda record: record.state == 'sale')
        domain = [('order_id', 'in', confirmed.ids), ('event_type', '=', 'sale_confirmed')]
        existing_ids = set(Event.search(domain).mapped('order_id').ids)
        for order in confirmed.filtered(lambda record: record.id not in existing_ids):
            company = order.company_id
            currency = company.currency_id
            amount_company = order.currency_id._convert(
                order.amount_total, currency, company, order.date_order.date(),
            )
            Event.create({
                'amount_company': amount_company,
                'company_id': company.id,
                'currency_id': currency.id,
                'event_type': 'sale_confirmed',
                'name': order.name,
                'occurred_at': order.date_order,
                'order_id': order.id,
                'partner_name': order.partner_id.name,
            })
        return result
