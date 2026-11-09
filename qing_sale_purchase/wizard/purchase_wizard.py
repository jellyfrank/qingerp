from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class PurchaseWizard(models.TransientModel):
    _name = 'qing.sale.purchase.wizard'
    _description = 'Sales Purchase Wizard'
    _check_company_auto = True

    def _prepare_order_values(self, supplier, currency):
        self.ensure_one()
        return {
            'partner_id': supplier.id, 'company_id': self.company_id.id,
            'currency_id': currency.id, 'user_id': self.user_id.id,
            'origin': self.sale_order_id.name, 'date_order': self.date_order,
        }

    def _purchase_action(self):
        self.purchase_order_ids.check_access('read')
        return self.purchase_order_ids._qing_view_action('purchase.purchase_rfq')

    @api.onchange('supplier_id')
    def _onchange_supplier(self):
        for wizard in self:
            wizard.line_ids.supplier_id = wizard.supplier_id
            wizard.line_ids._onchange_price_inputs()

    @api.onchange('currency_id', 'date_order')
    def _onchange_pricing(self):
        self.line_ids._onchange_price_inputs()

    company_id = fields.Many2one(related='sale_order_id.company_id', store=True)
    currency_id = fields.Many2one('res.currency', string='Purchase Currency',
                                  help='Leave empty to use each vendor currency, or the company currency.')
    date_order = fields.Datetime(string='Order Date', required=True, default=fields.Datetime.now)
    date_planned = fields.Datetime(string='Expected Arrival', required=True, default=fields.Datetime.now)
    line_ids = fields.One2many('qing.sale.purchase.wizard.line', 'wizard_id', string='Purchase Lines')
    purchase_order_ids = fields.Many2many('purchase.order', string='Created RFQs', readonly=True)
    sale_order_id = fields.Many2one('sale.order', required=True, readonly=True, check_company=True)
    supplier_id = fields.Many2one('res.partner', string='Default Vendor', check_company=True)
    user_id = fields.Many2one('res.users', string='Buyer', required=True, default=lambda self: self.env.user)

    def action_create_purchase(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('purchase.group_purchase_user'):
            raise AccessError(_('Purchase access is required to create RFQs.'))
        self.sale_order_id.check_access('read')
        if self.company_id not in self.env.companies or self.sale_order_id.state != 'sale':
            raise UserError(_('Select a confirmed sales order in an allowed company.'))
        self.lock_for_update()
        if self.purchase_order_ids:
            return self._purchase_action()
        selected = self.line_ids.filtered('selected')
        if not selected:
            raise UserError(_('Select at least one sales line.'))
        if not self.date_order or not self.date_planned:
            raise UserError(_('Set the order date and expected arrival date.'))
        sources = selected.sale_line_id
        sources._qing_lock_purchase()
        sources._qing_check_manual_purchase()
        if len(sources) != len(selected) or sources.order_id != self.sale_order_id:
            raise UserError(_('Each source line must belong to this sales order and appear only once.'))
        if self.company_id not in self.user_id.company_ids:
            raise UserError(_('The buyer must have access to the sales company.'))
        if not self.user_id.active or not self.user_id.has_group('purchase.group_purchase_user'):
            raise UserError(_('Choose an active buyer with purchase access.'))
        for line in selected:
            line._validate_purchase()
        PurchaseOrder = self.env['purchase.order'].with_company(self.company_id)
        orders = PurchaseOrder
        groups = {}
        for line in selected:
            currency = line._purchase_currency()
            groups.setdefault((line.supplier_id.id, currency.id), selected.browse())
            groups[(line.supplier_id.id, currency.id)] |= line
        for (supplier_id, currency_id), lines in groups.items():
            supplier = self.env['res.partner'].browse(supplier_id)
            currency = self.env['res.currency'].browse(currency_id)
            order = PurchaseOrder.create(self._prepare_order_values(supplier, currency))
            self.env['purchase.order.line'].with_company(self.company_id).create([
                line._prepare_line_values(order) for line in lines
            ])
            orders |= order
        self.purchase_order_ids = orders
        return self._purchase_action()
