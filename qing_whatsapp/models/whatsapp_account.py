import re
import secrets

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError
from ..services.cloud_api import CloudAPI, CloudError


class WhatsappAccount(models.Model):
    _name = 'qing.whatsapp.account'

    _description = 'WhatsApp Account'

    _inherit = ['mail.thread']

    _check_company_auto = True

    _phone_unique = models.Constraint('unique(phone_uid)', 'The phone ID is already configured.')

    _webhook_unique = models.Constraint('unique(webhook_key)', 'Webhook key must be unique.')

    def _assert_operator(self):
        self.ensure_one()
        self.check_access('read')
        if not self.env.user.has_group('qing_whatsapp.group_user') or not self.active:
            raise AccessError(_('WhatsApp access is unavailable.'))
        if self.company_id not in self.env.user.company_ids:
            raise AccessError(_('The account belongs to another company.'))
        if not self.env.user.has_group('qing_whatsapp.group_manager') and self.env.user not in self.operator_ids:
            raise AccessError(_('You are not assigned to this account.'))

    def _client(self):
        self.ensure_one()
        account = self.sudo()
        if not account.access_token:
            raise ValidationError(_('Configure the access token first.'))
        return CloudAPI(account.access_token, account.api_version, account.phone_uid)

    @api.constrains('phone_uid', 'waba_uid', 'api_version', 'operator_ids', 'company_id')
    def _check_configuration(self):
        for account in self:
            if not account.phone_uid.isdigit() or not account.waba_uid.isdigit():
                raise ValidationError(_('WABA and phone IDs must contain digits only.'))
            if not re.fullmatch(r'v\d+\.\d+', account.api_version):
                raise ValidationError(_('Use a Graph API version such as v23.0.'))
            if any(u.share or account.company_id not in u.company_ids for u in account.operator_ids):
                raise ValidationError(_('Operators must be internal users of the account company.'))

    def _compute_callback_url(self):
        for account in self:
            account.callback_url = '%s/qing_whatsapp/webhook/%s' % (account.get_base_url(), account.webhook_key)

    access_token = fields.Char(groups='qing_whatsapp.group_manager', copy=False)

    active = fields.Boolean(default=True)

    api_version = fields.Char(default='v23.0', required=True)

    app_secret = fields.Char(groups='qing_whatsapp.group_manager', copy=False)

    callback_url = fields.Char(compute='_compute_callback_url')

    catalog_uid = fields.Char(help='Meta catalog ID; used by the optional commerce integration.')

    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True, index=True)

    name = fields.Char(required=True)

    operator_ids = fields.Many2many('res.users', string='Operators')

    phone_uid = fields.Char(required=True, copy=False)

    verify_token = fields.Char(default=lambda self: secrets.token_urlsafe(32), groups='qing_whatsapp.group_manager', copy=False)

    waba_uid = fields.Char(required=True)

    webhook_key = fields.Char(default=lambda self: secrets.token_urlsafe(24), readonly=True, copy=False)

    def write(self, vals):
        if set(vals) & {'company_id', 'phone_uid', 'waba_uid'}:
            Conversation = self.env['qing.whatsapp.conversation'].sudo()
            domain = [('account_id', 'in', self.ids)]
            if Conversation.search_count(domain, limit=1):
                raise ValidationError(_('Account identity cannot change after conversations exist.'))
        result = super().write(vals)
        if set(vals) & {'operator_ids', 'active'}:
            domain = [('account_id', 'in', self.ids)]
            self.env['qing.whatsapp.conversation'].sudo().search(domain)._sync_discuss_members()
        return result

    def action_test_connection(self):
        self._assert_operator()
        if not self.env.user.has_group('qing_whatsapp.group_manager'):
            raise AccessError(_('Only a manager can test credentials.'))
        try:
            self._client().request('GET', self.phone_uid)
        except CloudError as exc:
            raise ValidationError(_('Connection failed. Provider code: %s', exc.code)) from exc
        return self._qing_notify(_('Connection successful'), notification_type='success')

    def action_sync_templates(self):
        self._assert_operator()
        if not self.env.user.has_group('qing_whatsapp.group_manager'):
            raise AccessError(_('Only a manager can synchronize templates.'))
        client, after = self._client(), None
        Template = self.env['qing.whatsapp.template'].sudo()
        for _page in range(100):
            params = {'fields': 'id,name,language,status,category,components,quality_score', 'limit': 100}
            if after:
                params['after'] = after
            try:
                data = client.request('GET', self.waba_uid + '/message_templates', params=params)
            except CloudError as exc:
                raise ValidationError(_('Template sync failed. Provider code: %s', exc.code)) from exc
            for item in data.get('data', []):
                Template._sync_remote(self, item)
            paging = data.get('paging', {})
            after = paging.get('cursors', {}).get('after') if paging.get('next') else None
            if not after:
                break
        if after:
            raise ValidationError(_('Template pagination limit reached. Please contact support.'))
        return True
