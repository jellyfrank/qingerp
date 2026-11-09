from datetime import timedelta
from odoo.tools.binary import BinaryBytes
from odoo.tools import file_open
import phonenumbers
from psycopg2 import IntegrityError

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

CONVERSATION_STATES = [('open', 'Open'), ('closed', 'Closed')]


class WhatsappConversation(models.Model):
    _name = 'qing.whatsapp.conversation'

    _description = 'WhatsApp Conversation'

    _inherit = ['mail.thread', 'mail.activity.mixin']

    _check_company_auto = True

    _order = 'write_date desc'

    _identity_unique = models.Constraint('unique(account_id, number)', 'This conversation already exists.')

    @api.model
    def _normalize_number(self, number):
        try:
            value = phonenumbers.parse('+' + (number or '').lstrip('+'), None)
            if not phonenumbers.is_valid_number(value):
                raise ValueError()
            return phonenumbers.format_number(value, phonenumbers.PhoneNumberFormat.E164).lstrip('+')
        except (ValueError, phonenumbers.NumberParseException) as exc:
            raise ValidationError(_('Enter a valid international phone number with country code.')) from exc

    @api.model
    def _find_or_create(self, account, number, partner=None):
        number = self._normalize_number(number)
        domain = [('account_id', '=', account.id), ('number', '=', number)]
        conversation = self.sudo().search(domain, limit=1)
        if conversation:
            return conversation
        values = {'account_id': account.id, 'number': number, 'name': number}
        if partner:
            partner.check_access('read')
            if partner.company_id and partner.company_id != account.company_id:
                raise AccessError(_('The contact belongs to another company.'))
            values['partner_id'] = partner.id
        try:
            with self.env.cr.savepoint():
                return self.sudo().create(values)
        except IntegrityError:
            return self.sudo().search(domain, limit=1)

    def _queue_commerce_message(self, interactive, request_key, purpose='service'):
        """Private shop-adapter API. The normal permission, window, consent and outbox rules apply."""
        self._check_sender()
        if not isinstance(interactive, dict) or interactive.get('type') not in {
            'product', 'product_list', 'catalog_message', 'flow', 'cta_url', 'button', 'list'
        }:
            raise ValidationError(_('Unsupported commerce interaction.'))
        return self.env['qing.whatsapp.message']._enqueue(
            self, 'interactive', interactive, request_key=request_key, purpose=purpose)

    @api.depends('last_customer_at')
    def _compute_window(self):
        for conversation in self:
            conversation.window_until = (conversation.last_customer_at + timedelta(hours=24)
                                         if conversation.last_customer_at else False)

    def _check_sender(self):
        self.ensure_one()
        self.check_access('read')
        self.account_id._assert_operator()

    @api.constrains('owner_id', 'partner_id', 'account_id')
    def _check_links(self):
        for conversation in self:
            if conversation.owner_id and conversation.owner_id not in conversation.account_id.operator_ids:
                raise ValidationError(_('The owner must be an account operator.'))
            if conversation.partner_id.company_id and conversation.partner_id.company_id != conversation.company_id:
                raise ValidationError(_('The contact belongs to another company.'))

    def _discuss_operators(self):
        self.ensure_one()
        account = self.sudo().account_id
        return account.operator_ids.filtered(lambda u: u.active and not u.share
            and account.active and account.company_id in u.company_ids
            and u.has_group('qing_whatsapp.group_user'))

    def _sync_discuss_members(self):
        for conversation in self.sudo():
            channel = conversation.discuss_channel_id
            if not channel:
                continue
            partners = conversation._discuss_operators().partner_id
            members = channel.channel_member_ids
            (members.filtered(lambda m: m.partner_id not in partners)).unlink()
            for partner in partners - members.exists().partner_id:
                self.env['discuss.channel.member'].sudo().create({'channel_id': channel.id, 'partner_id': partner.id})

    def _ensure_discuss_channel(self):
        self.ensure_one()
        self.env.cr.execute('SELECT id FROM qing_whatsapp_conversation WHERE id=%s FOR UPDATE', [self.id])
        self.invalidate_recordset(['discuss_channel_id'])
        if not self.discuss_channel_id:
            with file_open('qing_whatsapp/static/description/icon.png', 'rb') as icon_file:
                icon = BinaryBytes(icon_file.read())
            channel = self.env['discuss.channel'].sudo().with_context(install_mode=True).create({
                'name': 'WhatsApp · %s' % (self.partner_id.name or self.number),
                'channel_type': 'group', 'qing_conversation_id': self.id, 'image_128': icon,
                'description': _('Messages sent here are delivered to the WhatsApp customer. Use the conversation form for internal notes.'),
                'channel_member_ids': [(0, 0, {'partner_id': p.id}) for p in self._discuss_operators().partner_id]})
            self.sudo().write({'discuss_channel_id': channel.id})
        self._sync_discuss_members()
        return self.discuss_channel_id

    discuss_channel_id = fields.Many2one('discuss.channel', readonly=True, copy=False, ondelete='set null')

    account_id = fields.Many2one('qing.whatsapp.account', required=True, ondelete='restrict', index=True)

    company_id = fields.Many2one(related='account_id.company_id', store=True, index=True)

    last_customer_at = fields.Datetime(readonly=True)

    name = fields.Char(required=True)

    number = fields.Char(required=True, readonly=True, index=True)

    owner_id = fields.Many2one('res.users', tracking=True)

    partner_id = fields.Many2one('res.partner', tracking=True)

    state = fields.Selection(CONVERSATION_STATES, default='open', required=True, tracking=True)

    whatsapp_message_ids = fields.One2many('qing.whatsapp.message', 'conversation_id')

    window_until = fields.Datetime(compute='_compute_window')

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_('Create conversations through the message composer.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            if set(vals) - {'name', 'owner_id', 'state', 'partner_id'}:
                raise AccessError(_('Conversation identity and window are managed by the service.'))
            for conversation in self:
                conversation._check_sender()
        return super().write(vals)

    def action_compose(self):
        self._check_sender()
        return {'type': 'ir.actions.act_window', 'res_model': 'qing.whatsapp.compose',
                'view_mode': 'form', 'target': 'new', 'name': _('Send WhatsApp'),
                'context': {'default_conversation_id': self.id, 'default_account_id': self.account_id.id,
                            'default_number': self.number}}

    def action_open_discuss(self):
        self._check_sender()
        if self.env.user not in self._discuss_operators():
            raise AccessError(_('Assign yourself as an account operator to use its chat.'))
        channel = self._ensure_discuss_channel()
        domain = [('conversation_id', '=', self.id), ('discuss_message_id', '=', False)]
        for message in self.env['qing.whatsapp.message'].sudo().search(domain, order='id'):
            message.discuss_message_id = channel._qing_post_message(message)
        return {'type': 'ir.actions.client', 'tag': 'mail.action_discuss',
                'params': {'active_id': 'discuss.channel_%s' % channel.id}}
