from collections import defaultdict

from odoo import api, fields, models, Command
from odoo.exceptions import AccessError, UserError

PROTECTED_MOVE_FIELDS = {'name', 'state', 'partner_id', 'company_id', 'currency_id', 'move_type',
                         'invoice_line_ids', 'line_ids', 'invoice_date', 'date', 'invoice_payment_term_id'}
FAPIAO_STATUSES = [
    ('not_invoiced', 'Not Invoiced'),
    ('draft_request', 'Draft Request'),
    ('requested', 'Request In Progress'),
    ('pending_registration', 'Awaiting Tax Invoice Registration'),
    ('unallocated', 'Registered, Unallocated'),
    ('partial', 'Partially Invoiced'),
    ('invoiced', 'Fully Invoiced'),
]


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _qing_lock_fapiao_moves(self):
        if self:
            self.check_access('read')
            self.env.cr.execute('SELECT id FROM account_move WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(self.ids)])
            self.invalidate_recordset()

    def _qing_check_fapiao_protection(self):
        self._qing_lock_fapiao_moves()
        if not self:
            return
        self.env['qing.fapiao'].flush_model(['state', 'move_ids'])
        # Existence-only check: enforce the lock even for users without ledger read
        # rights, without exposing the tax document or elevating their access.
        self.env.cr.execute("""SELECT 1 FROM qing_fapiao_move_rel r JOIN qing_fapiao f ON f.id=r.fapiao_id
            WHERE r.move_id IN %s AND f.state='registered' LIMIT 1""", [tuple(self.ids)])
        protected = self.env.cr.fetchone()
        self.env['qing.fapiao.allocation'].flush_model(['state', 'move_id', 'fapiao_id'])
        self.env.cr.execute("""SELECT 1 FROM qing_fapiao_allocation a JOIN qing_fapiao f ON f.id=a.fapiao_id
            WHERE a.move_id IN %s AND a.state='confirmed' AND f.state='registered' LIMIT 1""", [tuple(self.ids)])
        allocated = self.env.cr.fetchone()
        self.env['qing.fapiao.request.line'].flush_model(['request_id', 'move_id'])
        self.env['qing.fapiao.request'].flush_model(['state'])
        self.env.cr.execute('''SELECT 1 FROM qing_fapiao_request_line l JOIN qing_fapiao_request r ON r.id=l.request_id
            WHERE l.move_id IN %s AND r.state IN ('submitted','approved') LIMIT 1''', [tuple(self.ids)])
        if self.env.cr.fetchone():
            raise UserError(self.env._('An active invoice request reserves this bill. Cancel the request before correcting the bill.'))
        if protected or allocated:
            raise UserError(self.env._('账单已关联登记税票。需更正时，请由财务管理员先处理税票登记；Reset不会撤销税务发票。'))

    def _qing_touch_allocation_revision(self):
        if self:
            self.env.cr.execute('UPDATE account_move SET qing_fapiao_revision = COALESCE(qing_fapiao_revision, 0) + 1 WHERE id IN %s',
                                [tuple(self.ids)])
            self.invalidate_recordset(['qing_fapiao_revision'])
            self.modified(['qing_fapiao_revision'])

    @api.depends('qing_fapiao_revision', 'amount_untaxed', 'amount_tax',
                 'qing_fapiao_allocation_ids.state', 'qing_fapiao_allocation_ids.fapiao_id.state',
                 'qing_fapiao_allocation_ids.amount_untaxed', 'qing_fapiao_allocation_ids.amount_tax')
    def _compute_qing_fapiao_remaining(self):
        for move in self:
            active = move.qing_fapiao_allocation_ids.filtered(lambda l: l.state == 'confirmed' and l.fapiao_id.state == 'registered')
            move.qing_fapiao_remaining_untaxed = move.amount_untaxed - sum(active.mapped('amount_untaxed'))
            move.qing_fapiao_remaining_tax = move.amount_tax - sum(active.mapped('amount_tax'))

    def _qing_validate_request_capacity(self):
        """Share monetary capacity between registered allocations and live requests."""
        if not self:
            return
        Allocation = self.env['qing.fapiao.allocation']
        RequestLine = self.env['qing.fapiao.request.line']
        domain = [('move_id', 'in', self.ids), ('state', '=', 'confirmed'), ('fapiao_id.state', '=', 'registered')]
        allocated = {move.id: (net, tax) for move, net, tax in Allocation._read_group(
            domain, ['move_id'], ['amount_untaxed:sum', 'amount_tax:sum'])}
        domain = [('move_id', 'in', self.ids), ('request_id.state', 'in', ['submitted', 'approved'])]
        reserved = {move.id: (net, tax) for move, net, tax in RequestLine._read_group(
            domain, ['move_id'], ['amount_untaxed:sum', 'amount_tax:sum'])}
        for move in self:
            for i, amount in enumerate((move.amount_untaxed, move.amount_tax)):
                used = allocated.get(move.id, (0, 0))[i] + reserved.get(move.id, (0, 0))[i]
                if move.currency_id.compare_amounts(used, amount) > 0:
                    raise UserError(self.env._('Confirmed tax allocations plus pending invoice requests exceed the bill amount or tax.'))
        domain = [('move_id', 'in', self.ids), '|', ('request_id.state', 'in', ['submitted', 'approved']),
                  '&', ('request_id.state', '=', 'done'), ('request_id.fapiao_id.state', '=', 'registered')]
        for source, qty, net, tax in RequestLine._read_group(domain, ['source_line_id'],
                ['quantity:sum', 'amount_untaxed:sum', 'amount_tax:sum']):
            if (qty > source.quantity + 1e-8
                    or source.currency_id.compare_amounts(net, source.price_subtotal) > 0
                    or source.currency_id.compare_amounts(tax, source.price_total - source.price_subtotal) > 0):
                raise UserError(self.env._('Active and recorded requests exceed a source line quantity, amount or tax.'))

    @api.depends('qing_fapiao_revision', 'amount_untaxed', 'amount_tax',
                 'qing_fapiao_request_line_ids.request_id.state', 'qing_fapiao_request_line_ids.amount_untaxed',
                 'qing_fapiao_request_line_ids.amount_tax', 'qing_fapiao_allocation_ids.state',
                 'qing_fapiao_allocation_ids.fapiao_id.state', 'qing_fapiao_allocation_ids.amount_untaxed',
                 'qing_fapiao_allocation_ids.amount_tax')
    def _compute_qing_request_remaining(self):
        for move in self:
            pending = move.qing_fapiao_request_line_ids.filtered(lambda l: l.request_id.state in ('submitted', 'approved'))
            move.qing_fapiao_reserved_untaxed = sum(pending.mapped('amount_untaxed'))
            move.qing_fapiao_reserved_tax = sum(pending.mapped('amount_tax'))
            move.qing_fapiao_request_remaining_untaxed = move.qing_fapiao_remaining_untaxed - move.qing_fapiao_reserved_untaxed
            move.qing_fapiao_request_remaining_tax = move.qing_fapiao_remaining_tax - move.qing_fapiao_reserved_tax

    @api.depends('amount_untaxed', 'amount_tax', 'qing_fapiao_revision',
                 'qing_fapiao_source_ids', 'qing_fapiao_source_ids.state',
                 'qing_fapiao_allocation_ids.fapiao_id', 'qing_fapiao_allocation_ids.fapiao_id.state',
                 'qing_fapiao_allocation_ids.state', 'qing_fapiao_allocation_ids.amount_untaxed',
                 'qing_fapiao_allocation_ids.amount_tax',
                 'qing_fapiao_request_line_ids.request_id', 'qing_fapiao_request_line_ids.request_id.state')
    def _compute_qing_fapiao_indicators(self):
        move_ids = set(self.ids)
        request_counts = {}
        draft_requests = set()
        active_requests = set()
        allocation_totals = {}
        ticket_ids = defaultdict(set)
        registered_tickets = set()
        draft_tickets = set()
        if move_ids:
            RequestLine = self.env['qing.fapiao.request.line']
            domain = [('move_id', 'in', self.ids)]
            request_counts = {move.id: count for move, count in RequestLine._read_group(
                domain, ['move_id'], ['request_id:count_distinct'])}
            domain = [('move_id', 'in', self.ids), ('request_id.state', '=', 'draft')]
            draft_requests = {move.id for move, _count in RequestLine._read_group(
                domain, ['move_id'], ['__count'])}
            domain = [('move_id', 'in', self.ids), ('request_id.state', 'in', ('submitted', 'approved'))]
            active_requests = {move.id for move, _count in RequestLine._read_group(
                domain, ['move_id'], ['__count'])}

            Allocation = self.env['qing.fapiao.allocation']
            domain = [('move_id', 'in', self.ids), ('state', '=', 'confirmed'),
                      ('fapiao_id.state', '=', 'registered')]
            allocation_totals = {move.id: (untaxed, tax) for move, untaxed, tax in Allocation._read_group(
                domain, ['move_id'], ['amount_untaxed:sum', 'amount_tax:sum'])}

            Fapiao = self.env['qing.fapiao']
            domain = ['|', ('move_ids', 'in', self.ids), ('allocation_ids.move_id', 'in', self.ids)]
            for ticket in Fapiao.search(domain):
                for move_id in set((ticket.move_ids | ticket.allocation_ids.move_id).ids) & move_ids:
                    ticket_ids[move_id].add(ticket.id)
                    if ticket.state == 'registered':
                        registered_tickets.add(move_id)
                    elif ticket.state == 'draft':
                        draft_tickets.add(move_id)

        for move in self:
            move.qing_fapiao_request_count = request_counts.get(move.id, 0)
            move.qing_fapiao_count = len(ticket_ids[move.id])
            if move.move_type != 'out_invoice':
                move.qing_fapiao_status = False
                continue
            untaxed, tax = allocation_totals.get(move.id, (0, 0))
            currency = move.currency_id
            covered = not currency.is_zero(untaxed) or not currency.is_zero(tax)
            if (covered and currency.compare_amounts(untaxed, move.amount_untaxed) >= 0
                    and currency.compare_amounts(tax, move.amount_tax) >= 0):
                move.qing_fapiao_status = 'invoiced'
            elif covered:
                move.qing_fapiao_status = 'partial'
            elif move.id in registered_tickets:
                move.qing_fapiao_status = 'unallocated'
            elif move.id in draft_tickets:
                move.qing_fapiao_status = 'pending_registration'
            elif move.id in active_requests:
                move.qing_fapiao_status = 'requested'
            elif move.id in draft_requests:
                move.qing_fapiao_status = 'draft_request'
            else:
                move.qing_fapiao_status = 'not_invoiced'

    qing_fapiao_allocation_ids = fields.One2many('qing.fapiao.allocation', 'move_id', groups='account.group_account_manager')
    qing_fapiao_count = fields.Integer(string='Tax Invoice Registrations', compute='_compute_qing_fapiao_indicators',
                                      groups='account.group_account_manager')
    qing_fapiao_remaining_tax = fields.Monetary(string='待分配税票税额', compute='_compute_qing_fapiao_remaining', groups='account.group_account_manager')
    qing_fapiao_remaining_untaxed = fields.Monetary(string='待分配税票金额', compute='_compute_qing_fapiao_remaining', groups='account.group_account_manager')
    qing_fapiao_request_count = fields.Integer(string='Invoice Requests', compute='_compute_qing_fapiao_indicators',
                                              groups='account.group_account_manager')
    qing_fapiao_request_line_ids = fields.One2many('qing.fapiao.request.line', 'move_id', groups='account.group_account_manager')
    qing_fapiao_request_remaining_tax = fields.Monetary(string='Available Request Tax', compute='_compute_qing_request_remaining', groups='account.group_account_manager')
    qing_fapiao_request_remaining_untaxed = fields.Monetary(string='Available Request Amount', compute='_compute_qing_request_remaining', groups='account.group_account_manager')
    qing_fapiao_reserved_tax = fields.Monetary(string='Reserved Request Tax', compute='_compute_qing_request_remaining', groups='account.group_account_manager')
    qing_fapiao_reserved_untaxed = fields.Monetary(string='Reserved Request Amount', compute='_compute_qing_request_remaining', groups='account.group_account_manager')
    qing_fapiao_revision = fields.Integer(default=0, readonly=True)
    qing_fapiao_source_ids = fields.Many2many('qing.fapiao', 'qing_fapiao_move_rel', 'move_id', 'fapiao_id',
                                             string='Source Tax Invoices', readonly=True, groups='account.group_account_manager')
    qing_fapiao_status = fields.Selection(FAPIAO_STATUSES, string='Tax Invoice Status',
                                         compute='_compute_qing_fapiao_indicators', groups='account.group_account_manager')

    def action_create_fapiao_request(self):
        self.check_access('read')
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(self.env._('Only accounting administrators can process invoice requests.'))
        if (not self or any(m.state != 'posted' or m.move_type != 'out_invoice' for m in self)
                or self.company_id != self.env.company or len(self.currency_id) != 1
                or len(self.commercial_partner_id) != 1):
            raise UserError(self.env._('Select posted customer invoices from the current company, one currency and one customer.'))
        partner = self.commercial_partner_id
        values = []
        invoice_lines = self.invoice_line_ids.filtered(lambda line: line.display_type == 'product')
        eligible_lines = invoice_lines.filtered(lambda line: line._qing_fapiao_has_nonzero_tax_rate())
        if not eligible_lines:
            raise UserError(self.env._('There are no invoice detail lines eligible for a tax invoice request.'))
        for line in eligible_lines:
            values.append(Command.create({'source_line_id': line.id, 'description': line.name,
                'quantity': line.quantity, 'amount_untaxed': line.price_subtotal,
                'amount_tax': line.price_total - line.price_subtotal}))
        request = self.env['qing.fapiao.request'].create({'company_id': self.company_id.id,
            'currency_id': self.currency_id.id, 'partner_id': partner.id, 'buyer_name': partner.name,
            'buyer_tax_id': partner.vat, 'seller_name': self.company_id.name,
            'seller_tax_id': self.company_id.vat, 'line_ids': values})
        return request._open()

    def action_open_fapiao_requests(self):
        self.ensure_one()
        self.check_access('read')
        return {'type': 'ir.actions.act_window', 'name': self.env._('Tax Invoice Requests'),
                'res_model': 'qing.fapiao.request', 'view_mode': 'list,form',
                'domain': [('line_ids.move_id', '=', self.id)]}

    def action_open_fapiao(self):
        self.ensure_one()
        self.check_access('read')
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(self.env._('仅财务管理员可查看税票登记。'))
        return {'type': 'ir.actions.act_window', 'name': self.env._('关联税票登记'),
                'res_model': 'qing.fapiao', 'view_mode': 'list,form',
                'domain': ['|', ('move_ids', 'in', self.ids), ('allocation_ids.move_id', 'in', self.ids)]}

    def button_draft(self):
        self._qing_check_fapiao_protection()
        return super().button_draft()

    def write(self, vals):
        if 'qing_fapiao_revision' in vals:
            raise AccessError(self.env._('分配修订号由系统维护。'))
        if PROTECTED_MOVE_FIELDS & vals.keys():
            self._qing_check_fapiao_protection()
        return super().write(vals)

    def unlink(self):
        self._qing_check_fapiao_protection()
        return super().unlink()
