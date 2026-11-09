import json
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.qing_base.models.ir_config_parameter import TELEMETRY_KEYS


@tagged('post_install', '-at_install')
class TestMarketTelemetry(TransactionCase):
    def setUp(self):
        super().setUp()
        self.parameters = self.env['ir.config_parameter'].sudo()
        self.parameters.set_str('web.base.url', 'https://erp.example.test/private?token=secret')
        self.parameters.set_bool('database.is_neutralized', False)
        for key in ('last_attempt', 'last_success', 'last_enqueue'):
            self.parameters.set_str(TELEMETRY_KEYS[key], '')

    def test_payload_contains_only_bounded_installation_statistics(self):
        payload = self.parameters._qing_telemetry_payload('test-instance-uid')
        installed = self.env['ir.module.module'].search([('state', '=', 'installed')]).read(['name'])
        self.assertEqual(set(payload), {
            'instance_uid', 'organization_name', 'system_url',
            'installed_module_count', 'qing_module_count', 'client_version', 'enterprise_version',
        })
        self.assertEqual(payload['system_url'], 'https://erp.example.test')
        self.assertEqual(payload['installed_module_count'], len(installed))
        self.assertEqual(
            payload['client_version'],
            self.env['ir.module.module'].search([('name', '=', 'qing_base')], limit=1).latest_version,
        )
        self.assertEqual(
            payload['qing_module_count'],
            sum(row['name'].startswith('qing_') for row in installed),
        )
        self.assertNotIn('secret', json.dumps(payload))

    def test_free_metadata(self):
        if 'qing.enterprise' in self.env.registry.models:
            self.skipTest('Commercial foundation is installed.')
        self.assertEqual(self.parameters._qing_product_metadata()['enterprise_version'], '')

    def test_neutralized_or_localhost_database_never_calls_network(self):
        with patch.object(type(self.parameters), '_qing_telemetry_post') as post:
            self.parameters.set_bool('database.is_neutralized', True)
            self.assertFalse(self.parameters._cron_qing_telemetry_heartbeat())
            self.parameters.set_bool('database.is_neutralized', False)
            self.parameters.set_str('web.base.url', 'http://127.0.0.1:8069')
            self.assertFalse(self.parameters._cron_qing_telemetry_heartbeat())
        post.assert_not_called()

    def test_cron_sends_once_without_blocking_business_work(self):
        identity = ('test-instance-uid', 'test-token', 'https://service.example.test')
        with (
            patch.object(type(self.parameters), '_qing_telemetry_identity', return_value=identity),
            patch.object(type(self.parameters), '_qing_telemetry_post', return_value={
                'instance_uid': identity[0],
            }) as post,
        ):
            self.assertTrue(self.parameters._cron_qing_telemetry_heartbeat())
            self.assertFalse(self.parameters._cron_qing_telemetry_heartbeat())
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[2]['instance_uid'], identity[0])
        self.assertTrue(self.parameters.get_str(TELEMETRY_KEYS['last_success']))

    def test_fallback_identity_rotates_after_database_clone(self):
        original = self.parameters._qing_telemetry_fallback_identity()
        self.assertTrue(original[0])
        self.assertTrue(original[1])
        self.parameters.set_str('database.uuid', 'new-database-uuid')
        rotated = self.parameters._qing_telemetry_fallback_identity()
        self.assertNotEqual(rotated[0], original[0])
        self.assertNotEqual(rotated[1], original[1])

    def test_network_failure_is_non_blocking_and_rate_limited(self):
        from urllib.error import URLError

        identity = ('test-instance-uid', 'test-token', 'https://service.example.test')
        with (
            patch.object(type(self.parameters), '_qing_telemetry_identity', return_value=identity),
            patch.object(type(self.parameters), '_qing_telemetry_post', side_effect=URLError('offline')) as post,
        ):
            self.assertFalse(self.parameters._cron_qing_telemetry_heartbeat())
            self.assertFalse(self.parameters._cron_qing_telemetry_heartbeat())
        post.assert_called_once()
        self.assertFalse(self.parameters.get_str(TELEMETRY_KEYS['last_success']))
        self.assertTrue(self.parameters.get_str(TELEMETRY_KEYS['last_attempt']))

    def test_http_transport_uses_bearer_and_rejects_mismatched_response(self):
        payload = self.parameters._qing_telemetry_payload('test-instance-uid')
        target = 'odoo.addons.qing_base.models.ir_config_parameter.build_opener'
        with patch(target) as build_opener:
            response = build_opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = b'{"instance_uid":"test-instance-uid","received_at":"2026-10-03T00:00:00Z"}'
            self.parameters._qing_telemetry_post('https://service.example.test', 'test-token', payload)
            request = build_opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, 'https://service.example.test/api/v1/service/instances/heartbeat')
            self.assertEqual(request.get_header('Authorization'), 'Bearer test-token')
            self.assertEqual(request.get_header('X-qing-instance-uid'), 'test-instance-uid')
            self.assertEqual(json.loads(request.data), payload)
            response.read.return_value = b'{"instance_uid":"another-instance"}'
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                self.parameters._qing_telemetry_post('https://service.example.test', 'test-token', payload)
            response.read.return_value = b'{"instance_uid":"test-instance-uid","error":"rejected"}'
            with self.assertRaisesRegex(ValueError, 'acceptance receipt'):
                self.parameters._qing_telemetry_post('https://service.example.test', 'test-token', payload)

    def test_browser_enqueue_only_schedules_due_heartbeat(self):
        cron = self.env.ref('qing_base.ir_cron_qing_telemetry_heartbeat')
        with patch.object(type(cron), '_trigger') as trigger:
            self.assertTrue(self.parameters._qing_telemetry_enqueue_if_due())
            self.assertFalse(self.parameters._qing_telemetry_enqueue_if_due())
        trigger.assert_called_once()
