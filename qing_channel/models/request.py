import logging
from uuid import uuid4

from odoo import api, fields, models
from odoo.addons.qing_base.tools.channel_queue import due_channel_jobs, locked_job_environment, snapshot_digest
from odoo.fields import Domain

from ..errors import ChannelReviewRequired, DeferredChannelError, retry_delay


_logger = logging.getLogger(__name__)
STATES = [
    ('ready', 'Prepared'), ('uncertain', 'Possibly Sent'), ('success', 'Confirmed'),
    ('failed', 'Failed'), ('verify', 'Checking Result'), ('satisfied', 'Target Satisfied'),
    ('review', 'Needs Review'), ('obsolete', 'Cancelled'), ('superseded', 'Replaced'),
]


class ChannelRequest(models.Model):
    _name = 'qing.channel.request'
    _description = 'Channel Outbound Request'
    _check_company_auto = True

    @api.model
    def _prepare_request(self, action, operation, resource, payload):
        return self.create({
            'action_id': action.id,
            'operation': operation,
            'resource': resource,
            'payload': payload,
            'payload_hash': snapshot_digest(payload),
            'idempotency_key': uuid4().hex,
        })

    @api.model
    def _expedited_job_domain(self):
        return Domain('action_id.expedited', '=', True)

    @api.model
    def _cron_process(self, limit=30):
        due = [('state', 'in', ('ready', 'uncertain', 'verify')),
               '|', ('retry_at', '=', False), ('retry_at', '<=', fields.Datetime.now())]
        stores = self.env['qing.channel.store']._channel_worker_domain()
        due = Domain(due) & Domain('store_id', 'any', stores)
        for item in due_channel_jobs(self.sudo(), due, self._expedited_job_domain(), limit):
            key = f'qing-channel-outbox:{item.store_id.id}:{item.resource}'
            with locked_job_environment(self.env.registry, key) as env:
                if env is not None:
                    env[self._name].browse(item.id)._process_one()

    def _process_one(self):
        self = self.with_company(self.company_id)
        if self.state not in ('ready', 'uncertain', 'verify') or not self.store_id._channel_worker_ready():
            return
        try:
            state = self.store_id._execute_channel_request(self, self.state != 'ready')
            self.state = state
            if state in ('success', 'satisfied'):
                self.action_id.state = 'done'
            elif state == 'obsolete':
                self.action_id.state = 'obsolete'
            self.env.cr.commit()
        except ChannelReviewRequired as exc:
            self.env.cr.rollback()
            if self.store_id._channel_operation_failed(exc.code):
                self._finish_review(exc.code)
            self.env.cr.commit()
        except DeferredChannelError as exc:
            self.env.cr.rollback()
            if self.store_id._channel_operation_failed(exc.code):
                self._defer(exc.code, exc.retry_after)
            self.env.cr.commit()
        except Exception:
            self.env.cr.rollback()
            _logger.error('Channel outbound request %s requires reconciliation', self.id)
            self._defer('outbound_error')
            self.env.cr.commit()

    def _mark_before_send(self):
        """Durably record the immutable request before any external mutation."""
        self.ensure_one()
        if snapshot_digest(self.payload) != self.payload_hash:
            raise ChannelReviewRequired('request_payload_changed')
        now = fields.Datetime.now()
        self.write({'state': 'uncertain', 'first_sent_at': self.first_sent_at or now,
                    'confirmed_rejection': False})
        self._log_attempt('send_started')
        self.env.cr.commit()

    def _record_remote_rejection(self, result):
        self.confirmed_rejection = True
        self._log_attempt(result)
        self.env.cr.commit()

    def _log_attempt(self, result):
        self.env['qing.channel.request.attempt'].create({'request_id': self.id, 'result': result})

    def _defer(self, code, retry_after=None):
        if retry_after is None:
            self.checks += 1
        if self.checks >= 8:
            self._finish_review(code)
            return
        if self.state != 'ready':
            self.state = 'verify'
        self.retry_at = fields.Datetime.now() + retry_delay(self.checks, retry_after)
        self._log_attempt(code)

    def _finish_review(self, code):
        self.state = 'review'
        self.action_id._review(code)
        self._log_attempt(code)
        self.env['qing.channel.exception']._flag(self.store_id, self, code)

    def _recheck_after_exception(self, code):
        self.action_id.state = 'external'
        self.write({'state': 'verify' if self.first_sent_at else 'ready', 'checks': 0, 'retry_at': False})

    action_id = fields.Many2one('qing.channel.action', required=True, ondelete='restrict', index=True)
    checks = fields.Integer(default=0)
    company_id = fields.Many2one(related='action_id.company_id', store=True, index=True, precompute=True)
    first_sent_at = fields.Datetime(copy=False)
    confirmed_rejection = fields.Boolean(copy=False)
    idempotency_key = fields.Char(required=True, readonly=True, copy=False)
    operation = fields.Char(required=True)
    payload = fields.Json(required=True, groups='qing_channel.group_channel_manager')
    payload_hash = fields.Char(required=True, copy=False)
    reconcile_after = fields.Datetime(copy=False)
    reconcile_quantity = fields.Integer(copy=False)
    remote_reference = fields.Char(copy=False)
    resource = fields.Char(required=True, index=True)
    retry_at = fields.Datetime(index=True)
    state = fields.Selection(STATES, required=True, default='ready', index=True)
    store_id = fields.Many2one(related='action_id.store_id', store=True, precompute=True, index=True)

    _idempotency_unique = models.UniqueIndex('(idempotency_key)')
