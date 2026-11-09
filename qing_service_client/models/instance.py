import json
import secrets
import ssl
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ServiceHTTPError(UserError):
    def __init__(self, status_code):
        super().__init__(_('The online service rejected the request (HTTP %s).') % status_code)
        self.status_code = status_code


class QingServiceInstance(models.Model):
    _name = 'qing.service.instance'
    _description = 'QingERP Online Service Instance'

    _uid_unique = models.Constraint('UNIQUE(instance_uid)', 'Instance identifier must be unique.')

    name = fields.Char(default='QingERP Online Services', required=True)
    instance_uid = fields.Char(readonly=True, copy=False, required=True)
    instance_token = fields.Char(readonly=True, copy=False, groups='base.group_system')
    database_uuid = fields.Char(readonly=True, copy=False)
    organization_name = fields.Char(required=True)
    organization_tax_id = fields.Char()
    system_url = fields.Char(required=True)
    contact_email = fields.Char()
    endpoint = fields.Char(default='https://service.qingsolution.com', required=True, groups='base.group_system')
    state = fields.Selection([
        ('local', 'Not submitted'), ('pending', 'Awaiting review'),
        ('active', 'Active'), ('suspended', 'Suspended'),
    ], default='local', readonly=True, required=True)
    service_instance_uid = fields.Char(readonly=True, copy=False)
    consent_at = fields.Datetime(readonly=True, copy=False)
    last_sync_at = fields.Datetime(readonly=True, copy=False)
    subscription_summary = fields.Text(readonly=True)
    credential_rotation_pending = fields.Boolean(readonly=True, copy=False)
    credential_updated_at = fields.Datetime(readonly=True, copy=False)

    @api.model
    def _current_database_uuid(self):
        return self.env['ir.config_parameter'].sudo().get_str('database.uuid') or ''

    @api.model
    def _local_instance(self):
        instance = self.sudo().search([], limit=1)
        if not instance:
            parameters = self.env['ir.config_parameter'].sudo()
            current_uuid = self._current_database_uuid()
            telemetry_uuid = parameters.get_str('qing_base.telemetry.database_uuid', default='')
            telemetry_uid = parameters.get_str('qing_base.telemetry.instance_uid', default='')
            telemetry_token = parameters.get_str('qing_base.telemetry.instance_token', default='')
            reuse_telemetry = telemetry_uuid == current_uuid and telemetry_uid and telemetry_token
            instance = self.sudo().create({
                'instance_uid': telemetry_uid if reuse_telemetry else str(uuid.uuid4()),
                'instance_token': telemetry_token if reuse_telemetry else secrets.token_urlsafe(48),
                'database_uuid': current_uuid,
                'organization_name': self.env.company.name,
                'organization_tax_id': self.env.company.vat or False,
                'system_url': self.env['ir.config_parameter'].sudo().get_str('web.base.url') or 'https://localhost',
            })
        if instance.database_uuid == self._current_database_uuid():
            self.env['ir.config_parameter'].sudo()._qing_telemetry_store_identity(
                instance.instance_uid, instance.instance_token, instance.database_uuid
            )
        return instance

    def _check_admin(self):
        if not self.env.su and not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Only a system administrator can register online services.'))

    @api.constrains('endpoint')
    def _check_urls(self):
        for instance in self:
            parts = urlsplit((instance.endpoint or '').strip())
            if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.fragment:
                raise ValidationError(_('The online service endpoint must be an HTTPS URL without credentials.'))

    @api.model_create_multi
    def create(self, vals_list):
        self._check_admin()
        if len(vals_list) != 1 or self.sudo().search_count([]):
            raise ValidationError(_('Only one online service identity is allowed per database.'))
        return super().create(vals_list)

    def write(self, vals):
        self._check_admin()
        protected = {'instance_uid', 'instance_token', 'database_uuid', 'service_instance_uid',
                     'state', 'consent_at', 'last_sync_at', 'subscription_summary',
                     'credential_rotation_pending', 'credential_updated_at'}
        if protected.intersection(vals):
            raise AccessError(_('Use registration actions to change online service status.'))
        if {'organization_name', 'organization_tax_id', 'system_url', 'endpoint'}.intersection(vals) and any(
            record.state != 'local' for record in self
        ):
            raise UserError(_('Contact the service operator before changing a registered identity.'))
        return super().write(vals)

    def _write_status(self, vals):
        return super(QingServiceInstance, self).write(vals)

    def _ensure_not_clone(self):
        self.ensure_one()
        current = self._current_database_uuid()
        if current != self.database_uuid:
            self._write_status({
                'instance_uid': str(uuid.uuid4()), 'instance_token': secrets.token_urlsafe(48),
                'database_uuid': current, 'state': 'local', 'service_instance_uid': False,
                'consent_at': False, 'last_sync_at': False, 'subscription_summary': False,
                'credential_rotation_pending': False, 'credential_updated_at': False,
            })
            self.env['ir.config_parameter'].sudo()._qing_telemetry_store_identity(
                self.instance_uid, self.instance_token, current
            )
            return False
        return True

    def _request(self, path, payload=None, *, method=None, headers=None,
                 max_body_bytes=16384, max_response_bytes=65536):
        self.ensure_one()
        if not self._ensure_not_clone():
            raise UserError(_('Register this database as a new online service instance.'))
        parts = urlsplit(self.endpoint)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
            raise ValidationError(_('The service endpoint must use HTTPS.'))
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
        if body and len(body) > max_body_bytes:
            raise ValidationError(_('The registration payload is too large.'))
        req = Request(self.endpoint.rstrip('/') + path, data=body, headers={
            **(headers or {}),
            'X-Qing-Instance-Uid': self.instance_uid,
            'Authorization': 'Bearer ' + self.instance_token,
            'Accept': 'application/json', 'Content-Type': 'application/json',
        }, method=method or ('POST' if body is not None else 'GET'))
        try:
            opener = build_opener(_NoRedirect, HTTPSHandler(context=ssl.create_default_context()))
            with opener.open(req, timeout=15) as response:
                raw = response.read(max_response_bytes + 1)
        except HTTPError as error:
            try:
                error_payload = json.loads(error.read(max_response_bytes + 1).decode('utf-8'))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                error_payload = {}
            error_detail = error_payload.get('error') if isinstance(error_payload, dict) else False
            error_code = error_detail.get('code') if isinstance(error_detail, dict) else False
            if error.code == 403 and error_code == 'SUBSCRIPTION_REQUIRED':
                raise UserError(_(
                    'An active tax classification subscription is required to synchronize this catalog.'
                )) from error
            raise ServiceHTTPError(error.code) from error
        except (URLError, OSError, TimeoutError) as error:
            raise UserError(_('The online service could not be reached; retry with the same instance identity.')) from error
        if len(raw) > max_response_bytes:
            raise UserError(_('The service response is too large.'))
        try:
            result = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise UserError(_('The service returned an invalid response.')) from error
        if not isinstance(result, dict):
            raise UserError(_('The service returned an invalid response.'))
        return result

    def action_register(self):
        self._check_admin()
        for instance in self:
            if not instance._ensure_not_clone():
                return False
            if instance.state != 'local':
                raise UserError(_('This instance is already registered. Sync its status instead.'))
            parts = urlsplit((instance.system_url or '').strip())
            if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.fragment:
                raise ValidationError(_('Set the public HTTPS system URL before registration.'))
            result = instance._request('/api/v1/service/instances', {
                'instance_uid': instance.instance_uid,
                'organization_name': instance.organization_name,
                'organization_tax_id': instance.organization_tax_id or '',
                'system_url': instance.system_url,
                'contact_email': instance.contact_email or '',
                **instance.env['ir.config_parameter']._qing_product_metadata(),
            })
            if result.get('instance_uid') != instance.instance_uid or result.get('state') not in ('pending', 'active', 'suspended'):
                raise UserError(_('The service did not acknowledge this instance registration.'))
            instance._write_status({'state': result['state'], 'service_instance_uid': result['instance_uid'],
                                    'consent_at': fields.Datetime.now(), 'last_sync_at': fields.Datetime.now()})
        return True

    def action_sync(self):
        self._check_admin()
        for instance in self:
            if not instance._ensure_not_clone():
                return False
            if instance.state == 'local':
                raise UserError(_('Register this instance before synchronizing.'))
            result = instance._request('/api/v1/service/instances/status?instance_uid=' + instance.instance_uid)
            if result.get('instance_uid') != instance.instance_uid or result.get('state') not in ('pending', 'active', 'suspended'):
                raise UserError(_('The service returned a mismatched instance status.'))
            instance._write_status({'state': result['state'], 'last_sync_at': fields.Datetime.now(),
                                    'subscription_summary': json.dumps(result.get('services', []), ensure_ascii=False)})
        return True

    def action_rotate_access_token(self):
        self._check_admin()
        self.ensure_one()
        if self.credential_rotation_pending:
            raise UserError(_('Confirm the pending credential before rotating again.'))
        next_token = secrets.token_urlsafe(48)
        result = self._request('/api/v1/service/credentials/rotate', {'new_token': next_token})
        if result.get('state') != 'pending':
            raise UserError(_('The online service did not stage the replacement credential.'))
        self._write_status({'instance_token': next_token, 'credential_rotation_pending': True})
        return True

    def action_confirm_access_token_rotation(self):
        self._check_admin()
        self.ensure_one()
        if not self.credential_rotation_pending:
            return True
        result = self._request('/api/v1/service/credentials/confirm', {})
        if result.get('state') != 'confirmed':
            raise UserError(_('The online service did not confirm the replacement credential.'))
        self._write_status({'credential_rotation_pending': False,
                            'credential_updated_at': fields.Datetime.now()})
        return True
