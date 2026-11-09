from odoo import _, api, models
from odoo.exceptions import AccessError, UserError


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def _qing_check_purchase_buyer(self, buyer_id):
        if self.env.su or not self.env.user._qing_purchase_restricted():
            return
        if buyer_id != self.env.uid:
            raise AccessError(_('Only a purchase administrator may assign purchases to another buyer.'))

    def _fetch_duplicate_orders(self):
        duplicates = super()._fetch_duplicate_orders()
        if self.env.su or not self.env.user._qing_purchase_restricted():
            return duplicates
        ids = {duplicate for values in duplicates.values() for duplicate in values}
        readable = set(self.browse(ids)._filtered_access('read').ids)
        return {order_id: set(values) & readable for order_id, values in duplicates.items()}

    @api.depends_context('uid')
    def _compute_duplicated_order_ids(self):
        super()._compute_duplicated_order_ids()

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and self.env.user._qing_purchase_restricted():
            default_buyer = self.default_get(['user_id']).get('user_id')
            for vals in vals_list:
                self._qing_check_purchase_buyer(vals.get('user_id', default_buyer))
        return super().create(vals_list)

    def write(self, vals):
        if 'user_id' in vals:
            self._qing_check_purchase_buyer(vals['user_id'])
        return super().write(vals)

    def action_qing_create_bill(self):
        """Create a draft bill through the native purchase billing workflow."""
        self.ensure_one()
        self.check_access('write')
        if self.state != 'purchase':
            raise UserError(_('Confirm the purchase order before creating a bill.'))
        if self.invoice_status != 'to invoice':
            raise UserError(_(
                'There is no quantity to bill. Check the received quantities '
                'and existing vendor bills, including drafts.'
            ))
        return self.action_create_invoice()
