"""Opt-in published knowledge spaces for each AI agent."""

from odoo import fields, models


class QingAiAgent(models.Model):
    _inherit = 'qing.ai.agent'

    knowledge_space_ids = fields.Many2many(
        'qing.knowledge.space', 'qing_ai_agent_knowledge_space_rel',
        'agent_id', 'space_id', string='Knowledge Spaces',
        help='Only published articles readable by the current user are sent to the provider.',
        groups='base.group_system',
    )
