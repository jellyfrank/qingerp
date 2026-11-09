from odoo import api, fields, models, Command
from odoo.exceptions import UserError, ValidationError


PRICING_FIELDS = {'qing_price_unit', 'qing_discount', 'qing_tax_ids', 'product_id', 'uom_id'}


class RepairPricingMixin(models.AbstractModel):
    _name = 'qing.repair.pricing.mixin'
    _description = 'Repair Pricing Fields'

    def _qing_quantity(self):
        raise NotImplementedError

    def _qing_description(self):
        self.ensure_one()
        return self.product_id.display_name

    def _qing_base_line(self):
        self.ensure_one()
        repair = self.repair_id
        return self.env['account.tax']._prepare_base_line_for_taxes_computation(
            self, product_id=self.product_id, product_uom_id=self.uom_id,
            partner_id=repair.partner_id, company_id=repair.company_id,
            currency_id=repair.qing_currency_id, quantity=self._qing_quantity(),
            price_unit=0.0 if repair.under_warranty else self.qing_price_unit,
            discount=self.qing_discount, tax_ids=self.qing_tax_ids,
        )

    def _qing_invoice_values(self):
        self.ensure_one()
        return {
            'price_unit': 0.0 if self.repair_id.under_warranty else self.qing_price_unit,
            'discount': self.qing_discount,
            'tax_ids': [Command.set(self.qing_tax_ids.ids)],
            'name': self._qing_description(),
        }

    def _qing_check_editable(self):
        for line in self:
            if not line.repair_id or line.repair_id.qing_pricing_mode != 'direct':
                continue
            line.repair_id._qing_lock()
            line.repair_id._qing_check_price_confirmation()
            if line.invoice_line_ids.filtered(lambda item: item.move_id.state == 'posted'):
                raise UserError(self.env._('A posted repair charge cannot be changed. Use a credit note or a new charge.'))

    def _qing_sync_invoice_prices(self):
        for line in self:
            if line.repair_id.qing_pricing_mode == 'direct':
                draft_lines = line.invoice_line_ids.filtered(lambda item: item.move_id.state == 'draft')
                if draft_lines:
                    draft_lines.write(line._qing_invoice_values())

    @api.depends('product_id', 'uom_id', 'repair_id.company_id')
    def _compute_qing_price_unit(self):
        for line in self:
            if not line.repair_id:
                line.qing_price_unit = 0.0
                continue
            product = line.product_id.with_company(line.repair_id.company_id or self.env.company)
            line.qing_price_unit = product.uom_id._compute_price(product.lst_price, line.uom_id) if product and line.uom_id else 0.0

    @api.depends('product_id', 'repair_id.company_id', 'repair_id.qing_fiscal_position_id')
    def _compute_qing_tax_ids(self):
        for line in self:
            if not line.repair_id:
                line.qing_tax_ids = False
                continue
            company = line.repair_id.company_id or self.env.company
            taxes = line.product_id.taxes_id.filtered(lambda tax: tax.company_id == company)
            line.qing_tax_ids = line.repair_id.qing_fiscal_position_id.map_tax(taxes)

    @api.depends('qing_price_unit', 'qing_discount', 'qing_tax_ids', 'repair_id.under_warranty', 'repair_id.qing_currency_id')
    def _compute_qing_price_subtotal(self):
        Tax = self.env['account.tax']
        for line in self:
            if not line.repair_id:
                line.qing_price_subtotal = 0.0
                continue
            base_lines = [line._qing_base_line()]
            Tax._add_tax_details_in_base_lines(base_lines, line.repair_id.company_id)
            Tax._round_base_lines_tax_details(base_lines, line.repair_id.company_id)
            line.qing_price_subtotal = base_lines[0]['tax_details']['total_excluded_currency']

    @api.constrains('qing_discount', 'qing_price_unit', 'qing_tax_ids')
    def _check_qing_prices(self):
        for line in self:
            if not line.repair_id:
                continue
            if line.qing_price_unit < 0 or not 0 <= line.qing_discount <= 100:
                raise ValidationError(self.env._('Repair prices must be nonnegative and discounts must be between 0 and 100.'))
            if any(tax.company_id != line.repair_id.company_id for tax in line.qing_tax_ids):
                raise ValidationError(self.env._('Repair taxes must belong to the repair company.'))

    repair_id = fields.Many2one('repair.order')
    qing_currency_id = fields.Many2one(related='repair_id.qing_currency_id')
    qing_discount = fields.Float(string='Discount (%)', default=0.0)
    qing_price_subtotal = fields.Monetary(string='Subtotal', currency_field='qing_currency_id', compute='_compute_qing_price_subtotal')
    qing_price_unit = fields.Monetary(
        string='Charge Unit Price', currency_field='qing_currency_id',
        compute='_compute_qing_price_unit', store=True, readonly=False, precompute=True,
    )
    qing_tax_ids = fields.Many2many(
        'account.tax', string='Charge Taxes', compute='_compute_qing_tax_ids',
        store=True, readonly=False, precompute=True, check_company=True,
        domain="[('type_tax_use', '=', 'sale'), ('company_id', '=', company_id)]",
    )
