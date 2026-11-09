"""Persistent responses and usage for the QingERP daily assistant."""

from odoo import fields, models


MESSAGE_ROLES = [
    ('user', 'User'),
    ('assistant', 'Assistant'),
    ('error', 'Error'),
]
MESSAGE_STATES = [
    ('pending', 'Pending'),
    ('done', 'Done'),
    ('failed', 'Failed'),
]


class QingAiMessage(models.Model):
    _name = 'qing.ai.message'
    _description = 'Qing AI Message'
    _order = 'id'

    company_id = fields.Many2one(related='conversation_id.company_id', store=True)
    content = fields.Text(required=True, readonly=True)
    conversation_id = fields.Many2one(
        'qing.ai.conversation', required=True, index=True, ondelete='cascade', readonly=True
    )
    image_attachment_id = fields.Many2one('ir.attachment', readonly=True)
    file_attachment_ids = fields.Many2many(
        'ir.attachment', 'qing_ai_message_file_rel', 'message_id', 'attachment_id', readonly=True
    )
    model = fields.Char(readonly=True)
    process_steps = fields.Json(readonly=True)
    provider = fields.Char(readonly=True)
    role = fields.Selection(MESSAGE_ROLES, required=True, readonly=True)
    source_refs = fields.Json(readonly=True)
    state = fields.Selection(MESSAGE_STATES, required=True, readonly=True)
    usage = fields.Json(readonly=True)
