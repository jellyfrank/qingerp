from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .res_company import PRICING_MODES

INVOICE_STATES = [
    ('none', 'No Invoice'), ('draft', 'Draft'), ('posted', 'Posted'),
    ('cancel', 'Cancelled'), ('mixed', 'Draft and Posted'),
]
PAYMENT_STATES = [
    ('none', 'Not Applicable'), ('not_paid', 'Not Paid'), ('in_payment', 'In Payment'),
    ('paid', 'Paid'), ('partial', 'Partially Paid'), ('reversed', 'Reversed'),
    ('blocked', 'Blocked'), ('mixed', 'Mixed Payment States'),
]


class RepairOrder(models.Model):
    _inherit = 'repair.order'

    def _qing_lock(self):
        self.ensure_one()
        self.check_access('write')
        self.env.cr.execute('SELECT id FROM repair_order WHERE id = %s FOR UPDATE', (self.id,))
        self.invalidate_recordset()

    def _qing_check_price_confirmation(self):
        """Portal extensions enforce signed-price immutability here."""

    def _qing_check_mode_change(self):
        if not self.env.su and not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._('Only administrators can change a repair pricing mode.'))
        for repair in self:
            repair._qing_lock()
            if repair.state != 'draft' or repair.sale_order_id or repair.invoice_ids:
                raise UserError(self.env._('Only draft repairs without a quotation or invoice can change pricing mode.'))
            repair._qing_check_price_confirmation()

    def _qing_direct_lines(self):
        self.ensure_one()
        return [*self.move_ids.filtered(lambda move: move.repair_line_type == 'add' and move.state != 'cancel'), *self.repair_service_line_ids]

    def _qing_sale_lines(self):
        self.ensure_one()
        lines = self.move_ids.sale_line_id | self.repair_service_line_ids.sale_line_id
        if self.sale_order_line_id.product_id.type == 'service':
            lines |= self.sale_order_line_id
        return lines.filtered(lambda line: not line.display_type and line.order_id.state != 'cancel')

    def _qing_base_lines(self):
        self.ensure_one()
        if self.qing_pricing_mode == 'direct':
            return [line._qing_base_line() for line in self._qing_direct_lines()]
        return [line._prepare_base_line_for_taxes_computation() for line in self._qing_sale_lines()]

    def _qing_customer_lines(self):
        self.ensure_one()
        if self.qing_pricing_mode == 'sale':
            return [{
                'description': line.name, 'quantity': line.product_uom_qty,
                'unit_price': line.price_unit, 'discount': line.discount, 'subtotal': line.price_subtotal,
            } for line in self._qing_sale_lines()]
        return [{
            'description': line._qing_description(), 'quantity': line._qing_quantity(),
            'unit_price': 0.0 if self.under_warranty else line.qing_price_unit,
            'discount': line.qing_discount, 'subtotal': line.qing_price_subtotal,
        } for line in self._qing_direct_lines()]

    def _qing_billing_invoices(self):
        """Only repair charges, not other lines on the linked sales order."""
        self.ensure_one()
        if self.qing_pricing_mode == 'sale':
            invoices = self._qing_sale_lines().invoice_lines.move_id
        else:
            invoices = self.invoice_ids.filtered(lambda invoice: invoice.repair_order_id == self)
        invoices |= invoices.reversal_move_ids
        return invoices.filtered(lambda invoice: invoice.company_id == self.company_id and invoice.move_type in ('out_invoice', 'out_refund'))

    @api.depends(
        'qing_pricing_mode', 'company_id', 'invoice_ids.state', 'invoice_ids.payment_state',
        'invoice_ids.reversal_move_ids.state', 'invoice_ids.reversal_move_ids.payment_state',
        'move_ids.sale_line_id.invoice_lines.move_id.state', 'move_ids.sale_line_id.invoice_lines.move_id.payment_state',
        'repair_service_line_ids.sale_line_id.invoice_lines.move_id.state',
        'repair_service_line_ids.sale_line_id.invoice_lines.move_id.payment_state',
        'sale_order_line_id.invoice_lines.move_id.state', 'sale_order_line_id.invoice_lines.move_id.payment_state',
        'sale_order_id.state',
    )
    def _compute_qing_billing_states(self):
        # Like native invoice_count, expose only the linked status to repair users,
        # not account.move records or extra financial permissions.
        for repair in self:
            invoices = repair._qing_billing_invoices()
            active = invoices.filtered(lambda invoice: invoice.state != 'cancel')
            states = set(active.mapped('state'))
            repair.qing_invoice_state = (
                'mixed' if len(states) > 1 else next(iter(states)) if states else 'cancel' if invoices else 'none'
            )
            posted = active.filtered(lambda invoice: invoice.state == 'posted')
            payments = set(posted.mapped('payment_state'))
            if len(payments) <= 1:
                repair.qing_payment_state = next(iter(payments)) if payments else 'none'
            elif payments <= {'not_paid', 'in_payment', 'paid', 'partial'}:
                repair.qing_payment_state = 'partial'
            else:
                repair.qing_payment_state = 'mixed'

    def _qing_has_uninvoiced_charges(self):
        self.ensure_one()
        return any(not line.invoice_line_ids and line._qing_quantity() > 0 for line in self._qing_direct_lines())

    @api.depends('sale_order_id.currency_id', 'company_id.currency_id')
    def _compute_qing_currency_id(self):
        for repair in self:
            repair.qing_currency_id = repair.sale_order_id.currency_id or repair.company_id.currency_id

    @api.depends('partner_id', 'company_id')
    def _compute_qing_fiscal_position_id(self):
        for repair in self:
            repair.qing_fiscal_position_id = self.env['account.fiscal.position'].with_company(repair.company_id)._get_fiscal_position(repair.partner_id)

    @api.depends(
        'qing_pricing_mode', 'qing_currency_id', 'state', 'under_warranty', 'company_id.tax_calculation_rounding_method',
        'move_ids.qing_price_unit', 'move_ids.qing_discount', 'move_ids.qing_tax_ids', 'move_ids.product_uom_qty',
        'move_ids.quantity', 'move_ids.state', 'move_ids.repair_line_type',
        'repair_service_line_ids.qing_price_unit', 'repair_service_line_ids.qing_discount',
        'repair_service_line_ids.qing_tax_ids', 'repair_service_line_ids.quantity',
        'move_ids.sale_line_id.price_unit', 'move_ids.sale_line_id.product_uom_qty', 'move_ids.sale_line_id.tax_ids', 'move_ids.sale_line_id.discount',
        'repair_service_line_ids.sale_line_id.price_unit', 'repair_service_line_ids.sale_line_id.product_uom_qty',
        'repair_service_line_ids.sale_line_id.tax_ids', 'repair_service_line_ids.sale_line_id.discount',
        'sale_order_line_id.price_unit', 'sale_order_line_id.product_uom_qty', 'sale_order_line_id.tax_ids', 'sale_order_line_id.discount',
    )
    def _compute_qing_amounts(self):
        Tax = self.env['account.tax']
        for repair in self:
            base_lines = repair._qing_base_lines()
            Tax._add_tax_details_in_base_lines(base_lines, repair.company_id)
            Tax._round_base_lines_tax_details(base_lines, repair.company_id)
            totals = Tax._get_tax_totals_summary(base_lines, repair.qing_currency_id, repair.company_id)
            repair.qing_amount_untaxed = totals['base_amount_currency']
            repair.qing_amount_tax = totals['tax_amount_currency']
            repair.qing_amount_total = totals['total_amount_currency']

    @api.depends('state', 'sale_order_id', 'invoice_ids', 'qing_pricing_mode')
    def _compute_qing_can_change_mode(self):
        for repair in self:
            repair.qing_can_change_mode = repair.state == 'draft' and not repair.sale_order_id and not repair.invoice_ids

    @api.constrains('qing_pricing_mode', 'sale_order_id', 'invoice_ids')
    def _check_qing_billing_path(self):
        for repair in self:
            if repair.qing_pricing_mode == 'direct' and repair.sale_order_id:
                raise ValidationError(self.env._('Direct-priced repairs cannot be linked to a quotation.'))
            # Native stored invoice_ids can set repair_order_id on SO invoices.
            # Keep that relationship valid, but reject genuinely direct invoices.
            direct = repair.invoice_ids.filtered(lambda invoice: invoice.repair_order_id == repair
                and (not repair.sale_order_id or repair.sale_order_id.id not in invoice.invoice_line_ids.sale_line_ids.order_id.ids))
            if repair.qing_pricing_mode == 'sale' and direct:
                raise ValidationError(self.env._('Quotation-priced repairs must be invoiced through their sales order.'))

    qing_amount_tax = fields.Monetary(string='Taxes', currency_field='qing_currency_id', compute='_compute_qing_amounts')
    qing_amount_total = fields.Monetary(string='Total', currency_field='qing_currency_id', compute='_compute_qing_amounts')
    qing_amount_untaxed = fields.Monetary(string='Untaxed Amount', currency_field='qing_currency_id', compute='_compute_qing_amounts')
    qing_can_change_mode = fields.Boolean(compute='_compute_qing_can_change_mode')
    qing_currency_id = fields.Many2one('res.currency', compute='_compute_qing_currency_id')
    qing_device_model = fields.Char(string='Device Model', tracking=True)
    qing_fault_description = fields.Text(string='Fault Description', tracking=True)
    qing_fiscal_position_id = fields.Many2one('account.fiscal.position', compute='_compute_qing_fiscal_position_id')
    qing_invoice_state = fields.Selection(INVOICE_STATES, string='Invoice Status', compute='_compute_qing_billing_states', compute_sudo=True)
    qing_payment_state = fields.Selection(PAYMENT_STATES, string='Payment Status', compute='_compute_qing_billing_states', compute_sudo=True)
    qing_photo_after = fields.Image(string='After Repair', attachment=True, max_width=1920, max_height=1920, copy=False)
    qing_photo_after_ids = fields.One2many('qing.repair.photo', 'repair_id', domain=[('kind', '=', 'after')], string='After Repair Photos', copy=False)
    qing_photo_before = fields.Image(string='Before Repair', attachment=True, max_width=1920, max_height=1920, copy=False)
    qing_photo_before_ids = fields.One2many('qing.repair.photo', 'repair_id', domain=[('kind', '=', 'before')], string='Before Repair Photos', copy=False)
    qing_photo_legacy_after_migrated = fields.Boolean(copy=False, readonly=True)
    qing_photo_legacy_before_migrated = fields.Boolean(copy=False, readonly=True)
    qing_pricing_mode = fields.Selection(PRICING_MODES, string='Pricing Mode', default=lambda self: self.env.company.qing_repair_pricing_mode, required=True, copy=False, tracking=True)
    qing_repair_content = fields.Text(string='Repair Content', tracking=True)
    state = fields.Selection(selection_add=[('done', 'Repaired')])

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if 'qing_pricing_mode' in fields_list and 'default_qing_pricing_mode' not in self.env.context:
            company = self.env['res.company'].browse(values.get('company_id') or self.env.company.id)
            values['qing_pricing_mode'] = 'sale' if values.get('sale_order_id') or self.env.context.get('default_sale_order_id') else company.qing_repair_pricing_mode
        return values

    @api.onchange('company_id', 'sale_order_id')
    def _onchange_qing_pricing_mode(self):
        if not self._origin.id:
            self.qing_pricing_mode = 'sale' if self.sale_order_id else self.company_id.qing_repair_pricing_mode

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            company = self.env['res.company'].browse(vals.get('company_id') or self.env.company.id)
            default_mode = 'sale' if vals.get('sale_order_id') else company.qing_repair_pricing_mode
            if vals.get('qing_pricing_mode', default_mode) != default_mode and not self.env.su and not self.env.user.has_group('base.group_system'):
                raise AccessError(self.env._('Only administrators can override the default repair pricing.'))
            vals.setdefault('qing_pricing_mode', default_mode)
        return super().create(vals_list)

    def copy_data(self, default=None):
        vals_list = super().copy_data(default=default)
        if not default or 'qing_pricing_mode' not in default:
            for vals in vals_list:
                vals.pop('qing_pricing_mode', None)
        return vals_list

    def write(self, vals):
        changed = self.filtered(lambda repair: vals.get('qing_pricing_mode', repair.qing_pricing_mode) != repair.qing_pricing_mode)
        if changed:
            changed._qing_check_mode_change()
        if {'under_warranty', 'partner_id', 'company_id'} & vals.keys():
            for repair in self.filtered(lambda record: record.qing_pricing_mode == 'direct'):
                repair._qing_lock()
                repair._qing_check_price_confirmation()
                if repair.invoice_ids.filtered(lambda invoice: invoice.state == 'posted'):
                    raise UserError(self.env._('The customer, company, and warranty of an invoiced repair cannot be changed.'))
        return super().write(vals)

    def _update_invoice_line_price(self):
        direct = self.filtered(lambda repair: repair.qing_pricing_mode == 'direct')
        for repair in direct:
            for line in repair._qing_direct_lines():
                line._qing_sync_invoice_prices()
        return super(RepairOrder, self - direct)._update_invoice_line_price()

    def _create_sale_order(self):
        for repair in self:
            repair._qing_lock()
            if repair.qing_pricing_mode != 'sale' or repair.invoice_ids:
                raise UserError(self.env._('This repair cannot create a quotation in its current billing path.'))
        return super()._create_sale_order()

    def action_create_invoice(self):
        self.ensure_one()
        self._qing_lock()
        if self.qing_pricing_mode != 'direct' or self.sale_order_id or self.state != 'done' or not self.partner_id:
            raise UserError(self.env._('Direct invoicing requires a completed direct-priced repair with a customer and no quotation.'))
        drafts = self.invoice_ids.filtered(lambda invoice: invoice.state == 'draft')
        if drafts:
            return self.action_view_invoice(drafts[0])
        if not self._qing_has_uninvoiced_charges():
            raise UserError(self.env._('There are no uninvoiced repair charges.'))
        return super(RepairOrder, self.with_company(self.company_id)).action_create_invoice()

    def action_repair_done(self):
        candidates = self.filtered(lambda repair: repair.state == 'confirmed'
            and repair.qing_pricing_mode == 'direct' and repair.company_id.qing_repair_auto_invoice)
        for repair in candidates.sorted('id'):
            repair._qing_lock()
            if not repair.partner_id:
                raise UserError(self.env._('Automatic invoicing requires a customer on the repair.'))
        candidates = candidates.filtered(lambda repair: repair.state == 'confirmed')
        to_invoice = candidates.filtered(lambda repair: not repair.invoice_ids.filtered(lambda invoice: invoice.state == 'draft')
            and repair._qing_has_uninvoiced_charges())
        if to_invoice:
            self.env['account.move'].check_access('create')
            self.env['account.move.line'].check_access('create')
        unfinished = self.filtered(lambda repair: repair.state != 'done')
        result = super(RepairOrder, unfinished).action_repair_done() if unfinished else True
        for repair in to_invoice:
            # Native completion may cancel unused parts; recheck actual charges.
            if repair.state == 'done' and repair._qing_has_uninvoiced_charges():
                repair.action_create_invoice()
        return result
