import secrets
import re

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError
from odoo.tools import html2plaintext


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    def _qing_post_message(self, message):
        """Private transport mirror; never use a client-controlled context bypass."""
        self.ensure_one()
        self.qing_conversation_id._sync_discuss_members()
        from markupsafe import escape
        author = message.sender_id.partner_id if message.direction == 'out' else message.conversation_id.partner_id
        body = message.body or message.kind
        if message.kind == 'template':
            definition = next((c for c in (message.template_snapshot or {}).get('components', []) if c.get('type') == 'BODY'), {})
            values = next((c.get('parameters', []) for c in message.payload.get('template', {}).get('components', []) if c.get('type') == 'body'), [])
            body = re.sub(r'\{\{(\d+)\}\}', lambda m: str(values[int(m[1]) - 1].get('text', m[0]))
                if int(m[1]) <= len(values) else m[0], definition.get('text') or _('Template: %s', message.template_id.name))
        elif message.direction == 'out' and message.kind in ('document', 'image', 'video', 'audio'):
            body = message.payload.get(message.kind, {}).get('caption') or message.attachment_id.name or message.kind
        if message.direction == 'in' and not author:
            body = '%s: %s' % (message.conversation_id.number, body)
        return super(DiscussChannel, self.sudo()).message_post(
            body=escape(body), message_type='comment', subtype_xmlid='mail.mt_comment',
            author_id=author.id or False, attachment_ids=message.attachment_id.ids)

    def _message_update_content(self, message, /, **kwargs):
        if self.sudo().qing_conversation_id:
            raise UserError(_('WhatsApp messages cannot be edited or deleted here. Send a correction instead.'))
        return super()._message_update_content(message, **kwargs)

    def _store_channel_fields(self, res):
        super()._store_channel_fields(res)
        res.attr('qing_conversation_id', lambda c: c.qing_conversation_id.id or False)

    qing_conversation_id = fields.Many2one('qing.whatsapp.conversation', copy=False, index=True, ondelete='restrict')

    _qing_conversation_unique = models.Constraint('unique(qing_conversation_id)', 'This conversation already has a chat.')

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(v.get('qing_conversation_id') for v in vals_list):
            raise AccessError(_('Create WhatsApp chats from a conversation.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and ('qing_conversation_id' in vals or
                (self.sudo().qing_conversation_id and set(vals) & {'parent_channel_id', 'group_ids', 'group_public_id'})):
            raise AccessError(_('WhatsApp chat access is managed by the account.'))
        return super().write(vals)

    def message_post(self, *, message_type='notification', **kwargs):
        self.ensure_one()
        conversation = self.sudo().qing_conversation_id
        if not conversation or message_type != 'comment':
            return super().message_post(message_type=message_type, **kwargs)
        conversation = conversation.with_user(self.env.uid)
        conversation._check_sender()
        text = html2plaintext(str(kwargs.get('body') or '')).strip()
        attachment_ids = kwargs.get('attachment_ids') or []
        if kwargs.get('attachments') or len(attachment_ids) > 1:
            raise UserError(_('Send one attachment at a time in WhatsApp.'))
        attachment = self.env['ir.attachment'].browse(attachment_ids).exists()
        kind = 'text'
        if attachment:
            attachment.check_access('read')
            mime = attachment.mimetype or ''
            kind = next((k for k in ('image', 'audio', 'video') if mime.startswith(k + '/')), 'document')
        if not text and not attachment:
            raise UserError(_('Enter a message or attach a file.'))
        if len(text) > (4096 if kind == 'text' else 1024):
            raise UserError(_('The WhatsApp message is too long.'))
        if kind == 'audio' and text:
            raise UserError(_('Send audio and text as separate messages.'))
        content = {'body': text} if kind == 'text' else {'caption': text}
        message = self.env['qing.whatsapp.message']._enqueue(
            conversation, kind, content, request_key=secrets.token_hex(24), attachment=attachment)
        return message.discuss_message_id
