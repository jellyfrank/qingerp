from collections import defaultdict

from odoo import api, fields, models


ISSUANCE_STATES = [
    ('no_invoice', 'No Posted Customer Invoice'),
    ('not_issued', 'Not Issued'),
    ('pending', 'Pending Tax Invoice'),
    ('partial', 'Partially Issued'),
    ('issued', 'Issued'),
    ('review', 'Needs Review'),
]
REVERSAL_STATES = [
    ('none', 'No Red Reversal'),
    ('pending', 'Pending Red Reversal'),
    ('partial', 'Partially Reversed'),
    ('reversed', 'Fully Reversed'),
    ('review', 'Needs Review'),
]


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _get_qing_fapiao_move_records(self, move_ids):
        """Fetch allocations, blue tickets and active requests for the batch."""
        allocations_by_move = defaultdict(list)
        tickets_by_move = defaultdict(list)
        requests_by_move = defaultdict(list)
        if not move_ids:
            return allocations_by_move, tickets_by_move, requests_by_move

        Allocation = self.env['qing.fapiao.allocation'].sudo()
        Fapiao = self.env['qing.fapiao'].sudo()
        RequestLine = self.env['qing.fapiao.request.line'].sudo()
        allocation_domain = [('move_id', 'in', list(move_ids))]
        for allocation in Allocation.search(allocation_domain):
            allocations_by_move[allocation.move_id.id].append(allocation)

        ticket_domain = [
            ('direction', '=', 'out'), ('color', '=', 'blue'),
            ('move_ids', 'in', list(move_ids)),
        ]
        for ticket in Fapiao.search(ticket_domain):
            for move in ticket.move_ids:
                if move.id in move_ids:
                    tickets_by_move[move.id].append(ticket)

        request_domain = [
            ('move_id', 'in', list(move_ids)),
            ('request_id.state', 'in', ['submitted', 'approved']),
        ]
        for line in RequestLine.search(request_domain):
            requests_by_move[line.move_id.id].append(line)
        return allocations_by_move, tickets_by_move, requests_by_move

    def _get_qing_fapiao_red_records(self, orders, allocations_by_move, tickets_by_move):
        """Fetch linked red tickets and legacy red numbers for relevant blues."""
        reds_by_blue = defaultdict(list)
        legacy_red_numbers = set()
        blue_ids = {
            allocation.fapiao_id.id for lines in allocations_by_move.values()
            for allocation in lines if allocation.fapiao_id.color == 'blue'
        }
        blue_ids.update(ticket.id for tickets in tickets_by_move.values() for ticket in tickets)
        if not blue_ids:
            return reds_by_blue, legacy_red_numbers

        Fapiao = self.env['qing.fapiao'].sudo()
        blues = Fapiao.browse(blue_ids)
        linked_domain = [
            ('direction', '=', 'out'), ('color', '=', 'red'),
            ('original_id', 'in', list(blue_ids)),
            ('state', 'in', ['draft', 'registered']),
        ]
        for red in Fapiao.search(linked_domain):
            reds_by_blue[red.original_id.id].append(red)

        legacy_domain = [
            ('direction', '=', 'out'), ('color', '=', 'red'),
            ('original_id', '=', False), ('state', '=', 'registered'),
            ('original_number', 'in', blues.mapped('number')),
            ('company_id', 'in', orders.mapped('company_id').ids),
        ]
        legacy_red_numbers = {
            (red.company_id.id, red.original_number) for red in Fapiao.search(legacy_domain)
        }
        return reds_by_blue, legacy_red_numbers

    def _get_qing_fapiao_blue_progress(
            self, order, invoices, allocations_by_move, tickets_by_move, requests_by_move):
        """Check attribution and sum confirmed blue allocations for an order."""
        currency = order.currency_id
        pending = review = False
        issued_untaxed = issued_tax = 0.0
        blue_allocations = self.env['qing.fapiao.allocation'].sudo()
        for move in invoices:
            if move.currency_id != currency or move.company_id != order.company_id:
                review = True
            product_lines = move.invoice_line_ids.filtered(lambda line: line.display_type == 'product')
            if not product_lines or any(
                    set(line.sale_line_ids.order_id.ids) != {order.id}
                    for line in product_lines):
                # Mixed-order and manual lines have no trustworthy order allocation.
                review = True
            if (currency.compare_amounts(move.amount_untaxed, 0) < 0
                    or currency.compare_amounts(move.amount_tax, 0) < 0):
                review = True
            if requests_by_move[move.id]:
                pending = True
            for ticket in tickets_by_move[move.id]:
                if ticket.company_id != order.company_id or ticket.currency_id != currency:
                    review = True
                if ticket.state == 'draft':
                    pending = True
                elif ticket.state == 'registered' and not any(
                        allocation.state == 'confirmed' and allocation.fapiao_id == ticket
                        for allocation in allocations_by_move[move.id]):
                    review = True
            for allocation in allocations_by_move[move.id]:
                ticket = allocation.fapiao_id
                if ticket.color != 'blue' or ticket.direction != 'out':
                    continue
                if ticket.company_id != order.company_id or ticket.currency_id != currency:
                    review = True
                    continue
                if ticket.state == 'draft' or allocation.state == 'draft':
                    pending = True
                if ticket.state == 'registered' and allocation.state == 'confirmed':
                    issued_untaxed += allocation.amount_untaxed
                    issued_tax += allocation.amount_tax
                    blue_allocations |= allocation
        return issued_untaxed, issued_tax, pending, review, blue_allocations

    def _get_qing_fapiao_issuance_state(
            self, invoices, currency, issued_untaxed, issued_tax, pending, review):
        """Classify blue progress against posted invoice amounts."""
        target_untaxed = sum(invoices.mapped('amount_untaxed'))
        target_tax = sum(invoices.mapped('amount_tax'))
        if (currency.compare_amounts(issued_untaxed, target_untaxed) > 0
                or currency.compare_amounts(issued_tax, target_tax) > 0
                or (currency.is_zero(target_untaxed) and currency.is_zero(target_tax))):
            review = True
        if review:
            return 'review', review
        if (currency.compare_amounts(issued_untaxed, target_untaxed) == 0
                and currency.compare_amounts(issued_tax, target_tax) == 0):
            return 'issued', review
        if not currency.is_zero(issued_untaxed) or not currency.is_zero(issued_tax):
            return 'partial', review
        return ('pending' if pending else 'not_issued'), review

    def _get_qing_fapiao_reversal_state(
            self, order, invoices, blue_allocations, issued_untaxed, issued_tax,
            blue_review, reds_by_blue, legacy_red_numbers):
        """Attribute red tickets only when their entire blue belongs to this order."""
        currency = order.currency_id
        reversed_untaxed = reversed_tax = 0.0
        red_review = red_pending = False
        for blue in blue_allocations.fapiao_id:
            if (blue.company_id.id, blue.number) in legacy_red_numbers:
                red_review = True
            reds = reds_by_blue[blue.id]
            if any(red.state == 'draft' for red in reds):
                red_pending = True
            registered = [red for red in reds if red.state == 'registered']
            if not registered:
                continue
            active = blue.allocation_ids.filtered(lambda line:
                line.state == 'confirmed' and blue.state == 'registered')
            if (set(active.move_id.ids) - set(invoices.ids)
                    or currency.compare_amounts(sum(active.mapped('amount_untaxed')), blue.amount_untaxed)
                    or currency.compare_amounts(sum(active.mapped('amount_tax')), blue.amount_tax)):
                red_review = True
                continue
            reversed_untaxed += sum(red.amount_untaxed for red in registered)
            reversed_tax += sum(red.amount_tax for red in registered)

        if red_review or (blue_review and (reversed_untaxed or reversed_tax)):
            return 'review', red_pending
        if currency.is_zero(reversed_untaxed) and currency.is_zero(reversed_tax):
            return ('pending' if red_pending else 'none'), red_pending
        if (currency.compare_amounts(reversed_untaxed, issued_untaxed) == 0
                and currency.compare_amounts(reversed_tax, issued_tax) == 0):
            return 'reversed', red_pending
        return 'partial', red_pending

    @api.depends('invoice_ids', 'invoice_ids.state', 'invoice_ids.amount_untaxed',
                 'invoice_ids.amount_tax', 'invoice_ids.invoice_line_ids.sale_line_ids')
    def _compute_qing_fapiao_status(self):
        # Sales users see only coarse states; ledger queries retain their ACL bypass.
        orders = self.sudo()
        invoices_by_order = {
            order.id: order.invoice_ids.filtered(
                lambda move: move.state == 'posted' and move.move_type == 'out_invoice')
            for order in orders
        }
        move_ids = {move.id for moves in invoices_by_order.values() for move in moves}
        (allocations_by_move, tickets_by_move,
         requests_by_move) = self._get_qing_fapiao_move_records(move_ids)
        reds_by_blue, legacy_red_numbers = self._get_qing_fapiao_red_records(
            orders, allocations_by_move, tickets_by_move)

        for order in self:
            invoices = invoices_by_order[order.id]
            if not invoices:
                order.qing_fapiao_issuance_state = 'no_invoice'
                order.qing_fapiao_reversal_state = 'none'
                order.qing_fapiao_pending = False
                continue

            (issued_untaxed, issued_tax, pending, review,
             blue_allocations) = self._get_qing_fapiao_blue_progress(
                order, invoices, allocations_by_move, tickets_by_move, requests_by_move)
            issuance, review = self._get_qing_fapiao_issuance_state(
                invoices, order.currency_id, issued_untaxed, issued_tax, pending, review)
            reversal, red_pending = self._get_qing_fapiao_reversal_state(
                order, invoices, blue_allocations, issued_untaxed, issued_tax,
                review, reds_by_blue, legacy_red_numbers)
            order.qing_fapiao_issuance_state = issuance
            order.qing_fapiao_reversal_state = reversal
            order.qing_fapiao_pending = pending or red_pending

    qing_fapiao_issuance_state = fields.Selection(
        ISSUANCE_STATES, string='Tax Invoice Status', compute='_compute_qing_fapiao_status',
        help='Compares confirmed allocations of registered blue tax invoices with posted customer invoices. '
             'This differs from the accounting invoice status.')
    qing_fapiao_pending = fields.Boolean(
        string='Tax Invoice Work Pending', compute='_compute_qing_fapiao_status',
        help='An active request, draft tax invoice, or unconfirmed allocation exists.')
    qing_fapiao_reversal_state = fields.Selection(
        REVERSAL_STATES, string='Tax Invoice Reversal', compute='_compute_qing_fapiao_status',
        help='Red tax invoices are assessed separately. A red invoice shared across orders needs review.')
