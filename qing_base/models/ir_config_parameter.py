import ipaddress
import json
import logging
import secrets
import ssl
import uuid
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from odoo import api, fields, models


_logger = logging.getLogger(__name__)

TELEMETRY_ENDPOINT = 'https://service.qingsolution.com'
TELEMETRY_PATH = '/api/v1/service/instances/heartbeat'
TELEMETRY_INTERVAL = timedelta(hours=6)
TELEMETRY_RETRY_INTERVAL = timedelta(minutes=30)
TELEMETRY_ENQUEUE_INTERVAL = timedelta(minutes=15)
TELEMETRY_KEYS = {
    'uid': 'qing_base.telemetry.instance_uid',
    'token': 'qing_base.telemetry.instance_token',
    'database_uuid': 'qing_base.telemetry.database_uuid',
    'last_attempt': 'qing_base.telemetry.last_attempt',
    'last_success': 'qing_base.telemetry.last_success',
    'last_enqueue': 'qing_base.telemetry.last_enqueue',
}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class IrConfigParameter(models.Model):
    _inherit = 'ir.config_parameter'

    @api.model
    def _qing_telemetry_site_url(self):
        raw_url = self.sudo().get_str('web.base.url', default='')
        try:
            parts = urlsplit(raw_url.strip())
        except ValueError:
            return ''
        if (
            parts.scheme not in ('http', 'https')
            or not parts.hostname
            or parts.hostname.lower().rstrip('.') == 'localhost'
            or parts.hostname.lower().endswith('.localhost')
            or parts.username
            or parts.password
        ):
            return ''
        try:
            port = parts.port
        except ValueError:
            return ''
        hostname = parts.hostname.lower()
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address and (address.is_loopback or address.is_unspecified):
            return ''
        host = f'[{hostname}]' if ':' in hostname else hostname
        netloc = f'{host}:{port}' if port else host
        site_url = urlunsplit((parts.scheme, netloc, '', '', ''))
        return site_url if len(site_url) <= 1024 else ''

    @api.model
    def _qing_telemetry_recent(self, key, now, interval):
        value = self.sudo().get_str(key, default='')
        if not value:
            return False
        try:
            elapsed = now - fields.Datetime.to_datetime(value)
        except (TypeError, ValueError):
            return False
        return timedelta(0) <= elapsed < interval

    @api.model
    def _qing_telemetry_due(self, *, now=None, check_attempt=True):
        parameters = self.sudo()
        if parameters.get_bool('database.is_neutralized', default=False):
            return False
        if not self._qing_telemetry_site_url():
            return False
        now = now or fields.Datetime.now()
        if self._qing_telemetry_recent(TELEMETRY_KEYS['last_success'], now, TELEMETRY_INTERVAL):
            return False
        if check_attempt and self._qing_telemetry_recent(
            TELEMETRY_KEYS['last_attempt'], now, TELEMETRY_RETRY_INTERVAL
        ):
            return False
        return True

    @api.model
    def _qing_telemetry_store_identity(self, instance_uid, token, database_uuid):
        parameters = self.sudo()
        changed = (
            parameters.get_str(TELEMETRY_KEYS['uid'], default='') != instance_uid
            or parameters.get_str(TELEMETRY_KEYS['database_uuid'], default='') != database_uuid
        )
        for key, value in (
            ('uid', instance_uid),
            ('token', token),
            ('database_uuid', database_uuid),
        ):
            if parameters.get_str(TELEMETRY_KEYS[key], default='') != value:
                parameters.set_str(TELEMETRY_KEYS[key], value)
        if changed:
            for key in ('last_attempt', 'last_success', 'last_enqueue'):
                parameters.set_str(TELEMETRY_KEYS[key], '')

    @api.model
    def _qing_telemetry_fallback_identity(self):
        parameters = self.sudo()
        current_uuid = parameters.get_str('database.uuid', default='')
        if not current_uuid:
            return None
        stored_uuid = parameters.get_str(TELEMETRY_KEYS['database_uuid'], default='')
        instance_uid = parameters.get_str(TELEMETRY_KEYS['uid'], default='')
        token = parameters.get_str(TELEMETRY_KEYS['token'], default='')
        if stored_uuid and stored_uuid != current_uuid:
            instance_uid = str(uuid.uuid4())
            token = secrets.token_urlsafe(48)
            self._qing_telemetry_store_identity(instance_uid, token, current_uuid)
            return instance_uid, token, TELEMETRY_ENDPOINT
        if not instance_uid or not token or not stored_uuid:
            instance_uid = str(uuid.uuid4())
            token = secrets.token_urlsafe(48)
            self._qing_telemetry_store_identity(instance_uid, token, current_uuid)
        return instance_uid, token, TELEMETRY_ENDPOINT

    @api.model
    def _qing_telemetry_identity(self):
        if 'qing.service.instance' not in self.env.registry.models:
            return self._qing_telemetry_fallback_identity()
        instance = self.env['qing.service.instance'].sudo()._local_instance()
        instance._ensure_not_clone()
        self._qing_telemetry_store_identity(
            instance.instance_uid, instance.instance_token, instance.database_uuid
        )
        return instance.instance_uid, instance.instance_token, TELEMETRY_ENDPOINT

    @api.model
    def _qing_product_metadata(self):
        """Return installed product versions; commercial addons extend this hook."""
        Module = self.env['ir.module.module'].sudo()
        domain = [('name', '=', 'qing_base')]
        base = Module.search(domain, limit=1)
        return {'client_version': (base.latest_version or '20.0')[:64], 'enterprise_version': ''}

    @api.model
    def _qing_telemetry_payload(self, instance_uid):
        Module = self.env['ir.module.module'].sudo()
        installed = Module.search([('state', '=', 'installed')]).read(['name'])
        company = self.env.ref('base.main_company', raise_if_not_found=False) or self.env.company
        organization_name = company.name or ''
        if 'qing.service.instance' in self.env.registry.models:
            instance = self.env['qing.service.instance'].sudo().search([], limit=1)
            if instance and instance.state != 'local' and instance.organization_name:
                organization_name = instance.organization_name
        return {
            'instance_uid': instance_uid,
            'organization_name': organization_name[:255],
            'system_url': self._qing_telemetry_site_url(),
            'installed_module_count': len(installed),
            'qing_module_count': sum(row['name'].startswith('qing_') for row in installed),
            **self._qing_product_metadata(),
        }

    @api.model
    def _qing_telemetry_post(self, endpoint, token, payload):
        parts = urlsplit(endpoint)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.fragment:
            raise ValueError('Invalid telemetry endpoint')
        body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(body) > 4096:
            raise ValueError('Telemetry payload is too large')
        request = Request(
            endpoint.rstrip('/') + TELEMETRY_PATH,
            data=body,
            headers={
                'X-Qing-Instance-Uid': payload['instance_uid'],
                'Authorization': 'Bearer ' + token,
                'Accept': 'application/json',
                'Content-Type': 'application/json',
            },
            method='POST',
        )
        opener = build_opener(_NoRedirect, HTTPSHandler(context=ssl.create_default_context()))
        with opener.open(request, timeout=8) as response:
            raw = response.read(4097)
        if len(raw) > 4096:
            raise ValueError('Telemetry response is too large')
        result = json.loads(raw.decode('utf-8'))
        if not isinstance(result, dict) or result.get('instance_uid') != payload['instance_uid']:
            raise ValueError('Telemetry response identity mismatch')
        if not result.get('received_at') or result.get('error'):
            raise ValueError('Telemetry response missing acceptance receipt')
        return result

    @api.model
    def _cron_qing_telemetry_heartbeat(self):
        if self.sudo().get_bool('database.is_neutralized', default=False):
            return False
        identity = self._qing_telemetry_identity()
        if not identity:
            return False
        now = fields.Datetime.now()
        if not self._qing_telemetry_due(now=now):
            return False
        instance_uid, token, endpoint = identity
        payload = self._qing_telemetry_payload(instance_uid)
        self.sudo().set_str(TELEMETRY_KEYS['last_attempt'], fields.Datetime.to_string(now))
        try:
            self._qing_telemetry_post(endpoint, token, payload)
        except (HTTPError, URLError, OSError, TimeoutError, ValueError, UnicodeError) as error:
            _logger.warning('QingERP installation heartbeat failed (%s)', type(error).__name__)
            return False
        self.sudo().set_str(TELEMETRY_KEYS['last_success'], fields.Datetime.to_string(fields.Datetime.now()))
        return True

    @api.model
    def _qing_telemetry_enqueue_if_due(self):
        if self.sudo().get_bool('database.is_neutralized', default=False):
            return False
        if not self._qing_telemetry_identity():
            return False
        now = fields.Datetime.now()
        if not self._qing_telemetry_due(now=now):
            return False
        self.env.cr.execute(
            'SELECT pg_try_advisory_xact_lock(hashtext(current_database()), %s)',
            [17624815],
        )
        if not self.env.cr.fetchone()[0]:
            return False
        self.env.cr.execute(
            'SELECT value FROM ir_config_parameter WHERE key = %s',
            [TELEMETRY_KEYS['last_enqueue']],
        )
        row = self.env.cr.fetchone()
        if row and row[0]:
            try:
                elapsed = now - fields.Datetime.to_datetime(row[0])
            except (TypeError, ValueError):
                elapsed = None
            if elapsed is not None and timedelta(0) <= elapsed < TELEMETRY_ENQUEUE_INTERVAL:
                return False
        parameters = self.sudo()
        cron = self.env.ref('qing_base.ir_cron_qing_telemetry_heartbeat', raise_if_not_found=False)
        if not cron or not cron.active:
            return False
        cron.sudo()._trigger(coalesce=5)
        parameters.set_str(TELEMETRY_KEYS['last_enqueue'], fields.Datetime.to_string(now))
        return True
