import io
import json
from urllib.error import HTTPError
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestServiceInstance(TransactionCase):
    def test_online_service_interface_is_removed(self):
        for name in ('menu_qing_service_instance', 'action_qing_service_instance',
                     'view_qing_service_instance_form', 'view_qing_service_instance_list'):
            self.assertFalse(self.env.ref('qing_service_client.' + name, raise_if_not_found=False))
        self.assertTrue(self.env['qing.service.instance'].sudo()._local_instance())

    def test_install_is_local_until_admin_confirms(self):
        instance = self.env['qing.service.instance'].sudo()._local_instance()
        self.assertEqual(instance.state, 'local')
        self.assertFalse(instance.consent_at)
        self.assertTrue(instance.instance_token)
        instance.write({'system_url': 'https://erp.example.test'})
        with patch.object(type(instance), '_request', return_value={
            'instance_uid': instance.instance_uid, 'state': 'pending', 'services': [],
        }) as request:
            instance.action_register()
        self.assertEqual(request.call_count, 1)
        metadata = instance.env['ir.config_parameter']._qing_product_metadata()
        self.assertEqual(request.call_args.args[1]['enterprise_version'], metadata['enterprise_version'])
        self.assertEqual(request.call_args.args[1]['client_version'], metadata['client_version'])
        self.assertEqual(instance.state, 'pending')
        self.assertTrue(instance.consent_at)

    def test_service_identity_adopts_existing_base_telemetry_identity(self):
        instance_model = self.env['qing.service.instance'].sudo()
        instance_model.search([]).unlink()
        parameters = self.env['ir.config_parameter'].sudo()
        parameters.set_str('qing_base.telemetry.database_uuid', parameters.get_str('database.uuid'))
        parameters.set_str('qing_base.telemetry.instance_uid', 'base-telemetry-uid')
        parameters.set_str('qing_base.telemetry.instance_token', 'base-telemetry-token')
        instance = instance_model._local_instance()
        self.assertEqual(instance.instance_uid, 'base-telemetry-uid')
        self.assertEqual(instance.instance_token, 'base-telemetry-token')
        self.assertEqual(instance.state, 'local')

    def test_market_heartbeat_does_not_request_online_service_approval(self):
        instance = self.env['qing.service.instance'].sudo()._local_instance()
        parameters = self.env['ir.config_parameter'].sudo()
        parameters.set_bool('database.is_neutralized', False)
        parameters.set_str('web.base.url', 'https://erp.example.test')
        parameters.set_str('qing_base.telemetry.last_attempt', '')
        parameters.set_str('qing_base.telemetry.last_success', '')
        with patch.object(type(parameters), '_qing_telemetry_post', return_value={
            'instance_uid': instance.instance_uid,
        }) as post:
            self.assertTrue(parameters._cron_qing_telemetry_heartbeat())
        self.assertEqual(post.call_count, 1)
        self.assertEqual(instance.state, 'local')
        self.assertFalse(instance.consent_at)

    def test_clone_resets_identity_without_calling_service(self):
        instance = self.env['qing.service.instance'].sudo()._local_instance()
        original = instance.instance_uid
        instance._write_status({'database_uuid': 'database-from-other-instance', 'state': 'active'})
        with patch.object(type(instance), '_request', side_effect=AssertionError('Network request')):
            self.assertFalse(instance.action_sync())
        self.assertEqual(instance.state, 'local')
        self.assertNotEqual(instance.instance_uid, original)
        self.assertFalse(instance.consent_at)

    def test_shared_http_transport_sends_instance_bearer(self):
        import json
        instance = self.env['qing.service.instance'].sudo()._local_instance()
        with patch('odoo.addons.qing_service_client.models.instance.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = b'{"state":"pending"}'
            result = instance._request('/api/v1/service/fapiao/applications', {'seller_name': 'Seller'})
        req = opener.return_value.open.call_args.args[0]
        self.assertEqual(req.get_header('Authorization'), 'Bearer ' + instance.instance_token)
        self.assertEqual(req.get_header('X-qing-instance-uid'), instance.instance_uid)
        self.assertEqual(json.loads(req.data), {'seller_name': 'Seller'})
        self.assertEqual(result['state'], 'pending')

    def test_subscription_error_is_explained_for_online_catalog_sync(self):
        instance = self.env['qing.service.instance'].sudo()._local_instance()
        error = HTTPError(
            'https://service.example.test/api/v1/service/fapiao/tax-classifications',
            403,
            'Forbidden',
            {},
            io.BytesIO(json.dumps({'error': {'code': 'SUBSCRIPTION_REQUIRED'}}).encode()),
        )
        with patch('odoo.addons.qing_service_client.models.instance.build_opener') as build_opener:
            build_opener.return_value.open.side_effect = error
            with self.assertRaisesRegex(UserError, 'active tax classification subscription'):
                instance._request('/api/v1/service/fapiao/tax-classifications')
