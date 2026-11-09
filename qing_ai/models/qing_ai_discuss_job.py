"""Queued Discuss replies so posting a message never waits for the AI provider."""

import logging
from datetime import timedelta

from odoo import api, fields, models


_logger = logging.getLogger(__name__)

JOB_STATES = [
    ('pending', 'Pending'),
    ('done', 'Done'),
    ('failed', 'Failed'),
]
MAX_JOBS_PER_RUN = 20
JOB_RETENTION = timedelta(days=7)
JOB_GC_BATCH_SIZE = 1000


class QingAiDiscussJob(models.Model):
    _name = 'qing.ai.discuss.job'
    _description = 'Qing AI Discuss Reply Job'
    _order = 'id'

    @api.model
    def _enqueue(self, channel, message):
        """
        Record a Discuss message for a background reply and wake the reply cron.

        :param channel: AI chat channel that received the message
        :param message: user message to answer
        :return: queued job
        """
        job = self.sudo().create({'channel_id': channel.id, 'message_id': message.id})
        channel._qing_ai_set_typing(True)
        self.env.ref('qing_ai.ir_cron_qing_ai_discuss_reply').sudo()._trigger()
        return job

    @api.model
    def _process_pending(self, limit=MAX_JOBS_PER_RUN):
        """Answer queued messages oldest first; each reply is committed separately in cron."""
        Job = self.sudo()
        domain = [('state', '=', 'pending')]
        jobs = Job.search(domain, limit=limit)
        in_cron = bool(self.env.context.get('cron_id'))
        for job in jobs:
            job._process_one()
            if in_cron:
                self.env['ir.cron']._commit_progress(1, remaining=Job.search_count(domain))
        if in_cron and Job.search_count(domain):
            self.env.ref('qing_ai.ir_cron_qing_ai_discuss_reply').sudo()._trigger()
        return len(jobs)

    def _process_one(self):
        self.ensure_one()
        # Skip a job another cron worker is already answering.
        self.env.cr.execute(
            'SELECT id FROM qing_ai_discuss_job WHERE id = %s AND state = %s FOR UPDATE SKIP LOCKED',
            [self.id, 'pending'],
        )
        if not self.env.cr.fetchone():
            return
        channel = self.channel_id.sudo()
        state = 'done'
        try:
            with self.env.cr.savepoint():
                if channel.exists() and self.message_id.exists():
                    channel._qing_ai_reply_to_message(self.message_id)
        except Exception:
            _logger.exception('Qing AI could not answer queued Discuss message %s', self.message_id.id)
            state = 'failed'
        if channel.exists():
            channel._qing_ai_set_typing(False)
        self.write({'state': state})

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
    message_id = fields.Many2one('mail.message', required=True, readonly=True, ondelete='cascade')
    state = fields.Selection(JOB_STATES, default='pending', required=True, readonly=True, index=True)
