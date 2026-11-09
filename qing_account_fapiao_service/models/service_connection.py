import uuid
from urllib.parse import urlsplit

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.addons.qing_service_client.models.instance import ServiceHTTPError


class FapiaoServiceConnection(models.Model):
    _name = 'qing.fapiao.service.connection'
    _description = 'Qing Invoice Service Connection'
    _check_company_auto = True
    _order = 'company_id, seller_tax_id, id'
    _rec_name = 'company_id'

    _connection_unique = models.Constraint(
        'UNIQUE(company_id, seller_tax_id)',
        'Only one cloud invoice connection is allowed per company and seller tax ID.',
    )

    def _validate_endpoint(self):
        self.ensure_one()
        instance = self.env['qing.service.instance'].sudo()._local_instance()
        try:
            parts = urlsplit((self.sudo().service_url or instance.endpoint).strip())
        except ValueError as error:
            raise ValidationError(_('The invoice service endpoint is invalid.')) from error
        if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
                or parts.query or parts.fragment):
            raise ValidationError(_('The invoice service endpoint must be an HTTPS base URL without credentials, query, or fragment.'))
        return parts.geturl().rstrip('/')

    def _request_service(self, path, payload=None, method=None):
        self.ensure_one()
        instance = self.env['qing.service.instance'].sudo()._local_instance()
        if self._validate_endpoint() != instance.endpoint.rstrip('/'):
            raise UserError(_('The invoice service endpoint must match the registered online service center.'))
        headers = {}
        if self.service_client_id:
            headers['X-Qing-Client-Uid'] = self.service_client_id
        return instance._request(path, payload, method=method, headers=headers,
                                 max_body_bytes=16 * 1024 * 1024,
                                 max_response_bytes=32 * 1024 * 1024)

    def _application_notification(self):
        self.ensure_one()
        messages = {
            'pending': _('Application submitted successfully. Please wait for service approval.'),
            'sandbox': _('Application synchronized. Sandbox access is enabled; production invoicing is not enabled.'),
            'production': _('Application synchronized. Production invoicing is enabled.'),
            'rejected': _('Application synchronized. Your application was rejected; please contact the service administrator.'),
            'suspended': _('Application synchronized. Your access is suspended; please contact the service administrator.'),
        }
        action = self._qing_notification_action(
            _('Online Invoicing Settings'),
            notification_type='warning' if self.service_state in ('rejected', 'suspended') else 'success',
            sticky=True,
        )
        action['params'].update({
            'message': messages[self.service_state],
            'next': {
                'type': 'ir.actions.act_window',
                'name': _('Online Invoicing Settings'),
                'res_model': self._name,
                'res_id': self.id,
                'views': [(False, 'form')],
                'target': 'current',
            },
        })
        return action

    def action_apply(self):
        self.check_access('write')
        self.ensure_one()
        for connection in self:
            connection = connection.sudo()
            connection._validate_endpoint()
            instance = self.env['qing.service.instance'].sudo()._local_instance()
            if not instance._ensure_not_clone():
                return False
            if instance.state != 'active':
                raise UserError(_('Register the QingERP instance and wait for online service approval before applying for invoicing.'))
            if connection._validate_endpoint() != instance.endpoint.rstrip('/'):
                raise UserError(_('The invoice service endpoint must match the registered online service center.'))
            payload = {
                'company_name': connection.company_id.name,
                'seller_name': connection.seller_name.strip(),
                'seller_tax_id': connection.seller_tax_id.strip(),
                'client_version': '20.0',
                'connection_uid': connection.connection_uid,
            }
            if connection.service_client_id:
                payload['client_id'] = connection.service_client_id
                if connection.access_token and not connection.unified_auth:
                    payload['legacy_token'] = connection.access_token
            result = connection._request_service('/api/v1/service/fapiao/applications', payload)
            if not result.get('client_id') or result.get('state') not in ('pending', 'sandbox', 'production', 'rejected', 'suspended'):
                raise UserError(_('The invoice service returned an incomplete application response.'))
            connection._write_service_state({
                'service_client_id': result['client_id'],
                'service_state': result['state'],
                'registration_editable': (
                    result.get('registration_editable') is True
                    and result['state'] == 'pending'
                ),
                'last_sync_at': fields.Datetime.now(),
                'unified_auth': True,
            })
        return self._application_notification()

    def action_sync_status(self):
        self.check_access('write')
        for connection in self:
            connection = connection.sudo()
            if not connection.service_client_id:
                return connection.action_apply()
            result = connection._request_service('/api/v1/service/fapiao/status', method='GET')
            state = result.get('state')
            if state not in ('pending', 'sandbox', 'production', 'rejected', 'suspended'):
                raise UserError(_('The invoice service returned an unknown access state.'))
            if result.get('client_id') != connection.service_client_id:
                raise UserError(_('The invoice service returned a different client identity.'))
            connection._write_service_state({
                'service_state': state,
                'registration_editable': (
                    result.get('registration_editable') is True and state == 'pending'
                ),
                'last_sync_at': fields.Datetime.now(),
                'unified_auth': True,
            })
        return True

    def action_rotate_access_token(self):
        self.check_access('write')
        self.ensure_one()
        instance = self.env['qing.service.instance'].sudo()._local_instance()
        instance.action_rotate_access_token()
        self.sudo().search([])._write_service_state({'credential_rotation_pending': True})
        return True

    def action_confirm_access_token_rotation(self):
        self.check_access('write')
        self.ensure_one()
        instance = self.env['qing.service.instance'].sudo()._local_instance()
        instance.action_confirm_access_token_rotation()
        self.sudo().search([])._write_service_state({'credential_rotation_pending': False,
                                   'credential_updated_at': fields.Datetime.now()})
        return True

    @api.constrains('service_url', 'seller_tax_id', 'seller_name')
    def _check_connection_identity(self):
        for connection in self:
            if connection.sudo().service_url:
                connection._validate_endpoint()
            if not connection.seller_name.strip() or not connection.seller_tax_id.strip():
                raise ValidationError(_('Set the seller name and exact seller tax ID before applying.'))

    @api.model_create_multi
    def create(self, vals_list):
        self.check_access('create')
        protected = {'unified_auth', 'connection_uid', 'access_token', 'service_client_id', 'service_state', 'last_sync_at',
                     'credential_rotation_pending', 'credential_updated_at',
                     'registration_editable'}
        if any(protected.intersection(values) for values in vals_list):
            raise AccessError(_('Service credentials and activation status are managed by the application workflow.'))
        if any('service_url' in values for values in vals_list) and not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Only a system administrator can configure the hosted service endpoint.'))
        allowed_company_ids = set(self.env.companies.ids)
        for values in vals_list:
            company_id = values.get('company_id') or self.env.context.get('default_company_id') or self.env.company.id
            if int(company_id) not in allowed_company_ids:
                raise AccessError(_('You cannot create a service connection for a company outside your allowed companies.'))
        values_list = []
        for values in vals_list:
            normalized = dict(values)
            if normalized.get('seller_name'):
                normalized['seller_name'] = normalized['seller_name'].strip()
            if normalized.get('seller_tax_id'):
                normalized['seller_tax_id'] = normalized['seller_tax_id'].strip()
            normalized['connection_uid'] = str(uuid.uuid4())
            values_list.append(normalized)
        connections = super(FapiaoServiceConnection, self.sudo()).create(values_list)
        connections._check_company()
        return connections.with_env(self.env)

    def _write_service_state(self, vals):
        return super(FapiaoServiceConnection, self).write(vals)

    def write(self, vals):
        vals = dict(vals)
        if vals.get('seller_name'):
            vals['seller_name'] = vals['seller_name'].strip()
        if vals.get('seller_tax_id'):
            vals['seller_tax_id'] = vals['seller_tax_id'].strip()
        protected = {'unified_auth', 'connection_uid', 'access_token', 'service_client_id', 'service_state', 'last_sync_at',
                     'credential_rotation_pending', 'credential_updated_at',
                     'registration_editable'}
        if protected.intersection(vals):
            raise AccessError(_('Service credentials and activation status are managed by the application workflow.'))
        if 'service_url' in vals and not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Only a system administrator can configure the hosted service endpoint.'))
        if 'company_id' in vals and int(vals['company_id']) not in set(self.env.companies.ids):
            raise AccessError(_('You cannot move a service connection to a company outside your allowed companies.'))
        identity = {'company_id', 'seller_name', 'seller_tax_id'}
        if identity.intersection(vals) and any(
                connection.service_client_id and not connection.registration_editable
                for connection in self):
            raise UserError(_('An identity already submitted to the hosted service cannot be changed. Create a new connection for another seller identity.'))
        if 'service_url' in vals and any(connection.service_client_id for connection in self):
            raise UserError(_('The service endpoint is frozen after application so pending tasks keep querying the same service.'))
        return super().write(vals)

    def action_open_provider_account(self):
        self.ensure_one()
        self.check_access('read')
        if self.service_state not in ('sandbox', 'production'):
            raise UserError(_('Wait until the service operator activates this connection.'))
        environment = 'test' if self.service_state == 'sandbox' else 'production'
        Provider = self.env['qing.fapiao.provider']
        provider = Provider.search([
            ('company_id', '=', self.company_id.id),
            ('seller_tax_id', '=', self.seller_tax_id),
            ('code', '=', 'qingerp_cloud'),
            ('environment', '=', environment),
        ], limit=1)
        if not provider:
            label = _('Sandbox') if environment == 'test' else _('Production')
            provider = Provider.create({
                'name': '%s - QingERP %s' % (self.seller_name, label),
                'company_id': self.company_id.id,
                'seller_tax_id': self.seller_tax_id,
                'code': 'qingerp_cloud',
                'environment': environment,
                'enabled': True,
            })
        return {'type': 'ir.actions.act_window', 'res_model': 'qing.fapiao.provider',
                'res_id': provider.id, 'view_mode': 'form', 'target': 'current'}

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company,
                                 index=True)
    seller_name = fields.Char(required=True)
    seller_tax_id = fields.Char(required=True, index=True)
    service_url = fields.Char(help='HTTPS base URL supplied by QingERP. Only system administrators can change it.',
                              groups='base.group_system')
    unified_auth = fields.Boolean(readonly=True, copy=False)
    connection_uid = fields.Char(default=lambda self: str(uuid.uuid4()), required=True, readonly=True, copy=False)
    access_token = fields.Char(copy=False, groups='base.group_system')
    service_client_id = fields.Char(readonly=True, copy=False, index=True)
    service_state = fields.Selection([
        ('pending', 'Pending Review'), ('sandbox', 'Sandbox Enabled'), ('production', 'Production Enabled'),
        ('rejected', 'Registration Rejected'), ('suspended', 'Suspended'),
    ], readonly=True, copy=False)
    last_sync_at = fields.Datetime(readonly=True, copy=False)
    credential_rotation_pending = fields.Boolean(readonly=True, copy=False)
    credential_updated_at = fields.Datetime(readonly=True, copy=False)
    registration_editable = fields.Boolean(readonly=True, copy=False)
