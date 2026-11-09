"""Durable, user-owned previews for AI-proposed sales writes."""

from odoo import api, fields, models

from odoo.addons.qing_ai.models.qing_ai_operation import GC_BATCH_SIZE, STALE_PREVIEW_GRACE


OPERATION_STATES = [
    ('pending', 'Pending'),
    ('done', 'Done'),
    ('expired', 'Expired'),
]


class QingAiSaleOperation(models.Model):
    _name = 'qing.ai.sale.operation'
    _description = 'Qing AI Sales Operation'
    _order = 'id desc'

    @api.autovacuum
    def _gc_expired_previews(self):
        """Delete never-confirmed sales previews; confirmed operations remain as results."""
        domain = [
            ('state', 'in', ('pending', 'expired')),
            ('expires_at', '<', fields.Datetime.now() - STALE_PREVIEW_GRACE),
        ]
        operations = self.sudo().search(domain, limit=GC_BATCH_SIZE + 1)
        batch = operations[:GC_BATCH_SIZE]
        batch.unlink()
        return len(batch), len(operations) > GC_BATCH_SIZE

    arguments = fields.Json(required=True, readonly=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True)
    conversation_id = fields.Many2one(
        'qing.ai.conversation', required=True, index=True, ondelete='cascade', readonly=True,
    )
    expires_at = fields.Datetime(required=True, readonly=True)
    fingerprint = fields.Char(required=True, readonly=True)
    owner_id = fields.Many2one('res.users', required=True, readonly=True)
    preview = fields.Text(required=True, readonly=True)
    result_id = fields.Integer(readonly=True)
    result_model = fields.Char(readonly=True)
    state = fields.Selection(OPERATION_STATES, default='pending', required=True, readonly=True)
    tool_key = fields.Selection(
        [('create_customer', 'Create Customer'),
         ('update_customer', 'Update Customer'),
         ('create_followup', 'Create Follow-up Activity'),
         ('create_quote_draft', 'Create Quotation Draft')],
        required=True, readonly=True,
    )
