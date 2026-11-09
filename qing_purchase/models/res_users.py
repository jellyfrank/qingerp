from odoo import models
from odoo.fields import Domain


VENDOR_MOVE_TYPES = ('in_invoice', 'in_refund', 'in_receipt')
PURCHASE_FULL_ACCESS_GROUPS = (
    'purchase.group_purchase_manager',
    'base.group_system',
    'account.group_account_user',
    'account.group_account_manager',
)


class ResUsers(models.Model):
    _inherit = 'res.users'

    def _qing_purchase_restricted(self):
        self.ensure_one()
        # env.user is sudoed even during ordinary ir.access domain evaluation.
        if not self.has_group('purchase.group_purchase_user'):
            return False
        return not any(self.has_group(group) for group in PURCHASE_FULL_ACCESS_GROUPS)

    def _qing_purchase_order_domain(self, prefix='user_id'):
        if not self._qing_purchase_restricted():
            return Domain.TRUE
        return Domain(prefix, '=', self.id)

    def _qing_purchase_owned_bills(self):
        """Keep mixed-buyer bills private even when other PO lines are hidden."""
        def linked_orders(operator):
            buyer = Domain('user_id', operator, self.id)
            order = Domain('order_id', 'any!', buyer)
            purchase_line = Domain('purchase_line_id', 'any!', order)
            return Domain('line_ids', 'any!', purchase_line)

        # Trusted negative checks must see every source line, not just readable ones.
        linked = Domain('line_ids', 'any!', Domain('purchase_line_id', '!=', False))
        own = linked_orders('=') & ~linked_orders('!=')
        manual = ~linked & Domain('create_uid', '=', self.id)
        return own | manual

    def _qing_purchase_bill_domain(self):
        if not self._qing_purchase_restricted():
            return Domain.TRUE
        vendor = Domain('move_type', 'in', VENDOR_MOVE_TYPES)
        return ~vendor | self._qing_purchase_owned_bills()

    def _qing_purchase_source_domain(self, field):
        if not self._qing_purchase_restricted():
            return Domain.TRUE
        return Domain(field, 'access', 'read')

    def _qing_purchase_matching_domain(self, order_field, bill_field):
        if not self._qing_purchase_restricted():
            return Domain.TRUE
        order = Domain(order_field, '=', False) | Domain(order_field, 'access', 'read')
        bill = Domain(bill_field, '=', False) | Domain(bill_field, 'access', 'read')
        return order & bill
