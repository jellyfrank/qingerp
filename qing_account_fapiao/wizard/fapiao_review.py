from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

BUILD_TOKEN = object()
MAX_SOURCES = 2000
DIRECTIONS = [('all', 'Input and Output'), ('in', 'Input'), ('out', 'Output')]
FILTER_FIELDS = {'company_id', 'date_from', 'date_to', 'direction', 'partner_id'}
RESULT_FIELDS = {'line_ids', 'generated_at', 'source_count'}


class FapiaoReview(models.TransientModel):
    _name = 'qing.fapiao.review'
    _description = 'Tax Invoice Difference Review'

    def _check_scope(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(self.env._('Only accounting administrators can review tax invoice differences.'))
        if self.company_id != self.env.company:
            raise UserError(self.env._('Switch to the selected company before checking differences.'))
        if not self.date_from or not self.date_to or self.date_from > self.date_to:
            raise UserError(self.env._('Enter a valid date range.'))

    def _sources(self):
        ticket_domain = [('company_id', '=', self.company_id.id), ('state', '!=', 'cancelled'),
                         ('invoice_date', '>=', self.date_from), ('invoice_date', '<=', self.date_to)]
        move_domain = [('company_id', '=', self.company_id.id), ('state', '=', 'posted'),
                       ('date', '>=', self.date_from), ('date', '<=', self.date_to),
                       ('move_type', 'in', ['in_invoice', 'in_refund', 'out_invoice', 'out_refund'])]
        if self.direction != 'all':
            ticket_domain.append(('direction', '=', self.direction))
            move_domain.append(('move_type', 'in', ['in_invoice', 'in_refund'] if self.direction == 'in'
                                else ['out_invoice', 'out_refund']))
        if self.partner_id:
            partner_id = self.partner_id.commercial_partner_id.id
            ticket_domain.append(('partner_id.commercial_partner_id', '=', partner_id))
            move_domain.append(('commercial_partner_id', '=', partner_id))
        tickets = self.env['qing.fapiao'].search(ticket_domain, limit=MAX_SOURCES + 1)
        moves = self.env['account.move'].search(move_domain, limit=MAX_SOURCES + 1)
        if len(tickets) > MAX_SOURCES or len(moves) > MAX_SOURCES:
            raise UserError(self.env._('Too many source documents. Narrow the date range or select a partner.'))
        return tickets, moves

    def _allocation_totals(self, source_field, records):
        Allocation = self.env['qing.fapiao.allocation']
        domain = [(source_field, 'in', records.ids), ('fapiao_id.state', '=', 'registered'),
                  ('state', 'in', ['draft', 'confirmed'])]
        groups = Allocation._read_group(domain, [source_field, 'state'],
                                        ['amount_untaxed:sum', 'amount_tax:sum', '__count'])
        totals, pending = {}, {}
        for source, state, net, tax, count in groups:
            if state == 'confirmed':
                totals[source.id] = (net, tax)
            else:
                pending[source.id] = count
        return totals, pending

    def _prepare_lines(self, tickets, moves):
        lines = []
        for source_field, records in (('fapiao_id', tickets), ('move_id', moves)):
            totals, pending = self._allocation_totals(source_field, records)
            for record in records:
                currency = record.currency_id
                net, tax = totals.get(record.id, (0, 0))
                remaining_net = currency.round(record.amount_untaxed - net)
                remaining_tax = currency.round(record.amount_tax - tax)
                ticket = source_field == 'fapiao_id'
                issue = False
                if record.amount_untaxed < 0 or record.amount_tax < 0:
                    issue = 'unsupported'
                elif remaining_net < 0 or remaining_tax < 0:
                    issue = 'overallocated'
                elif ticket and record.color == 'red' and not record.original_id:
                    issue = 'legacy_red'
                elif ticket and record.state == 'draft':
                    issue = 'registration'
                elif pending.get(record.id):
                    issue = 'confirmation'
                elif not currency.is_zero(remaining_net) or not currency.is_zero(remaining_tax):
                    issue = 'ticket_balance' if ticket else 'bill_balance'
                if not issue:
                    continue
                lines.append({source_field: record.id, 'review_id': self.id, 'issue': issue,
                    'document_name': record.number if ticket else record.name,
                    'document_date': record.invoice_date if ticket else record.date,
                    'partner_id': record.partner_id.id, 'currency_id': currency.id,
                    'direction': record.direction if ticket else ('in' if record.move_type.startswith('in_') else 'out'),
                    'color': record.color if ticket else ('red' if record.move_type.endswith('refund') else 'blue'),
                    'amount_untaxed': record.amount_untaxed, 'amount_tax': record.amount_tax,
                    'allocated_untaxed': net, 'allocated_tax': tax,
                    'remaining_untaxed': remaining_net, 'remaining_tax': remaining_tax,
                    'pending_count': pending.get(record.id, 0)})
        return lines

    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company, required=True)
    date_from = fields.Date(string='From', default=lambda self: fields.Date.start_of(fields.Date.context_today(self), 'month'), required=True)
    date_to = fields.Date(string='To', default=fields.Date.context_today, required=True)
    direction = fields.Selection(DIRECTIONS, string='Direction', default='all', required=True)
    generated_at = fields.Datetime(string='Checked At', readonly=True)
    line_ids = fields.One2many('qing.fapiao.review.line', 'review_id', string='Differences', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Partner')
    source_count = fields.Integer(string='Documents Checked', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if RESULT_FIELDS & vals.keys() or any('default_' + key in self.env.context for key in RESULT_FIELDS):
                raise AccessError(self.env._('Check results can only be generated by the review action.'))
        return super().create(vals_list)

    def write(self, vals):
        if RESULT_FIELDS & vals.keys():
            raise AccessError(self.env._('Check results can only be generated by the review action.'))
        if FILTER_FIELDS & vals.keys():
            self.line_ids.with_context(fapiao_review_token=BUILD_TOKEN).unlink()
            vals = dict(vals, generated_at=False, source_count=0)
        return super().write(vals)

    def action_check(self):
        self._check_scope()
        tickets, moves = self._sources()
        values = self._prepare_lines(tickets, moves)
        with self.env.cr.savepoint():
            self.line_ids.with_context(fapiao_review_token=BUILD_TOKEN).unlink()
            self.env['qing.fapiao.review.line'].with_context(fapiao_review_token=BUILD_TOKEN).create(values)
            super().write({'generated_at': fields.Datetime.now(), 'source_count': len(tickets) + len(moves)})
        return {'type': 'ir.actions.act_window', 'name': self.env._('Tax Invoice Difference Review'),
                'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'current'}
