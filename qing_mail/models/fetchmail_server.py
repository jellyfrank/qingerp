import hashlib
import logging

from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.binary import BinaryBytes


_logger = logging.getLogger(__name__)
MAX_FAILURE_BYTES = 10 * 1024 * 1024
MAX_PENDING_FAILURES = 1000
ADVISORY_LOCK_NAMESPACE = 872057


class FetchmailServer(models.Model):
    _inherit = 'fetchmail.server'

    def _qing_process_incoming(self, payload):
        self.ensure_one()
        with self.env.registry.cursor() as cursor:
            context = dict(self.env.context, default_fetchmail_server_id=self.id)
            message_env = api.Environment(cursor, SUPERUSER_ID, context)
            message_env['mail.thread'].message_process(
                model=self.object_id.model or False,
                message=payload,
                save_original=self.original,
                strip_attachments=not self.attach,
            )
            cursor.commit()

    def _qing_store_failure(self, payload, error):
        self.ensure_one()
        if len(payload) > MAX_FAILURE_BYTES:
            raise UserError(self.env._('The failed email exceeds the recovery storage limit.'))

        digest = hashlib.sha256(payload).hexdigest()
        with self.env.registry.cursor() as cursor:
            failure_env = api.Environment(cursor, SUPERUSER_ID, {})
            Failure = failure_env['qing.mail.inbound.failure']
            domain = [('server_id', '=', self.id), ('payload_sha256', '=', digest)]
            existing = Failure.search(domain, limit=1)
            if existing:
                cursor.commit()
                return
            pending_domain = [('state', '=', 'pending')]
            if Failure.search_count(pending_domain) >= MAX_PENDING_FAILURES:
                raise UserError(self.env._('The incoming email recovery queue is full.'))
            Failure.create({
                'server_id': self.id,
                'payload': BinaryBytes(payload),
                'payload_sha256': digest,
                'payload_size': len(payload),
                'failure_type': type(error).__name__,
            })
            cursor.commit()

    def _qing_fetch_imap(self, batch_limit):
        self.ensure_one()
        self.env.cr.execute(
            'SELECT pg_try_advisory_lock(%s, %s)',
            (ADVISORY_LOCK_NAMESPACE, self.id),
        )
        if not self.env.cr.fetchone()[0]:
            return None

        connection = None
        try:
            connection = self._connect__()
            connection.check_unread_messages()
            count = 0
            for message_number, payload in connection.retrieve_unread_messages():
                try:
                    self._qing_process_incoming(payload)
                except Exception as error:  # noqa: BLE001
                    _logger.warning(
                        'Incoming email processing failed on server %s: %s',
                        self.id,
                        type(error).__name__,
                    )
                    self._qing_store_failure(payload, error)
                # Only acknowledge after successful business processing or durable recovery.
                connection.handled_message(message_number)
                count += 1
                if self.env.context.get('cron_id'):
                    remaining_time = self.env['ir.cron']._commit_progress(1)
                    if not remaining_time:
                        break
                if count >= batch_limit:
                    break
            self.write({
                'date': fields.Datetime.now(),
                'error_date': False,
                'error_message': False,
            })
            self.env.cr.commit()
            return None
        except Exception as error:  # noqa: BLE001
            _logger.warning(
                'Incoming email fetch stopped on server %s: %s',
                self.id,
                type(error).__name__,
            )
            self.env.cr.rollback()
            self.write({
                'error_date': fields.Datetime.now(),
                'error_message': type(error).__name__,
            })
            self.env.cr.commit()
            return error
        finally:
            if connection:
                try:
                    connection.disconnect()
                except Exception:  # noqa: BLE001
                    _logger.warning('Could not close incoming server %s', self.id)
            self.env.cr.execute(
                'SELECT pg_advisory_unlock(%s, %s)',
                (ADVISORY_LOCK_NAMESPACE, self.id),
            )

    def _fetch_mail(self, batch_limit=50):
        reliable_servers = self.filtered(
            lambda server: server.qing_reliable_imap and server.server_type == 'imap'
        )
        regular_servers = self - reliable_servers
        result = None
        if regular_servers:
            result = super(FetchmailServer, regular_servers)._fetch_mail(batch_limit)
        for server in reliable_servers:
            error = server._qing_fetch_imap(batch_limit)
            if error and not result:
                result = error
        return result

    qing_reliable_imap = fields.Boolean(
        string='Recover Failed IMAP Emails',
        default=True,
        help='Keep a private, recoverable copy before acknowledging a message that Odoo could not process.',
    )
