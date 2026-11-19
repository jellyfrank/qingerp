import json
import logging
import os
import time
import tempfile
import urllib.request
from pathlib import Path

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..release_version import version_key

_logger = logging.getLogger(__name__)

LATEST_URL = 'https://www.qingerp.com/install/latest.json'
UPDATES_DIR = '/var/lib/qingerp-updates'
CACHE_SECONDS = 3600
UPDATE_STATES = [
    ('unsupported', 'Managed Elsewhere'),
    ('unknown', 'Unknown'),
    ('up_to_date', 'Up to Date'),
    ('available', 'Update Available'),
    ('requested', 'Upgrade Requested'),
    ('running', 'Upgrading'),
    ('failed', 'Last Upgrade Failed'),
]
# Per-process cache of the release site answer: {url: (monotonic time, version)}.
_LATEST_CACHE = {}


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    @api.model
    def _qing_update_dir(self):
        """Directory shared with the host updater, or None when there is none."""
        path = Path(os.environ.get('QINGERP_UPDATES_DIR', UPDATES_DIR))
        return path if path.is_dir() else None

    @api.model
    def _qing_requests_open(self):
        """Whether the host accepts web requests: its request inbox is writable."""
        directory = self._qing_update_dir()
        return bool(directory) and os.access(directory / 'requests', os.W_OK)

    @api.model
    def _qing_current_version(self):
        version = os.environ.get('QINGERP_RELEASE_ID', '').split('-', 1)[0]
        return version if version_key(version) else ''

    @api.model
    def _qing_latest_version(self, force=False):
        url = self.env['ir.config_parameter'].sudo().get_str('qing_system_update.latest_url') or LATEST_URL
        cached = _LATEST_CACHE.get(url)
        if cached and not force and time.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]
        version = ''
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                data = json.loads(response.read(65536))
            if isinstance(data, dict) and version_key(data.get('version')):
                version = data['version']
        except (OSError, ValueError) as error:
            _logger.info('Cannot read the latest QingERP release from %s: %s', url, error)
        _LATEST_CACHE[url] = (time.monotonic(), version)
        return version

    @api.model
    def _qing_read_status(self, directory):
        try:
            data = json.loads((directory / 'status' / 'status.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @api.model
    def _qing_step_labels(self):
        _ = self.env._
        return {
            'download': _('Downloading and verifying the new release.'),
            'pull': _('Downloading the new images.'),
            'backup': _('Backing up the database.'),
            'migrate': _('Upgrading the database while QingERP is stopped.'),
            'restore': _('Restoring the database backup.'),
            'switch': _('Switching to the new release; QingERP restarts briefly.'),
            'rollback': _('The update needs administrator attention.'),
        }

    @api.model
    def _qing_update_values(self, force=False):
        current = self._qing_current_version()
        directory = self._qing_update_dir()
        values = {
            'qing_update_current_version': current,
            'qing_update_latest_version': '',
            'qing_update_message': '',
        }
        if not directory or not current:
            return dict(values, qing_update_state='unsupported', qing_update_message=self.env._(
                'Upgrades of this installation are managed by your hosting platform or server administrator.'))
        status = self._qing_read_status(directory)
        host_managed = os.environ.get('QINGERP_UPDATES_HOST_MANAGED') == '1'
        if host_managed:
            latest = status.get('to_version', '')
            latest = latest if version_key(latest) else ''
        else:
            latest = self._qing_latest_version(force=force)
        values['qing_update_latest_version'] = latest
        if (directory / 'requests' / 'request.json').exists():
            return dict(values, qing_update_state='requested', qing_update_message=self.env._(
                'Waiting for the server to start the upgrade.'))
        if status.get('state') == 'running':
            message = self._qing_step_labels().get(status.get('step'), status.get('message') or '')
            return dict(values, qing_update_state='running', qing_update_message=message)
        if not latest:
            if host_managed and status.get('state') == 'failed':
                # The host updater reports its own failures without a target.
                return dict(values, qing_update_state='failed', qing_update_message=self.env._(
                    'The last upgrade did not complete. Details: %s', status.get('message') or '-'))
            return dict(values, qing_update_state='unknown', qing_update_message=self.env._(
                'Cannot reach the QingERP release site. Try again later.'))
        if version_key(latest) > version_key(current):
            if status.get('state') == 'failed' and status.get('to_version') == latest:
                return dict(values, qing_update_state='failed', qing_update_message=self.env._(
                    'The last upgrade did not complete. Details: %s', status.get('message') or '-'))
            return dict(values, qing_update_state='available')
        return dict(values, qing_update_state='up_to_date')

    def _compute_qing_update(self):
        values = self._qing_update_values()
        values['qing_update_host_managed'] = os.environ.get('QINGERP_UPDATES_HOST_MANAGED') == '1'
        values['qing_update_can_request'] = self._qing_requests_open()
        for settings in self:
            settings.update(values)

    qing_update_current_version = fields.Char(
        string='Installed Version', compute='_compute_qing_update')
    qing_update_host_managed = fields.Boolean(compute='_compute_qing_update')
    qing_update_can_request = fields.Boolean(compute='_compute_qing_update')
    qing_update_latest_version = fields.Char(
        string='Latest Version', compute='_compute_qing_update')
    qing_update_message = fields.Char(
        string='Update Message', compute='_compute_qing_update')
    qing_update_state = fields.Selection(
        UPDATE_STATES, string='Update Status', compute='_compute_qing_update')

    def _qing_check_admin(self):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._('Only administrators can upgrade QingERP.'))

    def action_qing_update_check(self):
        self._qing_check_admin()
        if os.environ.get('QINGERP_UPDATES_HOST_MANAGED') != '1':
            self._qing_latest_version(force=True)
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_qing_update_request(self):
        """Publish one complete request; the host verifies the signed release."""
        self._qing_check_admin()
        # A host that only applies its own schedule mounts the inbox read-only.
        if self._qing_update_dir() and not self._qing_requests_open():
            raise AccessError(self.env._('The host manages upgrades for this installation.'))
        values = self._qing_update_values(force=True)
        if values.get('qing_update_state') != 'available':
            raise UserError(self.env._('No update is available or an earlier update needs attention.'))
        directory = self._qing_update_dir() / 'requests'
        request = directory / 'request.json'
        staged = None
        try:
            fd, filename = tempfile.mkstemp(prefix='.request-', dir=directory)
            staged = Path(filename)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                os.fchmod(stream.fileno(), 0o660)
                json.dump({'version': values['qing_update_latest_version'],
                           'requested_by': self.env.user.id}, stream)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            # A hard link publishes only fully written content and refuses to
            # overwrite a request from another administrator.
            os.link(staged, request, follow_symlinks=False)
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except FileExistsError as error:
            raise UserError(self.env._('An update has already been requested.')) from error
        except OSError as error:
            _logger.warning('Could not submit QingERP update request: %s', error)
            raise UserError(self.env._('The update request could not be sent to the server.')) from error
        finally:
            if staged:
                staged.unlink(missing_ok=True)
        return {'type': 'ir.actions.client', 'tag': 'reload'}
