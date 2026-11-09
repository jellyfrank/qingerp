import hashlib

from odoo import fields, models
from odoo.exceptions import UserError


FAILURE_STATES = [
    ('pending', 'Pending Review'),
    ('resolved', 'Resolved'),
]


class QingMailInboundFailure(models.Model):
    _name = 'qing.mail.inbound.failure'
    _description = 'Recoverable Incoming Email Failure'
    _order = 'create_date desc, id desc'

    def _decode_payload(self):
        self.ensure_one()
        payload = bytes(self.payload) if self.payload else b''
        if hashlib.sha256(payload).hexdigest() != self.payload_sha256:
            raise UserError(self.env._('The saved email content does not match its checksum.'))
        return payload

    server_id = fields.Many2one(
        'fetchmail.server',
        required=True,
        index=True,
        ondelete='restrict',
        string='Incoming Server',
    )
    payload = fields.Binary(
        attachment=False,
        groups='base.group_system',
        string='Original Email',
    )
    payload_sha256 = fields.Char(required=True, index=True, readonly=True)
    payload_size = fields.Integer(readonly=True)
    failure_type = fields.Char(readonly=True)
    retry_count = fields.Integer(readonly=True)
    state = fields.Selection(FAILURE_STATES, default='pending', required=True, index=True)

    _unique_server_payload = models.Constraint(
        'unique(server_id, payload_sha256)',
        'This email is already recorded for this incoming server.',
    )

    def action_retry(self):
        self.check_access('write')
        for failure in self:
            if failure.state != 'pending':
                continue
            payload = failure._decode_payload()
            server = failure.server_id
            try:
                with self.env.cr.savepoint():
                    self.env['mail.thread'].sudo().with_context(
                        default_fetchmail_server_id=server.id,
                    ).message_process(
                        model=server.object_id.model or False,
                        message=payload,
                        save_original=server.original,
                        strip_attachments=not server.attach,
                    )
            except Exception as error:  # noqa: BLE001
                failure.write({
                    'failure_type': type(error).__name__,
                    'retry_count': failure.retry_count + 1,
                })
                continue
            failure.with_context(qing_mail_replay_succeeded=True).write({
                'failure_type': False,
                'payload': False,
                'retry_count': failure.retry_count + 1,
                'state': 'resolved',
            })
        return True

    def write(self, values):
        if values.get('state') == 'resolved' and not self.env.context.get('qing_mail_replay_succeeded'):
            raise UserError(self.env._('Use Retry to resolve a saved incoming email.'))
        return super().write(values)

    def unlink(self):
        if self.filtered(lambda failure: failure.state == 'pending'):
            raise UserError(self.env._('Resolve an incoming email before deleting its only saved copy.'))
        return super().unlink()
