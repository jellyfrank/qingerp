"""Discuss replies advanced in bounded steps by the chat window, with the cron as fallback."""

import logging
from datetime import timedelta

from odoo import api, fields, models


_logger = logging.getLogger(__name__)

JOB_STATES = [
    ('pending', 'Pending'),
    ('running', 'Running'),
    ('done', 'Done'),
    ('failed', 'Failed'),
]
ACTIVE_STATES = ('pending', 'running')
MAX_JOBS_PER_RUN = 20
# One step is one provider round; planner runs stop by themselves after a few rounds.
MAX_STEPS_PER_JOB = 12
# The chat window advances replies itself; the cron only finishes what it leaves behind.
CRON_FALLBACK_DELAY = timedelta(seconds=15)
JOB_RETENTION = timedelta(days=7)
JOB_GC_BATCH_SIZE = 1000


class QingAiDiscussJob(models.Model):
    _name = 'qing.ai.discuss.job'
    _description = 'Qing AI Discuss Reply Job'
    _order = 'id'

    @api.model
    def _enqueue(self, channel, message, *, general_chat=False, manual=False):
        """
        Record a Discuss message for a reply and schedule the fallback cron.

        :param channel: AI chat channel that received the message
        :param message: user message to answer
        :param general_chat: reply to ordinary chat in a Qing Assistant private conversation
        :param manual: answer from the public manual, even in a chat bound to an AI agent
        :return: queued job
        """
        conversation = channel.sudo().qing_ai_conversation_id
        job = self.sudo().create({
            'channel_id': channel.id,
            'general_chat': general_chat,
            'manual_reply': manual,
            'message_id': message.id,
            'reply_after_id': max(conversation.message_ids.ids, default=0) if conversation else 0,
        })
        channel._qing_ai_set_typing(True)
        # Agent chats are advanced by the chat window; other replies are answered by the cron.
        browser_driven = bool(channel.sudo().qing_ai_agent_id) and not (general_chat or manual)
        self.env.ref('qing_ai.ir_cron_qing_ai_discuss_reply').sudo()._trigger(
            fields.Datetime.now() + CRON_FALLBACK_DELAY if browser_driven else None
        )
        return job

    @api.model
    def _process_pending(self, limit=MAX_JOBS_PER_RUN):
        """Finish unanswered replies oldest first; each step is committed separately in cron."""
        Job = self.sudo()
        domain = [('state', 'in', ACTIVE_STATES)]
        jobs = Job.search(domain, limit=limit)
        in_cron = bool(self.env.context.get('cron_id'))
        for job in jobs:
            job._process_one(commit=in_cron)
        if in_cron and Job.search_count(domain):
            self.env.ref('qing_ai.ir_cron_qing_ai_discuss_reply').sudo()._trigger()
        return len(jobs)

    def _process_one(self, commit=False):
        """Advance one job until it finishes or reaches the step limit of this run."""
        self.ensure_one()
        for _step in range(MAX_STEPS_PER_JOB):
            if not self._lock():
                return
            self._step()
            if commit:
                self.env['ir.cron']._commit_progress(1)
            if self.state not in ACTIVE_STATES:
                return

    def _lock(self):
        """Lock this unfinished job for one step; skip it when another worker holds it."""
        self.ensure_one()
        self.env.cr.execute(
            'SELECT id FROM qing_ai_discuss_job WHERE id = %s AND state IN %s FOR UPDATE SKIP LOCKED',
            [self.id, ACTIVE_STATES],
        )
        locked = bool(self.env.cr.fetchone())
        if locked:
            self.invalidate_recordset()
        return locked

    def _step(self):
        """Run one bounded step: start the reply or advance its planner run by one round."""
        self.ensure_one()
        channel = self.channel_id.sudo()
        try:
            with self.env.cr.savepoint():
                if not channel.exists():
                    self.write({'state': 'failed'})
                elif self.state == 'pending' and (
                        self.general_chat or self.manual_reply or not channel.qing_ai_agent_id):
                    if self.message_id.exists():
                        if self.general_chat:
                            channel._qing_ai_reply_from_general_chat(self.message_id)
                        else:
                            channel._qing_ai_reply_from_manual(self.message_id)
                    self.write({'state': 'done'})
                else:
                    self._step_agent_chat(channel)
        except Exception:
            _logger.exception('Qing AI could not answer queued Discuss message %s', self.message_id.id)
            self.write({'state': 'failed'})
        if channel.exists():
            channel._qing_ai_set_typing(self.state in ACTIVE_STATES)

    def _step_agent_chat(self, channel):
        if self.state == 'pending':
            if self.message_id.exists():
                channel._qing_ai_reply_to_message(self.message_id)
        elif self.run_id:
            channel._qing_ai_continue_reply(self.run_id)
        last_id = channel._qing_ai_publish_answers(self.reply_after_id)
        active_run = channel._qing_ai_conversation_env()._assistant_active_run()
        self.write({
            'reply_after_id': last_id,
            'run_id': active_run['id'] if active_run else 0,
            'state': 'running' if active_run else 'done',
        })

    @api.autovacuum
    def _gc_finished_jobs(self):
        """Keep finished jobs briefly for diagnosis, then delete them."""
        domain = [
            ('state', 'in', ('done', 'failed')),
            ('write_date', '<', fields.Datetime.now() - JOB_RETENTION),
        ]
        jobs = self.sudo().search(domain, limit=JOB_GC_BATCH_SIZE + 1)
        batch = jobs[:JOB_GC_BATCH_SIZE]
        batch.unlink()
        return len(batch), len(jobs) > JOB_GC_BATCH_SIZE

    channel_id = fields.Many2one('discuss.channel', required=True, readonly=True, index=True,
                                 ondelete='cascade')
    general_chat = fields.Boolean(readonly=True)
    manual_reply = fields.Boolean(readonly=True, help='Answer from the public manual.')
    message_id = fields.Many2one(
        'mail.message', readonly=True, ondelete='cascade',
        help='User message to answer; empty when continuing after a confirmed preview.',
    )
    reply_after_id = fields.Integer(readonly=True, help='Last Qing AI answer already shown in the chat.')
    run_id = fields.Integer(readonly=True, help='Planner run the reply is waiting on.')
    state = fields.Selection(JOB_STATES, default='pending', required=True, readonly=True, index=True)
