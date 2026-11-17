import io
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.qing_system_update.wizard import res_config_settings as module

LATEST_URL = 'https://www.qingerp.com/install/latest.json'


@tagged('post_install', '-at_install')
class TestSystemUpdate(TransactionCase):

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.updates = Path(self.tmp.name)
        (self.updates / "requests").mkdir()
        (self.updates / "status").mkdir()
        module._LATEST_CACHE.clear()
        self.env["ir.config_parameter"].sudo().set_str("qing_system_update.latest_url", LATEST_URL)
        self.addCleanup(module._LATEST_CACHE.clear)
        env_patch = patch.dict(os.environ, {
            'QINGERP_UPDATES_DIR': str(self.updates),
            'QINGERP_RELEASE_ID': '20.0.2-599340516ff3',
        })
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.latest = '20.0.3'
        urlopen = patch.object(module.urllib.request, 'urlopen', side_effect=self._urlopen)
        urlopen.start()
        self.addCleanup(urlopen.stop)

    def _urlopen(self, url, timeout=None):
        self.assertEqual(url, LATEST_URL)
        if self.latest is None:
            raise OSError('offline')
        return io.BytesIO(json.dumps({'version': self.latest, 'bundle': 'x', 'sha256': 'y'}).encode())

    def _settings(self):
        return self.env['res.config.settings'].create({})

    def test_available_update_and_request(self):
        settings = self._settings()
        self.assertEqual(settings.qing_update_current_version, '20.0.2')
        self.assertEqual(settings.qing_update_latest_version, '20.0.3')
        self.assertEqual(settings.qing_update_state, 'available')

        settings.action_qing_update_request()
        request = json.loads((self.updates / 'requests' / 'request.json').read_text())
        self.assertEqual(request['version'], '20.0.3')
        self.assertEqual(request['requested_by'], self.env.user.id)
        with self.assertRaises(UserError):
            settings.action_qing_update_request()

    def test_up_to_date_does_not_write_request(self):
        self.latest = '20.0.2'
        settings = self._settings()
        self.assertEqual(settings.qing_update_state, 'up_to_date')
        with self.assertRaises(UserError):
            settings.action_qing_update_request()
        self.assertFalse((self.updates / 'requests' / 'request.json').exists())

    def test_unsupported_without_host_updater(self):
        with patch.dict(os.environ, {'QINGERP_UPDATES_DIR': str(self.updates / 'missing')}):
            settings = self._settings()
            self.assertEqual(settings.qing_update_state, 'unsupported')
            with self.assertRaises(UserError):
                settings.action_qing_update_request()

    def test_release_site_unreachable(self):
        self.latest = None
        self.assertEqual(self._settings().qing_update_state, 'unknown')

    def test_running_and_failed_status_from_host(self):
        (self.updates / 'status' / 'status.json').write_text(json.dumps({
            'state': 'running', 'step': 'backup', 'message': 'Backing up', 'to_version': '20.0.3'}))
        settings = self._settings()
        self.assertEqual(settings.qing_update_state, 'running')
        self.assertEqual(settings.qing_update_message, 'Backing up the database.')

        (self.updates / 'status' / 'status.json').write_text(json.dumps({
            'state': 'failed', 'message': 'Restored 20.0.2', 'to_version': '20.0.3'}))
        settings = self._settings()
        self.assertEqual(settings.qing_update_state, 'failed')
        self.assertIn('Restored 20.0.2', settings.qing_update_message)
        with self.assertRaises(UserError):
            settings.action_qing_update_request()
        self.assertFalse((self.updates / 'requests' / 'request.json').exists())

    def test_only_administrators_can_request(self):
        user = new_test_user(self.env, login='qing_update_user', groups='base.group_user')
        settings = self._settings().with_user(user)
        with self.assertRaises(AccessError):
            settings.action_qing_update_request()
        self.assertFalse((self.updates / 'requests' / 'request.json').exists())

    def test_settings_view_has_upgrade_block(self):
        arch = self.env.ref('base_setup.res_config_settings_view_form').get_combined_arch()
        from lxml import etree
        root = etree.fromstring(arch.encode())
        blocks = root.xpath("//block[@name='about_setting_container']")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].get('title'), 'QingERP Version')
        self.assertEqual(len(blocks[0].xpath(".//setting[@id='qing_system_update']")), 1)
        self.assertFalse(root.xpath("//block[@name='qing_system_update']"))
        self.assertNotIn('Updates stop the service', arch)
        self.assertIn('id="qing_system_update"', arch)
        self.assertIn('name="action_qing_update_request"', arch)
