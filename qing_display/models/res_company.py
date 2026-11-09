from datetime import datetime, time, timedelta, timezone

import pytz

from odoo import api, fields, models
from odoo.exceptions import AccessError


class ResCompany(models.Model):
    _inherit = 'res.company'

    def _qing_display_boundaries(self):
        user_tz = pytz.timezone(self.env.user.tz or 'UTC')
        local_today = fields.Datetime.context_timestamp(self, fields.Datetime.now()).date()

        def utc_start(day):
            local_start = user_tz.localize(datetime.combine(day, time.min))
            return local_start.astimezone(timezone.utc).replace(tzinfo=None)

        today_start = utc_start(local_today)
        tomorrow_start = utc_start(local_today + timedelta(days=1))
        month_start = utc_start(local_today.replace(day=1))
        return local_today, today_start, tomorrow_start, month_start, utc_start

    def _qing_display_amount(self, SaleReport, company_id, start, end=None):
        domain = [
            ('company_id', '=', company_id),
            ('state', '=', 'sale'),
            ('date', '>=', start),
        ]
        if end:
            domain.append(('date', '<', end))
        groups = SaleReport._read_group(domain, [], ['price_total:sum'])
        return round(float(groups[0][0] or 0.0), 2)

    def _qing_display_events(self, company_id, last_event_id):
        Event = self.env['qing.display.event'].sudo()
        company_domain = [('company_id', '=', company_id)]
        if last_event_id is None:
            latest = Event.search(company_domain, order='id desc', limit=1)
            return [], latest.id or 0

        cutoff = fields.Datetime.now() - timedelta(minutes=15)
        domain = company_domain + [
            ('id', '>', last_event_id),
            ('occurred_at', '>=', cutoff),
        ]
        events = Event.search(domain, order='id asc', limit=20)
        if not events:
            latest = Event.search(company_domain, order='id desc', limit=1)
            return [], max(last_event_id, latest.id or 0)

        return [
            {
                'id': event.id,
                'name': event.name,
                'partner': event.partner_name,
                'amount': round(event.amount_company, 2),
                'time': fields.Datetime.context_timestamp(self, event.occurred_at).strftime('%H:%M'),
            }
            for event in events
        ], events[-1].id

    @api.model
    def qing_display_snapshot(self, company_id, last_event_id=None):
        """Return company-scoped, read-only sales figures for the signed-in display user."""
        user = self.env.user
        if not user.has_group('qing_display.group_display_viewer'):
            raise AccessError(self.env._('You are not allowed to open Qing Display.'))
        try:
            company_id = int(company_id)
            last_event_id = None if last_event_id is None else max(0, int(last_event_id))
        except (TypeError, ValueError) as error:
            raise AccessError(self.env._('Invalid display request.')) from error
        if company_id not in user.company_ids.ids:
            raise AccessError(self.env._('You cannot view this company.'))

        company = self.browse(company_id)
        local_today, today_start, tomorrow_start, month_start, utc_start = (
            self._qing_display_boundaries()
        )
        SaleOrder = self.env['sale.order'].sudo()
        SaleReport = self.env['sale.report'].sudo()
        today_domain = [
            ('company_id', '=', company_id),
            ('state', '=', 'sale'),
            ('date_order', '>=', today_start),
            ('date_order', '<', tomorrow_start),
        ]
        quotation_domain = [
            ('company_id', '=', company_id),
            ('state', 'in', ['draft', 'sent']),
        ]
        recent_domain = [('company_id', '=', company_id), ('state', '=', 'sale')]
        recent_orders = SaleOrder.search(recent_domain, order='date_order desc, id desc', limit=6)
        trends = []
        for days_ago in range(6, -1, -1):
            day = local_today - timedelta(days=days_ago)
            start = utc_start(day)
            end = utc_start(day + timedelta(days=1))
            trends.append({
                'label': day.strftime('%m-%d'),
                'amount': self._qing_display_amount(SaleReport, company_id, start, end),
            })

        ranking_domain = [
            ('company_id', '=', company_id),
            ('state', '=', 'sale'),
            ('date', '>=', month_start),
        ]
        ranking = SaleReport._read_group(
            ranking_domain, ['partner_id'], ['price_total:sum'],
            order='price_total:sum DESC', limit=5,
        )
        events, cursor = self._qing_display_events(company_id, last_event_id)
        return {
            'company': company.name,
            'companies': [{'id': item.id, 'name': item.name} for item in user.company_ids],
            'company_id': company_id,
            'currency': company.currency_id.symbol or company.currency_id.name,
            'cursor': cursor,
            'events': events,
            'generated_at': fields.Datetime.context_timestamp(
                self, fields.Datetime.now(),
            ).strftime('%Y-%m-%d %H:%M:%S'),
            'month_amount': self._qing_display_amount(SaleReport, company_id, month_start),
            'pending_quotations': SaleOrder.search_count(quotation_domain),
            'ranking': [
                {'name': partner.name if partner else self.env._('Unknown'),
                 'amount': round(float(amount or 0.0), 2)}
                for partner, amount in ranking
            ],
            'recent_orders': [
                {
                    'name': order.name,
                    'partner': order.partner_id.name,
                    'amount': round(order.currency_id._convert(
                        order.amount_total, company.currency_id, company,
                        order.date_order.date(),
                    ), 2),
                    'time': fields.Datetime.context_timestamp(
                        self, order.date_order,
                    ).strftime('%m-%d %H:%M'),
                }
                for order in recent_orders
            ],
            'today_amount': self._qing_display_amount(
                SaleReport, company_id, today_start, tomorrow_start,
            ),
            'today_count': SaleOrder.search_count(today_domain),
            'trends': trends,
        }
