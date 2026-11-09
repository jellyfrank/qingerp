"""Persistent, resumable planning runs for the Qing AI assistant."""

from datetime import timedelta

from odoo import api, fields, models

from .qing_ai_operation import GC_BATCH_SIZE


RUN_STATES = [
    ('pending', 'Pending'),
    ('running', 'Running'),
    ('done', 'Done'),
    ('failed', 'Failed'),
]
# A browser normally finishes a run within minutes. Runs abandoned for a day keep
# prompts, business data and tool results that nobody will resume.
ABANDONED_RUN_GRACE = timedelta(days=1)
RUN_MODES = [
    ('triage', 'Answer First'),
    ('plan', 'Plan Actions'),
]


class QingAiRun(models.Model):
    _name = 'qing.ai.run'
    _description = 'Qing AI Run'
    _order = 'id desc'

    @api.autovacuum
    def _gc_abandoned_runs(self):
        """Delete planner runs whose deadline passed long ago without being finished."""
        domain = [('deadline', '<', fields.Datetime.now() - ABANDONED_RUN_GRACE)]
        runs = self.sudo().search(domain, limit=GC_BATCH_SIZE + 1)
        batch = runs[:GC_BATCH_SIZE]
        batch.unlink()
        return len(batch), len(runs) > GC_BATCH_SIZE

    answer_prompt = fields.Text(readonly=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True, index=True)
    conversation_id = fields.Many2one(
        'qing.ai.conversation', required=True, readonly=True, index=True, ondelete='cascade',
    )
    deadline = fields.Datetime(required=True, readonly=True)
    error = fields.Text(readonly=True)
    mode = fields.Selection(RUN_MODES, default='plan', required=True, readonly=True)
    model = fields.Char(readonly=True)
    owner_id = fields.Many2one('res.users', required=True, readonly=True, index=True)
    planner_prompt = fields.Text(required=True, readonly=True)
    planner_system = fields.Text(readonly=True)
    provider = fields.Char(readonly=True)
    request_text = fields.Text(required=True, readonly=True)
    round_count = fields.Integer(default=0, required=True, readonly=True)
    source_refs = fields.Json(readonly=True)
    state = fields.Selection(RUN_STATES, default='pending', required=True, readonly=True, index=True)
    tool_call_count = fields.Integer(default=0, required=True, readonly=True)
    trace = fields.Json(default=list, readonly=True)
    usage = fields.Json(default=dict, readonly=True)
