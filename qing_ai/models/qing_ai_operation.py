"""Immutable, user-owned confirmations for configured ORM tools."""

from datetime import timedelta

from odoo import api, fields, models


OPERATION_STATES = [('pending', 'Pending'), ('done', 'Done')]
# Expired previews stay visible for a day so the user still sees why a late
# confirmation failed; afterwards the stored preview and arguments are removed.
STALE_PREVIEW_GRACE = timedelta(days=1)
GC_BATCH_SIZE = 1000


class QingAiOperation(models.Model):
    _name = 'qing.ai.operation'
    _description = 'Qing AI Operation'
    _order = 'id desc'

    @api.autovacuum
    def _gc_expired_previews(self):
        """Delete never-confirmed previews; confirmed operations remain as results."""
        domain = [
            ('state', '=', 'pending'),
            ('expires_at', '<', fields.Datetime.now() - STALE_PREVIEW_GRACE),
        ]
        operations = self.sudo().search(domain, limit=GC_BATCH_SIZE + 1)
        batch = operations[:GC_BATCH_SIZE]
        batch.unlink()
        return len(batch), len(operations) > GC_BATCH_SIZE

    arguments = fields.Json(required=True, readonly=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True)
    conversation_id = fields.Many2one('qing.ai.conversation', required=True, index=True,
                                      ondelete='cascade', readonly=True)
    expires_at = fields.Datetime(required=True, readonly=True)
    fingerprint = fields.Char(required=True, readonly=True)
    normalized = fields.Json(required=True, readonly=True)
    owner_id = fields.Many2one('res.users', required=True, readonly=True)
    preview = fields.Text(required=True, readonly=True)
    result_id = fields.Integer(readonly=True)
    result_model = fields.Char(readonly=True)
    state = fields.Selection(OPERATION_STATES, default='pending', required=True, readonly=True)
    tool_id = fields.Many2one('qing.ai.tool', required=True, ondelete='restrict', readonly=True)
