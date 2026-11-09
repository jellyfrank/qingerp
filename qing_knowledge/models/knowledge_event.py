from uuid import uuid4

from odoo import api, fields, models
from odoo.exceptions import AccessError

KINDS = [
    ('published', 'Published'), ('withdrawn', 'Withdrawn'),
    ('archived', 'Archived'), ('trashed', 'Trashed'), ('restored', 'Restored'),
    ('policy', 'Policy changed'),
]


class KnowledgeEvent(models.Model):
    _name = 'qing.knowledge.event'
    _description = 'Knowledge Change Event'
    _order = 'id desc'

    _unique_operation = models.Constraint('UNIQUE(operation_uuid)', 'Operation UUID must be unique.')

    actor_id = fields.Many2one('res.users', required=True, readonly=True)
    article_id = fields.Many2one('qing.knowledge.article', readonly=True)
    draft_id = fields.Many2one('qing.knowledge.draft', readonly=True)
    kind = fields.Selection(KINDS, required=True, readonly=True)
    operation_uuid = fields.Char(required=True, readonly=True)
    policy_epoch = fields.Integer(required=True, readonly=True)
    request_hash = fields.Char(readonly=True)
    revision_id = fields.Many2one('qing.knowledge.revision', readonly=True)
    space_id = fields.Many2one('qing.knowledge.space', readonly=True, index=True)
    uuid = fields.Char(required=True, default=lambda self: str(uuid4()), readonly=True)

    def init(self):
        self.env.cr.execute("""
            UPDATE qing_knowledge_event AS event
            SET space_id = article.space_id
            FROM qing_knowledge_article AS article
            WHERE event.article_id = article.id AND event.space_id IS NULL
        """)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(self.env._('Knowledge events are written by server actions.'))
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(self.env._('Knowledge events are immutable.'))

    def unlink(self):
        raise AccessError(self.env._('Knowledge events are immutable.'))
