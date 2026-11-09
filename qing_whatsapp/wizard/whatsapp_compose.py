import json
import secrets
import re

from odoo import fields, models, _
from odoo.exceptions import ValidationError

COMPOSE_KINDS = [(v, v.title()) for v in ('text', 'template', 'image', 'document', 'audio', 'video')]


class WhatsappCompose(models.TransientModel):
    _name = 'qing.whatsapp.compose'

    _description = 'Send WhatsApp Message'

    def _template_parameters(self):
        self.ensure_one()
        definitions = json.loads(self.template_id.components_json or '[]')
        components = []
        values = (self.parameter_values or '').splitlines()
        for definition in definitions:
            section = definition.get('type', '').upper()
            if section == 'BUTTONS':
                urls = iter((self.button_values or '').splitlines())
                for index, button in enumerate(definition.get('buttons', [])):
                    if button.get('type') == 'URL' and '{{' in button.get('url', ''):
                        value = next(urls, None)
                        if not value:
                            raise ValidationError(_('Provide a value for each dynamic URL button.'))
                        components.append({'type': 'button', 'sub_type': 'url', 'index': str(index),
                            'parameters': [{'type': 'text', 'text': value}]})
                continue
            if section not in {'BODY', 'HEADER'}:
                continue
            slots = sorted(set(re.findall(r'\{\{(\d+)\}\}', definition.get('text', ''))), key=int)
            if section == 'HEADER' and definition.get('format', 'TEXT') != 'TEXT':
                if definition.get('format') not in {'IMAGE', 'DOCUMENT', 'VIDEO'} or not self.attachment_id:
                    raise ValidationError(_('Select the media attachment required by this template.'))
                continue
            if not slots:
                continue
            parameters = values if section == 'BODY' else [self.header_value or '']
            if len(parameters) != len(slots) or any(not value for value in parameters):
                raise ValidationError(_('Provide one value per template placeholder, in numeric order.'))
            components.append({'type': section.lower(), 'parameters': [{'type': 'text', 'text': value} for value in parameters]})
        return components

    def _after_enqueue(self, message):
        """Private business integration hook inside the enqueue transaction."""
        return None

    account_id = fields.Many2one('qing.whatsapp.account', required=True)

    attachment_id = fields.Many2one('ir.attachment')

    body = fields.Text()

    button_values = fields.Text(string='Dynamic URL values', help='One URL suffix per dynamic button, in button order.')

    conversation_id = fields.Many2one('qing.whatsapp.conversation')

    header_value = fields.Char(string='Header value')

    kind = fields.Selection(COMPOSE_KINDS, default='text', required=True)

    number = fields.Char(required=True)

    parameter_values = fields.Text(string='Template values', help='One value per line, in placeholder number order.')

    partner_id = fields.Many2one('res.partner')

    request_key = fields.Char(default=lambda self: secrets.token_hex(24), readonly=True)

    template_id = fields.Many2one('qing.whatsapp.template')

    upload_data = fields.Binary(string='Upload attachment', attachment=False)

    upload_name = fields.Char(string='Filename')

    def action_send(self):
        self.ensure_one()
        self.account_id._assert_operator()
        conversation = self.conversation_id
        if conversation:
            conversation._check_sender()
            if conversation.account_id != self.account_id:
                raise ValidationError(_('The conversation belongs to another account.'))
        else:
            conversation = self.env['qing.whatsapp.conversation']._find_or_create(self.account_id, self.number, self.partner_id)
        if self.upload_data and not self.attachment_id:
            self.attachment_id = self.env['ir.attachment'].create({
                'name': self.upload_name or 'attachment', 'raw': self.upload_data,
                'res_model': self._name, 'res_id': self.id})
        if self.kind == 'text' and (not self.body or len(self.body) > 4096):
            raise ValidationError(_('Enter text up to 4096 characters.'))
        if self.kind in {'image', 'document', 'audio', 'video'} and not self.attachment_id:
            raise ValidationError(_('Select an attachment.'))
        if self.kind in {'image', 'document', 'video'} and len(self.body or '') > 1024:
            raise ValidationError(_('Media captions must not exceed 1024 characters.'))
        try:
            content = self._template_parameters() if self.kind == 'template' else {'body': self.body or ''}
        except ValueError as exc:
            raise ValidationError(_('Invalid JSON parameters.')) from exc
        if self.kind in {'image', 'document', 'video'}:
            content = {'caption': self.body or ''}
        message = self.env['qing.whatsapp.message']._enqueue(conversation, self.kind, content,
            request_key=self.request_key, template=self.template_id if self.kind == 'template' else None,
            attachment=self.attachment_id if self.kind in {'template', 'image', 'document', 'audio', 'video'} else None)
        self._after_enqueue(message)
        return {'type': 'ir.actions.act_window', 'res_model': conversation._name,
                'res_id': conversation.id, 'view_mode': 'form', 'target': 'current'}
