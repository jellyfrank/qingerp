from odoo import api, Command, fields, models
from odoo.exceptions import UserError


class RepairPurchaseWizard(models.TransientModel):
    _name = 'qing.repair.purchase.wizard'
    _description = 'Repair Purchase Wizard'
    _check_company_auto = True

    def _purchase_action(self):
        self.purchase_order_id.check_access('read')
        return {'type': 'ir.actions.act_window', 'res_model': 'purchase.order',
            'res_id': self.purchase_order_id.id, 'view_mode': 'form', 'context': {'create': False}}

    @api.onchange('supplier_id', 'date_order')
    def _onchange_vendor(self):
        self.line_ids._onchange_price()

    @api.depends('supplier_id', 'company_id')
    def _compute_currency_id(self):
        for wizard in self:
            wizard.currency_id = wizard.supplier_id.with_company(wizard.company_id).property_purchase_currency_id or wizard.company_id.currency_id

    company_id = fields.Many2one(related='repair_id.company_id', store=True)
    currency_id = fields.Many2one('res.currency', compute='_compute_currency_id')
    date_order = fields.Datetime(required=True, default=fields.Datetime.now)
    date_planned = fields.Datetime(string='Expected Arrival', required=True, default=fields.Datetime.now)
    line_ids = fields.One2many('qing.repair.purchase.wizard.line', 'wizard_id', string='Purchase Lines')
    purchase_order_id = fields.Many2one('purchase.order', readonly=True, copy=False)
    repair_id = fields.Many2one('repair.order', required=True, readonly=True, check_company=True)
    supplier_id = fields.Many2one('res.partner', string='Vendor', check_company=True)

    def action_create_purchase(self):
        self.ensure_one()
        self.check_access('write')
        repair = self.repair_id
        repair._qing_check_purchase_access()
        self.lock_for_update()
        if self.purchase_order_id:
            return self._purchase_action()
        repair.lock_for_update()
        # A row update makes stale repeatable-read snapshots retry after a competing allocation.
        repair.qing_purchase_revision += 1
        repair.flush_recordset(['qing_purchase_revision'])
        selected = self.line_ids.filtered('selected')
        if not selected or not self.supplier_id.active or not self.date_order or not self.date_planned:
            raise UserError(self.env._('Select repair lines, an active vendor and purchase dates.'))
        self._check_company()
        arranged = repair._qing_arranged_purchase()
        seen = set()
        for line in selected:
            line._validate_purchase(arranged, seen)
        Purchase = self.env['purchase.order'].with_company(self.company_id)
        currency = self.supplier_id.with_company(self.company_id).property_purchase_currency_id or self.company_id.currency_id
        order = Purchase.create({'partner_id': self.supplier_id.id, 'company_id': self.company_id.id,
            'currency_id': currency.id, 'date_order': self.date_order, 'origin': repair.name,
            'qing_repair_id': repair.id, 'order_line': [Command.create(line._prepare_line()) for line in selected]})
        self.purchase_order_id = order
        return self._purchase_action()
