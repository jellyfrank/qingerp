import hashlib
import json
import secrets
from datetime import timedelta

from psycopg2 import IntegrityError
from odoo import api, fields, models, _, SUPERUSER_ID
from odoo.exceptions import AccessError, UserError, ValidationError
from ..services.cloud_api import CloudError, MAX_MEDIA_BYTES

MESSAGE_STATES = [(v, v.replace('_', ' ').title()) for v in (
    'queued', 'sending', 'accepted', 'delivered', 'read', 'received',
    'retry_wait', 'unknown', 'failed', 'cancelled', 'expired')]
MESSAGE_KINDS = [(v, v.title()) for v in ('text', 'template', 'image', 'document', 'audio', 'video', 'interactive', 'other')]
PURPOSES = [('service', 'Service'), ('marketing', 'Marketing')]
STATE_RANK = {'accepted': 1, 'delivered': 2, 'read': 3}
MEDIA_TYPES = {'image': {'image/jpeg', 'image/png'},
               'document': {'application/pdf', 'text/plain'},
               'audio': {'audio/ogg', 'audio/mpeg', 'audio/mp4'}, 'video': {'video/mp4'}}


class WhatsappMessage(models.Model):
    _name = 'qing.whatsapp.message'

    _description = 'WhatsApp Message'

    _order = 'id desc'

    _logical_unique = models.Constraint('unique(account_id, request_key)', 'This request has already been queued.')

    _provider_unique = models.Constraint('unique(account_id, provider_uid)', 'This platform message exists.')

    @api.model
    def _enqueue(self, conversation, kind, content, *, request_key, template=None,
                 attachment=None, purpose='service'):
        """Trusted extension entry point; always authorize the current real user."""
        conversation.with_user(self.env.uid)._check_sender()
        if not request_key or len(request_key) > 128 or purpose not in dict(PURPOSES):
            raise ValidationError(_('Invalid message request.'))
        account = conversation.account_id
        digest = hashlib.sha256(json.dumps({'kind': kind, 'content': content, 'purpose': purpose,
            'template': template.id if template else None, 'attachment': attachment.id if attachment else None},
            sort_keys=True).encode()).hexdigest()
        domain = [('account_id', '=', account.id), ('request_key', '=', request_key)]
        existing = self.sudo().search(domain, limit=1)
        if existing:
            if existing.conversation_id != conversation or existing.request_digest != digest:
                raise ValidationError(_('The request key belongs to another conversation.'))
            return existing
        if kind not in dict(MESSAGE_KINDS) or kind == 'other':
            raise ValidationError(_('Unsupported outbound message type.'))
        if kind == 'template':
            if not template or template.account_id != account:
                raise ValidationError(_('Select a template for this account.'))
            template.check_access('read')
            content = template._prepare_payload(content)
            purpose = 'marketing' if template.category == 'MARKETING' else purpose
        payload = {'messaging_product': 'whatsapp', 'recipient_type': 'individual',
                   'to': conversation.number, 'type': kind, kind: content,
                   'biz_opaque_callback_data': request_key}
        values = {'conversation_id': conversation.id, 'kind': kind, 'payload': payload,
                  'request_key': request_key, 'request_digest': digest, 'sender_id': self.env.uid, 'purpose': purpose,
                  'template_id': template.id if template else False,
                  'body': content.get('body', '') if kind == 'text' else json.dumps(content, ensure_ascii=False)}
        if template:
            values['template_snapshot'] = {'name': template.name, 'language': template.language,
                'category': template.category, 'components': json.loads(template.components_json or '[]')}
        media_kind = kind
        if kind == 'template':
            headers = [part for part in json.loads(template.components_json or '[]') if part.get('type') == 'HEADER']
            media_kind = headers[0].get('format', '').lower() if headers else ''
            if media_kind in MEDIA_TYPES and not attachment:
                raise ValidationError(_('This template requires a media attachment.'))
        if attachment:
            attachment.check_access('read')
            if attachment.mimetype not in MEDIA_TYPES.get(media_kind, set()):
                raise ValidationError(_('Unsupported media type. Use PDF/text, JPEG/PNG, audio or MP4.'))
            raw = attachment.raw.content
            if not raw or len(raw) > MAX_MEDIA_BYTES:
                raise ValidationError(_('The attachment is empty or exceeds 16 MB.'))
            values['attachment_hash'] = hashlib.sha256(raw).hexdigest()
            values['media_kind'] = media_kind
        try:
            with self.env.cr.savepoint():
                message = self.sudo().create(values)
                if attachment:
                    snapshot = attachment.sudo().copy({'res_model': self._name, 'res_id': message.id,
                                                       'public': False, 'access_token': False})
                    message.attachment_id = snapshot
                message._preflight()
                message._post_to_conversation()
        except IntegrityError:
            existing = self.sudo().search(domain, limit=1)
            if existing and existing.request_digest == digest and existing.conversation_id == conversation:
                return existing
            raise
        self.env.ref('qing_whatsapp.cron_send').sudo()._trigger()
        return message

    def _preflight(self):
        self.ensure_one()
        user = self.sender_id
        if not user.active or user.share:
            raise AccessError(_('The sending user is inactive.'))
        self.conversation_id.with_user(user)._check_sender()
        if not self.account_id.active:
            raise UserError(_('Account disabled.'))
        if self.kind == 'template':
            current_definition = {'name': self.template_id.name, 'language': self.template_id.language,
                'category': self.template_id.category, 'components': json.loads(self.template_id.components_json or '[]')}
            if self.template_snapshot != current_definition:
                raise UserError(_('The template changed after this message was queued. Create a new message.'))
            if self.template_id.status != 'APPROVED' or self.template_id.quality == 'RED':
                raise UserError(_('The template is no longer available.'))
        elif not self.conversation_id.window_until or self.conversation_id.window_until <= fields.Datetime.now():
            raise UserError(_('The customer service window expired. Send an approved template.'))
        Consent = self.env['qing.whatsapp.consent'].sudo()
        domain = [('conversation_id', '=', self.conversation_id.id), ('purpose', '=', self.purpose)]
        consent = Consent.search(domain, limit=1)
        if consent.state == 'revoked' or (self.purpose == 'marketing' and consent.state != 'granted'):
            raise UserError(_('Consent is missing or has been revoked.'))

    def _post_to_conversation(self):
        self.ensure_one()
        from markupsafe import Markup, escape
        body = Markup('<p>%s</p>') % escape(self.body or self.kind)
        message = self.conversation_id.sudo().message_post(body=body, message_type='comment',
            subtype_xmlid='mail.mt_note', attachment_ids=self.attachment_id.ids,
            author_id=self.sender_id.partner_id.id if self.sender_id else False)
        self.mail_message_id = message
        channel = self.conversation_id._ensure_discuss_channel()
        self.discuss_message_id = channel._qing_post_message(self)

    def _apply_status(self, status, provider_uid=None):
        self.ensure_one()
        self.env.cr.execute('SELECT id FROM qing_whatsapp_message WHERE id=%s FOR UPDATE', [self.id])
        self.invalidate_recordset()
        mapped = {'sent': 'accepted', 'failed': 'failed'}.get(status, status)
        values = {}
        if provider_uid and not self.provider_uid:
            values['provider_uid'] = provider_uid
        if mapped in STATE_RANK:
            if STATE_RANK[mapped] > STATE_RANK.get(self.state, 0):
                values['state'] = mapped
        elif mapped == 'failed' and self.state not in {'delivered', 'read'}:
            values['state'] = 'failed'
        if values:
            self.write(values)

    @api.model
    def _cron_send(self):
        """Durably mark sending BEFORE external IO; crashes remain unknown, never auto-resend."""
        domain = [('state', 'in', ['queued', 'retry_wait']), ('next_attempt_at', '<=', fields.Datetime.now())]
        ids = self.sudo().search(domain, order='id', limit=50).ids
        for message_id in ids:
            with self.env.registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                message = env[self._name].browse(message_id)
                cr.execute("SELECT id FROM qing_whatsapp_message WHERE id=%s AND state IN ('queued','retry_wait') FOR UPDATE SKIP LOCKED", [message_id])
                if not cr.fetchone():
                    continue
                try:
                    message._preflight()
                except (UserError, AccessError, ValidationError):
                    message.write({'state': 'expired' if message.kind != 'template' and
                        (not message.conversation_id.window_until or message.conversation_id.window_until <= fields.Datetime.now()) else 'failed',
                        'error_code': 'preflight_denied'})
                    cr.commit()
                    continue
                attempt = env['qing.whatsapp.attempt'].create({'message_id': message.id})
                message.write({'state': 'sending', 'attempt_count': message.attempt_count + 1,
                               'last_attempt_at': fields.Datetime.now()})
                cr.commit()
                # If this transaction crashes after an API success, the durable sending marker survives.
                try:
                    uid = message._send_cloud()
                except CloudError as exc:
                    state = 'unknown' if exc.uncertain else ('retry_wait' if exc.retryable and message.attempt_count < 5 else 'failed')
                    message._record_transport_error({'state': state, 'error_code': exc.code,
                        'next_attempt_at': fields.Datetime.now() + timedelta(seconds=min(3600, 30 * 2 ** message.attempt_count) + secrets.randbelow(15))})
                    attempt.write({'outcome': state, 'error_code': exc.code, 'finished_at': fields.Datetime.now()})
                except Exception:
                    # Never log payloads, credentials, or provider response text.
                    message._record_transport_error({'state': 'unknown', 'error_code': 'unexpected_transport_error'})
                    attempt.write({'outcome': 'unknown', 'error_code': 'unexpected_transport_error', 'finished_at': fields.Datetime.now()})
                else:
                    message._apply_status('sent', uid)
                    attempt.write({'outcome': 'accepted', 'finished_at': fields.Datetime.now()})
                cr.commit()
        self._recover_stale()

    def _record_transport_error(self, values):
        self.env.cr.execute('SELECT id FROM qing_whatsapp_message WHERE id=%s FOR UPDATE', [self.id])
        self.invalidate_recordset()
        if self.state not in STATE_RANK:
            self.write(values)

    def _send_cloud(self):
        self.ensure_one()
        payload = dict(self.payload)
        client = self.account_id._client()
        if self.attachment_id:
            raw = self.attachment_id.raw.content
            if hashlib.sha256(raw).hexdigest() != self.attachment_hash:
                raise CloudError('attachment_changed')
            media_id = client.upload(self.attachment_id.name, raw, self.attachment_id.mimetype)
            media = {'id': media_id}
            if self.kind in {'image', 'document', 'video'} and payload[self.kind].get('caption'):
                media['caption'] = payload[self.kind]['caption']
            if self.media_kind == 'document':
                media['filename'] = self.attachment_id.name
            if self.kind == 'template':
                payload = json.loads(json.dumps(payload))
                components = payload['template']['components']
                components[:] = [c for c in components if c.get('type') != 'header']
                components.append({'type': 'header', 'parameters': [{'type': self.media_kind, self.media_kind: media}]})
            else:
                payload[self.kind] = media
        return client.send(payload)

    def _recover_stale(self):
        domain = [('state', '=', 'sending'), ('last_attempt_at', '<', fields.Datetime.now() - timedelta(minutes=15))]
        for message in self.sudo().search(domain):
            self.env.cr.execute("SELECT id FROM qing_whatsapp_message WHERE id=%s AND state='sending' FOR UPDATE SKIP LOCKED", [message.id])
            if self.env.cr.fetchone():
                message.invalidate_recordset()
                if message.state == 'sending':
                    message.write({'state': 'unknown', 'error_code': 'worker_interrupted'})

    account_id = fields.Many2one(related='conversation_id.account_id', store=True, index=True)

    attachment_hash = fields.Char()

    attachment_id = fields.Many2one('ir.attachment', ondelete='restrict')

    attempt_count = fields.Integer(default=0)

    attempt_ids = fields.One2many('qing.whatsapp.attempt', 'message_id')

    body = fields.Text()

    company_id = fields.Many2one(related='conversation_id.company_id', store=True, index=True)

    conversation_id = fields.Many2one('qing.whatsapp.conversation', required=True, ondelete='restrict', index=True)

    direction = fields.Selection([('in', 'Inbound'), ('out', 'Outbound')], default='out', required=True)

    discuss_message_id = fields.Many2one('mail.message', ondelete='set null')

    error_code = fields.Char()

    kind = fields.Selection(MESSAGE_KINDS, required=True)

    last_attempt_at = fields.Datetime()

    mail_message_id = fields.Many2one('mail.message', ondelete='set null')

    media_kind = fields.Char()

    next_attempt_at = fields.Datetime(default=fields.Datetime.now, index=True)

    payload = fields.Json()

    provider_uid = fields.Char(index=True)

    purpose = fields.Selection(PURPOSES, default='service', required=True)

    request_digest = fields.Char()

    request_key = fields.Char(required=True, index=True)

    sender_id = fields.Many2one('res.users', ondelete='restrict')

    state = fields.Selection(MESSAGE_STATES, default='queued', required=True, index=True)

    template_id = fields.Many2one('qing.whatsapp.template', ondelete='restrict')

    template_snapshot = fields.Json()

    def action_cancel(self):
        for message in self:
            message.conversation_id._check_sender()
            self.env.cr.execute('SELECT id FROM qing_whatsapp_message WHERE id=%s FOR UPDATE', [message.id])
            message.invalidate_recordset()
            if message.state not in {'queued', 'retry_wait'}:
                raise UserError(_('Only messages waiting to send may be cancelled.'))
            message.sudo().write({'state': 'cancelled'})

    def action_retry(self):
        for message in self:
            message.conversation_id._check_sender()
            self.env.cr.execute('SELECT id FROM qing_whatsapp_message WHERE id=%s FOR UPDATE', [message.id])
            message.invalidate_recordset()
            if message.state != 'failed' or message.provider_uid:
                raise UserError(_('Only definitely failed messages without a platform ID may be retried. Unknown results require reconciliation.'))
            message.sudo()._preflight()
            message.sudo().write({'state': 'queued', 'next_attempt_at': fields.Datetime.now(), 'error_code': False})
        self.env.ref('qing_whatsapp.cron_send').sudo()._trigger()
