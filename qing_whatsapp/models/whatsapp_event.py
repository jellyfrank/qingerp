import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from psycopg2 import IntegrityError
from odoo import api, fields, models, _
from odoo.exceptions import UserError

EVENT_STATES = [(v, v.title()) for v in ('pending', 'done', 'retry', 'waiting', 'failed')]


class WhatsappEvent(models.Model):
    _name = 'qing.whatsapp.event'

    _description = 'WhatsApp Webhook Inbox'

    _order = 'id desc'

    _event_unique = models.Constraint('unique(account_id, event_key)', 'This event has been received.')

    @api.model
    def _receive(self, account, kind, payload):
        if kind == 'message' and isinstance(payload.get('id'), str):
            key = 'message:' + payload['id']
        else:
            key = kind + ':' + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        domain = [('account_id', '=', account.id), ('event_key', '=', key)]
        existing = self.sudo().search(domain, limit=1)
        if existing:
            return existing
        try:
            with self.env.cr.savepoint():
                event = self.sudo().create({'account_id': account.id, 'kind': kind, 'payload': payload, 'event_key': key})
        except IntegrityError:
            return self.sudo().search(domain, limit=1)
        self.env.ref('qing_whatsapp.cron_events').sudo()._trigger()
        return event

    def _process(self):
        self.ensure_one()
        if self.kind == 'status':
            if not isinstance(self.payload.get('id'), str) or not self.payload['id']:
                raise UserError(_('Missing platform message ID.'))
            Message = self.env['qing.whatsapp.message'].sudo()
            domain = [('account_id', '=', self.account_id.id), ('direction', '=', 'out'),
                      ('provider_uid', '=', self.payload.get('id'))]
            message = Message.search(domain, limit=1)
            if not message and self.payload.get('biz_opaque_callback_data'):
                domain = [('account_id', '=', self.account_id.id), ('direction', '=', 'out'),
                          ('state', 'in', ['sending', 'unknown', 'accepted', 'delivered', 'read']),
                          ('request_key', '=', self.payload['biz_opaque_callback_data'])]
                message = Message.search(domain, limit=1)
            if not message:
                if self.create_date < fields.Datetime.now() - timedelta(days=7):
                    self.write({'state': 'failed', 'error_code': 'unmatched_receipt'})
                    return
                self.write({'state': 'waiting', 'next_attempt_at': fields.Datetime.now() + timedelta(minutes=5)})
                return
            message._apply_status(self.payload.get('status'), self.payload.get('id'))
        elif self.kind == 'message':
            self._process_message()
        elif self.kind == 'template':
            domain = [('account_id', '=', self.account_id.id), ('remote_uid', '=', str(self.payload.get('message_template_id')))]
            template = self.env['qing.whatsapp.template'].sudo().search(domain)
            if template and self.payload.get('event'):
                template.write({'status': self.payload['event']})
            if template and self.payload.get('new_quality_score'):
                template.write({'quality': self.payload['new_quality_score']})
        self.write({'state': 'done', 'error_code': False})

    def _process_message(self):
        values = self.payload
        Conversation = self.env['qing.whatsapp.conversation'].sudo()
        conversation = Conversation._find_or_create(self.account_id, values.get('from'))
        # Serialize a customer's messages so timestamps and consent decisions cannot race.
        self.env.cr.execute('SELECT id FROM qing_whatsapp_conversation WHERE id=%s FOR UPDATE', [conversation.id])
        conversation.invalidate_recordset()
        kind = values.get('type', 'other')
        raw_time = int(values.get('timestamp', 0))
        event_time = datetime.fromtimestamp(raw_time, timezone.utc).replace(tzinfo=None)
        now = fields.Datetime.now()
        if event_time > now + timedelta(minutes=5):
            raise UserError(_('Invalid future event timestamp.'))
        latest = not conversation.last_customer_at or event_time >= conversation.last_customer_at
        if latest:
            conversation.write({'last_customer_at': min(event_time, now), 'state': 'open'})
        body = values.get('text', {}).get('body', '')
        if kind == 'button':
            body = values.get('button', {}).get('text', '')
        attachment = None
        if kind in {'image', 'document', 'audio', 'video', 'sticker'}:
            media = values.get(kind, {})
            content, mime = self.account_id._client().download(media['id'])
            attachment = self.env['ir.attachment'].sudo().create({'name': media.get('filename', kind),
                'raw': content, 'mimetype': mime, 'res_model': conversation._name, 'res_id': conversation.id})
            body = media.get('caption', '')
        if not body:
            body = json.dumps(values.get(kind, {}), ensure_ascii=False)
        Message = self.env['qing.whatsapp.message'].sudo()
        message = Message.create({'conversation_id': conversation.id, 'direction': 'in',
            'kind': kind if kind in {'text', 'image', 'document', 'audio', 'video', 'interactive'} else 'other',
            'body': body, 'payload': values, 'provider_uid': values['id'],
            'state': 'received', 'request_key': 'in:' + values['id'],
            'attachment_id': attachment.id if attachment else False})
        message._post_to_conversation()
        normalized = ' '.join(re.findall(r'[^\W_]+', body.casefold()))
        if normalized in {'stop', 'unsubscribe', 'stop promotions', '\u9000\u8ba2', '\u53d6\u6d88\u8ba2\u9605'}:
            self.env['qing.whatsapp.consent'].sudo().create({'conversation_id': conversation.id,
                'purpose': 'marketing', 'state': 'revoked', 'evidence': 'inbound:' + values['id']})
        if kind in {'order', 'interactive', 'button'}:
            commerce = self.env['qing.whatsapp.commerce.event'].sudo().create({
                'event_id': self.id, 'conversation_id': conversation.id, 'kind': kind,
                'payload': values.get(kind, {}), 'message_id': message.id})
            commerce._dispatch_verified_event()

    @api.model
    def _cron_process(self):
        domain = [('state', 'in', ['pending', 'retry', 'waiting']), ('next_attempt_at', '<=', fields.Datetime.now())]
        events = self.sudo().search(domain, order='id', limit=100)
        for event in events:
            self.env.cr.execute("SELECT id FROM qing_whatsapp_event WHERE id=%s AND state IN ('pending','retry','waiting') FOR UPDATE SKIP LOCKED", [event.id])
            if not self.env.cr.fetchone():
                continue
            try:
                with self.env.cr.savepoint():
                    event._process()
            except Exception:
                event.invalidate_recordset()
                count = event.attempts + 1
                event.write({'attempts': count, 'state': 'failed' if count >= 5 else 'retry',
                    'error_code': 'processing_failed', 'next_attempt_at': fields.Datetime.now() + timedelta(seconds=60 * 2 ** count)})

    account_id = fields.Many2one('qing.whatsapp.account', required=True, ondelete='restrict')

    attempts = fields.Integer(default=0)

    company_id = fields.Many2one(related='account_id.company_id', store=True, index=True)

    error_code = fields.Char()

    event_key = fields.Char(required=True, index=True)

    kind = fields.Char(required=True)

    next_attempt_at = fields.Datetime(default=fields.Datetime.now, index=True)

    payload = fields.Json()

    state = fields.Selection(EVENT_STATES, default='pending', required=True, index=True)

    def action_retry(self):
        self.check_access('read')
        if not self.env.user.has_group('qing_whatsapp.group_manager'):
            raise UserError(_('Only managers can retry events.'))
        if any(e.state not in {'failed', 'waiting'} for e in self):
            raise UserError(_('Only failed or waiting events may be retried.'))
        self.sudo().write({'state': 'pending', 'attempts': 0, 'next_attempt_at': fields.Datetime.now()})
        self.env.ref('qing_whatsapp.cron_events').sudo()._trigger()
