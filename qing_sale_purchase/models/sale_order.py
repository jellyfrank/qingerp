from odoo import Command, api, fields, models, _
from odoo.exceptions import AccessError, UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _qing_purchase_allowed_states(self):
        return ('sale',)

    @api.depends('state')
    def _compute_qing_can_create_purchase(self):
        for order in self:
            order.qing_can_create_purchase = order.state in order._qing_purchase_allowed_states()

    def _qing_purchase_change_domain(self):
        return [('sale_line_id.order_id', 'in', self.ids),
                ('qing_manual_purchase', '=', True), ('order_id.state', '!=', 'cancel')]

    def _qing_notify_purchase_change(self):
        PurchaseLine = self.env['purchase.order.line'].sudo()
        domain = self._qing_purchase_change_domain()
        for purchase in PurchaseLine.search(domain).order_id:
            purchase.activity_schedule(
                'mail.mail_activity_data_warning',
                summary=_('Review sales purchase changes'),
                note=_('The source sales order was cancelled or its quantity fell below the arranged purchase quantity. Review this purchase manually.'),
                user_id=purchase.user_id.id or self.env.uid,
            )

    def _action_cancel(self):
        result = super()._action_cancel()
        self._qing_notify_purchase_change()
        return result

    qing_can_create_purchase = fields.Boolean(compute='_compute_qing_can_create_purchase')

    def action_qing_create_purchase(self):
        self.ensure_one()
        self.check_access('read')
        if not self.env.user.has_group('purchase.group_purchase_user'):
            raise AccessError(_('Purchase access is required to create RFQs.'))
        if self.state not in self._qing_purchase_allowed_states() or self.company_id not in self.env.companies:
            raise UserError(_('Select a confirmed sales order in an allowed company.'))
        lines = self.order_line.filtered(
            lambda line: not line.display_type and not line.is_downpayment
            and line.product_id.type == 'consu' and line.product_id.purchase_ok
            and line.product_uom_qty > 0
        )
        # Show eligible goods; automatic-route rows are rejected at submission.
        if not lines:
            raise UserError(_('There are no purchasable goods on this order.'))
        quantities = lines._qing_purchase_quantities()
        values = []
        for line in lines:
            remaining = max(line.product_uom_qty - quantities.get(line.id, 0), 0)
            values.append(Command.create({
                'sale_line_id': line.id, 'quantity': remaining,
                'uom_id': line.product_uom_id.id, 'selected': remaining > 0,
            }))
        wizard = self.env['qing.sale.purchase.wizard'].with_company(self.company_id).create({
            'sale_order_id': self.id, 'line_ids': values,
        })
        return {
            'name': _('Create Purchase'), 'type': 'ir.actions.act_window',
            'res_model': wizard._name, 'res_id': wizard.id,
            'view_mode': 'form', 'target': 'new',
            'context': {'dialog_size': 'large'},
        }
