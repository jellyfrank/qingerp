from odoo import fields, models
from odoo.exceptions import AccessError


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def _qing_portal_home_values(self, todo_limit=6, recent_limit=5):
        """Values for the QingERP portal account home of this contact.

        Runs with the caller's environment, so a portal user only sees what
        the native portal pages would show them (record rules apply). Sales
        and invoicing are optional: each part is skipped when its app is not
        installed or the user cannot read it.

        Returns a dict with:
        - ``greeting``: ``morning``, ``afternoon`` or ``evening`` in the
          user's time zone;
        - ``todo``: documents waiting for the customer (overdue invoices,
          quotations to sign or pay, unpaid invoices), most urgent first;
        - ``unpaid_total`` / ``unpaid_currency``: what is left to pay, when
          all open invoices share one currency;
        - ``overdue_count``, ``open_order_count``;
        - ``recent``: the latest quotations, orders and invoices.
        """
        self.ensure_one()
        partner = self.commercial_partner_id
        today = fields.Date.context_today(self)
        hour = fields.Datetime.context_timestamp(self, fields.Datetime.now()).hour
        values = {
            'greeting': 'morning' if hour < 12 else 'afternoon' if hour < 18 else 'evening',
            'todo': [],
            'recent': [],
            'unpaid_total': False,
            'unpaid_currency': False,
            'overdue_count': 0,
            'open_order_count': False,
        }
        overdue, quotes, unpaid = [], [], []

        if 'account.move' in self.env:
            try:
                invoices = self.env['account.move'].search([
                    ('move_type', 'in', ('out_invoice', 'out_receipt')),
                    ('state', '=', 'posted'),
                    ('partner_id', 'child_of', partner.id),
                ], order='invoice_date desc, id desc', limit=200)
            except AccessError:
                invoices = self.env['account.move']
            open_invoices = invoices.filtered(lambda m: m.payment_state in ('not_paid', 'partial'))
            for move in open_invoices.sorted(lambda m: (m.invoice_date_due or today, m.id)):
                due = move.invoice_date_due
                item = {
                    'kind': 'invoice',
                    'record': move,
                    'name': move.name,
                    'amount': move.amount_residual,
                    'currency': move.currency_id,
                    'date': due,
                    'days': abs((due - today).days) if due else 0,
                    'overdue': bool(due and due < today),
                    'url': move.get_portal_url(),
                }
                (overdue if item['overdue'] else unpaid).append(item)
            values['overdue_count'] = len(overdue)
            currencies = open_invoices.currency_id
            if len(currencies) == 1:
                values['unpaid_total'] = sum(open_invoices.mapped('amount_residual'))
                values['unpaid_currency'] = currencies
            values['recent'] += [{
                'kind': 'invoice',
                'name': move.name,
                'date': move.invoice_date or move.date,
                'amount': move.amount_total,
                'currency': move.currency_id,
                'url': move.get_portal_url(),
                'due': move.invoice_date_due,
            } for move in invoices[:recent_limit]]

        if 'sale.order' in self.env:
            SaleOrder = self.env['sale.order']
            try:
                sent = SaleOrder.search([
                    ('partner_id', 'child_of', [partner.id]),
                    ('state', '=', 'sent'),
                ], order='date_order desc, id desc', limit=50)
                confirmed = SaleOrder.search([
                    ('partner_id', 'child_of', [partner.id]),
                    ('state', '=', 'sale'),
                ], order='date_order desc, id desc', limit=200)
            except AccessError:
                sent = confirmed = SaleOrder
            waiting = sent.filtered(lambda o: not o.is_expired and (o._has_to_be_signed() or o._has_to_be_paid()))
            for order in waiting.sorted(lambda o: (o.validity_date or fields.Date.add(today, years=10), o.id)):
                quotes.append({
                    'kind': 'quote',
                    'record': order,
                    'name': order.name,
                    'amount': order.amount_total,
                    'currency': order.currency_id,
                    'date': order.validity_date,
                    'days': (order.validity_date - today).days if order.validity_date else False,
                    'overdue': False,
                    'sign': order._has_to_be_signed(),
                    'url': order.get_portal_url(),
                })
            if 'delivery_status' in confirmed._fields:
                in_progress = confirmed.filtered(lambda o: o.delivery_status != 'full')
            else:
                in_progress = confirmed.filtered(lambda o: o.invoice_status != 'invoiced')
            values['open_order_count'] = len(in_progress)
            values['recent'] += [{
                'kind': 'quote',
                'name': order.name,
                'date': fields.Date.to_date(fields.Datetime.context_timestamp(self, order.date_order)),
                'amount': order.amount_total,
                'currency': order.currency_id,
                'url': order.get_portal_url(),
                'due': order.validity_date,
            } for order in sent[:recent_limit]]
            values['recent'] += [{
                'kind': 'order',
                'name': order.name,
                'date': fields.Date.to_date(fields.Datetime.context_timestamp(self, order.date_order)),
                'amount': order.amount_total,
                'currency': order.currency_id,
                'url': order.get_portal_url(),
                'due': False,
            } for order in confirmed[:recent_limit]]

        values['todo'] = (overdue + quotes + unpaid)[:todo_limit]
        values['recent'] = sorted(values['recent'], key=lambda r: (r['date'] or today), reverse=True)[:recent_limit]
        return values
