from psycopg2 import IntegrityError

from odoo import api, fields, models, Command
from odoo.exceptions import AccessError, UserError
from .fapiao import KINDS

STATES = [('draft', 'Draft'), ('submitted', 'Pending Approval'), ('approved', 'Awaiting Invoice'),
          ('done', 'Recorded'), ('cancelled', 'Cancelled')]
AUDIT_FIELDS = {'name', 'state', 'submitted_at', 'submitted_by', 'approved_at', 'approved_by',
                'completed_at', 'completed_by', 'cancelled_at', 'cancelled_by', 'reset_at', 'reset_by', 'fapiao_id', 'revision'}
RESULT_FIELDS = {'result_number', 'result_code', 'result_date', 'result_file', 'result_filename', 'result_checked', 'result_amount_untaxed', 'result_amount_tax'}


class FapiaoRequest(models.Model):
    _name = 'qing.fapiao.request'
    _description = 'Tax Invoice Request'
    _order = 'id desc'
    _check_company_auto = True

    def _lock(self):
        self.check_access('write')
        if self:
            self.env.cr.execute('SELECT id FROM qing_fapiao_request WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(self.ids)])
            self.invalidate_recordset()

    def _touch(self):
        if self:
            self.env.cr.execute('UPDATE qing_fapiao_request SET revision=COALESCE(revision,0)+1 WHERE id IN %s', [tuple(self.ids)])
            self.invalidate_recordset(['revision'])

    def _check_scope(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(self.env._('Only accounting administrators can process invoice requests.'))
        if self.company_id != self.env.company:
            raise UserError(self.env._('Switch to the request company before proceeding.'))

    @api.depends('line_ids.amount_untaxed', 'line_ids.amount_tax')
    def _compute_amounts(self):
        for record in self:
            record.amount_untaxed = sum(record.line_ids.mapped('amount_untaxed'))
            record.amount_tax = sum(record.line_ids.mapped('amount_tax'))
            record.amount_total = record.amount_untaxed + record.amount_tax

    @api.depends('partner_id', 'company_id.partner_id')
    def _compute_party_partners(self):
        for record in self:
            record.buyer_partner_id = record.partner_id.commercial_partner_id
            record.seller_partner_id = record.company_id.partner_id.commercial_partner_id

    @api.model
    def _party_snapshot_defaults(self, vals):
        company = self.env['res.company'].browse(vals.get('company_id')) or self.env.company
        buyer = self.env['res.partner'].browse(vals.get('partner_id')).commercial_partner_id
        seller = company.partner_id.commercial_partner_id
        defaults = {}
        for key, value in (
            ('buyer_name', buyer.name), ('buyer_tax_id', buyer.vat),
            ('seller_name', seller.name), ('seller_tax_id', seller.vat),
        ):
            if key not in vals:
                defaults[key] = value
        return defaults

    @api.onchange('partner_id', 'company_id')
    def _onchange_parties(self):
        self.buyer_name = self.partner_id.commercial_partner_id.name
        self.buyer_tax_id = self.partner_id.commercial_partner_id.vat
        self.seller_name = self.company_id.partner_id.name
        self.seller_tax_id = self.company_id.partner_id.vat

    @api.onchange(*(RESULT_FIELDS - {'result_checked'}))
    def _onchange_result(self):
        self.result_checked = False

    def _validate_submission(self):
        if not self.line_ids:
            raise UserError(self.env._('Add at least one invoice line.'))
        if not (self.buyer_name or '').strip() or not (self.seller_name or '').strip():
            raise UserError(self.env._('Enter the buyer and seller names.'))
        if not (self.seller_tax_id or '').strip():
            raise UserError(self.env._('Enter the seller tax ID.'))
        if self.kind == 'special' and not (self.buyer_tax_id or '').strip():
            raise UserError(self.env._('Enter the buyer tax ID for special VAT invoices.'))
        self.line_ids._check_values()
        if any(not (line.tax_code or '').strip() for line in self.line_ids):
            raise UserError(self.env._('Enter and review the tax classification code for every line.'))
        if any(not line._valid_tax_code() for line in self.line_ids):
            raise UserError(self.env._('Tax classification codes must contain exactly 19 digits.'))
        if any(line.tax_classification_id and (
                not line.tax_classification_id.active or not line.tax_classification_id.invoicing_allowed)
                for line in self.line_ids):
            raise UserError(self.env._('Select an active tax classification code allowed for invoicing.'))

    def _lock_sources(self):
        moves = self.line_ids.move_id
        moves._qing_lock_fapiao_moves()
        moves._qing_touch_allocation_revision()
        return moves

    def _open(self):
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'name': self.env._('Tax Invoice Request'), 'view_mode': 'form', 'target': 'current'}

    def _get_registered_seller_name(self):
        """Use the approved seller name for manual invoice registration."""
        self.ensure_one()
        return self.seller_name

    amount_tax = fields.Monetary(string='Requested Tax', compute='_compute_amounts')
    amount_total = fields.Monetary(string='Requested Total', compute='_compute_amounts')
    amount_untaxed = fields.Monetary(string='Requested Amount', compute='_compute_amounts')
    approved_at = fields.Datetime(string='Approved At', readonly=True)
    approved_by = fields.Many2one('res.users', string='Approved By', readonly=True)
    buyer_name = fields.Char(string='Buyer Name', required=True)
    buyer_partner_id = fields.Many2one('res.partner', string='Buyer', compute='_compute_party_partners', store=True)
    buyer_tax_id = fields.Char(string='Buyer Tax ID')
    cancel_reason = fields.Text(string='Cancellation / Rejection Reason')
    cancelled_at = fields.Datetime(string='Cancelled At', readonly=True)
    cancelled_by = fields.Many2one('res.users', string='Cancelled By', readonly=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True, index=True)
    completed_at = fields.Datetime(string='Recorded At', readonly=True)
    completed_by = fields.Many2one('res.users', string='Recorded By', readonly=True)
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id, required=True)
    fapiao_id = fields.Many2one('qing.fapiao', string='Recorded Tax Invoice', readonly=True, copy=False, ondelete='restrict', check_company=True)
    fapiao_state = fields.Selection(related='fapiao_id.state', string='Tax Invoice Registration State')
    kind = fields.Selection(KINDS, string='Invoice Kind', default='normal', required=True)
    line_ids = fields.One2many('qing.fapiao.request.line', 'request_id', string='Requested Lines', copy=False)
    name = fields.Char(string='Request Number', default='/', readonly=True, copy=False)
    note = fields.Text(string='Notes')
    partner_id = fields.Many2one('res.partner', string='Customer', required=True, check_company=True)
    reset_at = fields.Datetime(string='Last Reset At', readonly=True, copy=False)
    reset_by = fields.Many2one('res.users', string='Last Reset By', readonly=True, copy=False)
    result_amount_tax = fields.Monetary(string='Issued Tax')
    result_amount_untaxed = fields.Monetary(string='Issued Amount')
    result_checked = fields.Boolean(string='I verified the issued invoice against this approved request')
    result_code = fields.Char(string='Invoice Code')
    result_date = fields.Date(string='Invoice Date')
    result_file = fields.Binary(string='Issued Invoice File', attachment=False)
    result_filename = fields.Char(string='File Name')
    result_number = fields.Char(string='Invoice Number')
    revision = fields.Integer(default=0, readonly=True)
    seller_name = fields.Char(string='Seller Name', required=True)
    seller_partner_id = fields.Many2one('res.partner', string='Seller', compute='_compute_party_partners', store=True)
    seller_tax_id = fields.Char(string='Seller Tax ID')
    state = fields.Selection(STATES, string='Status', default='draft', required=True, readonly=True, copy=False, index=True)
    submitted_at = fields.Datetime(string='Submitted At', readonly=True)
    submitted_by = fields.Many2one('res.users', string='Submitted By', readonly=True)

    _unique_fapiao = models.Constraint('UNIQUE(fapiao_id)', 'A tax invoice can complete only one request.')

    @api.model_create_multi
    def create(self, vals_list):
        forbidden = AUDIT_FIELDS | RESULT_FIELDS | {'amount_untaxed', 'amount_tax', 'amount_total', 'fapiao_state'}
        if any('default_' + key in self.env.context for key in forbidden):
            raise AccessError(self.env._('Request status, result and audit fields are managed by actions.'))
        if any(forbidden & vals.keys() for vals in vals_list):
            raise AccessError(self.env._('Request status, result and audit fields are managed by actions.'))
        prepared = []
        for vals in vals_list:
            vals = dict(vals)
            vals.update(self._party_snapshot_defaults(vals))
            vals.update(state='draft', revision=0)
            prepared.append(vals)
        records = super().create(prepared)
        for record in records:
            super(FapiaoRequest, record).write({'name': 'FPREQ-%06d' % record.id})
        return records

    def write(self, vals):
        if (AUDIT_FIELDS | {'amount_untaxed', 'amount_tax', 'amount_total', 'fapiao_state'}) & vals.keys():
            raise AccessError(self.env._('Request status, result and audit fields are managed by actions.'))
        self._lock()
        for record in self:
            permitted = {'cancel_reason'} | (RESULT_FIELDS if record.state == 'approved' else set())
            if record.state in ('done', 'cancelled') or (record.state != 'draft' and set(vals) - permitted):
                raise UserError(self.env._('This request is locked. Reset it to draft before editing if eligible.'))
            if record.state != 'approved' and RESULT_FIELDS & vals.keys():
                raise UserError(self.env._('Record issued invoice details only after approval.'))
        vals = dict(vals)
        if RESULT_FIELDS.intersection(vals) - {'result_checked'} and 'result_checked' not in vals:
            vals['result_checked'] = False
        self._touch()
        return super().write(vals)

    def unlink(self):
        self._lock()
        if any(r.state not in ('draft', 'cancelled') for r in self):
            raise UserError(self.env._('Only draft or cancelled requests can be deleted.'))
        # Request lines cascade with their parent. Direct line deletion remains
        # draft-only, so cancelled requests cannot be edited piecemeal.
        return super().unlink()

    def copy(self, default=None):
        raise UserError(self.env._('Create a new request from the source bills to recheck available amounts.'))

    def action_submit(self):
        with self.env.cr.savepoint():
            self._check_scope()
            self._lock()
            if self.state != 'draft':
                raise UserError(self.env._('Only draft requests can be submitted.'))
            moves = self._lock_sources()
            self._validate_submission()
            self._touch()
            super().write({'state': 'submitted', 'submitted_at': fields.Datetime.now(), 'submitted_by': self.env.uid})
            moves._qing_validate_request_capacity()
        return True

    def action_approve(self):
        with self.env.cr.savepoint():
            self._check_scope()
            self._lock()
            if self.state != 'submitted':
                raise UserError(self.env._('Only submitted requests can be approved.'))
            moves = self._lock_sources()
            self._validate_submission()
            moves._qing_validate_request_capacity()
            self._touch()
            super().write({'state': 'approved', 'approved_at': fields.Datetime.now(), 'approved_by': self.env.uid})
        return True

    def action_reset_draft(self):
        with self.env.cr.savepoint():
            self._check_scope()
            self._lock()
            if self.state not in ('submitted', 'approved', 'cancelled') or self.fapiao_id:
                raise UserError(self.env._('Only unrecorded submitted, approved or cancelled requests can be reset to draft.'))
            if any(self[key] for key in RESULT_FIELDS):
                raise UserError(self.env._('This request contains invoice results. Verify and clear them before resetting to draft.'))
            self._lock_sources()
            self._touch()
            super().write({
                'state': 'draft', 'submitted_at': False, 'submitted_by': False,
                'approved_at': False, 'approved_by': False, 'cancelled_at': False,
                'cancelled_by': False, 'cancel_reason': False,
                'reset_at': fields.Datetime.now(), 'reset_by': self.env.uid,
            })
        return True

    def action_cancel(self):
        with self.env.cr.savepoint():
            self._check_scope()
            self._lock()
            if self.state not in ('draft', 'submitted', 'approved') or not (self.cancel_reason or '').strip():
                raise UserError(self.env._('Only unrecorded requests can be cancelled; enter a reason.'))
            self._lock_sources()
            self._touch()
            super().write({'state': 'cancelled', 'cancelled_at': fields.Datetime.now(), 'cancelled_by': self.env.uid})
        return True

    def action_record_invoice(self):
        """Atomically replace the reservation with registered invoice allocations."""
        with self.env.cr.savepoint():
            self._check_scope()
            self._lock()
            if self.state == 'done':
                return self.action_open_invoice()
            if self.state != 'approved' or not self.result_checked:
                raise UserError(self.env._('Approve the request and verify the issued invoice before recording it.'))
            if not self.result_number or not self.result_date or not self.result_file or not self.result_filename:
                raise UserError(self.env._('Enter the invoice number, date and original file.'))
            self.env['qing.fapiao']._validate_input_amounts({'amount_untaxed': self.result_amount_untaxed, 'amount_tax': self.result_amount_tax})
            if (self.currency_id.compare_amounts(self.result_amount_untaxed, self.amount_untaxed)
                    or self.currency_id.compare_amounts(self.result_amount_tax, self.amount_tax)):
                raise UserError(self.env._('The issued amount and tax must match the approved request. Correct the result or cancel and recreate the request.'))
            moves = self._lock_sources()
            self._validate_submission()
            moves._qing_validate_request_capacity()
            try:
                with self.env.cr.savepoint():
                    ticket = self.env['qing.fapiao'].create({
                        'company_id': self.company_id.id, 'currency_id': self.currency_id.id, 'direction': 'out', 'color': 'blue',
                        'partner_id': self.partner_id.id, 'kind': self.kind, 'buyer_name': self.buyer_name,
                        'buyer_tax_id': self.buyer_tax_id, 'seller_name': self._get_registered_seller_name(),
                        'seller_tax_id': self.seller_tax_id,
                        'number': self.result_number, 'code': self.result_code, 'invoice_date': self.result_date,
                        'source_file': self.result_file, 'source_filename': self.result_filename,
                        'amount_untaxed': self.amount_untaxed, 'amount_tax': self.amount_tax,
                        'move_ids': [Command.set(moves.ids)], 'note': self.name})
            except IntegrityError as error:
                if error.diag.constraint_name == 'qing_fapiao_active_identity':
                    raise UserError(self.env._('This invoice number is already recorded for this company and direction. Review the existing tax invoice.')) from error
                raise
            ticket.action_register()
            allocations = self.env['qing.fapiao.allocation']
            values = []
            for move, lines in self.line_ids.grouped('move_id').items():
                values.append({'fapiao_id': ticket.id, 'move_id': move.id,
                    'amount_untaxed': sum(lines.mapped('amount_untaxed')), 'amount_tax': sum(lines.mapped('amount_tax'))})
            allocations = allocations.create(values)
            self._touch()
            super().write({'state': 'done', 'fapiao_id': ticket.id, 'completed_at': fields.Datetime.now(), 'completed_by': self.env.uid})
            allocations.action_confirm()
        return self.action_open_invoice()

    def action_open_invoice(self):
        self.ensure_one()
        self.check_access('read')
        self.fapiao_id.check_access('read')
        if not self.fapiao_id:
            raise UserError(self.env._('This request has no recorded tax invoice.'))
        return {'type': 'ir.actions.act_window', 'res_model': 'qing.fapiao', 'res_id': self.fapiao_id.id,
                'view_mode': 'form', 'target': 'current'}
