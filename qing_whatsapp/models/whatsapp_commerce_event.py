from odoo import fields, models

COMMERCE_STATES = [('pending', 'Pending integration'), ('handled', 'Handled')]


class WhatsappCommerceEvent(models.Model):
    """Durable extension boundary; an order webhook is not a confirmed Odoo order."""

    _name = 'qing.whatsapp.commerce.event'

    _description = 'WhatsApp Commerce Event'

    _order = 'id desc'

    _source_unique = models.Constraint('unique(event_id)', 'This commerce event already exists.')

    def _dispatch_verified_event(self):
        self.ensure_one()
        # Implement these private hooks in qing_whatsapp_shop. No database-supplied method names.
        if self.kind == 'order':
            handled = self._handle_order()
        elif self.kind == 'interactive' and self.payload.get('type') == 'nfm_reply':
            handled = self._handle_flow_result()
        else:
            handled = self._handle_interaction()
        if handled:
            self.state = 'handled'

    def _handle_order(self):
        """Extension must validate catalog/SKU, identity, currency, prices and duplicate orders."""
        return False

    def _handle_flow_result(self):
        """Extension must verify a short-lived, account/customer-bound flow token."""
        return False

    def _handle_interaction(self):
        """Extension handles allow-listed button/list IDs; never execute arbitrary payload actions."""
        return False

    account_id = fields.Many2one(related='conversation_id.account_id', store=True)

    company_id = fields.Many2one(related='conversation_id.company_id', store=True, index=True)

    conversation_id = fields.Many2one('qing.whatsapp.conversation', required=True, ondelete='restrict')

    event_id = fields.Many2one('qing.whatsapp.event', required=True, ondelete='restrict')

    kind = fields.Char(required=True)

    message_id = fields.Many2one('qing.whatsapp.message', ondelete='restrict')

    payload = fields.Json()

    state = fields.Selection(COMMERCE_STATES, default='pending', required=True)
