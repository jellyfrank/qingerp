from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged('post_install', '-at_install')
class TestServiceAccess(TransactionCase):

    def test_connection_access_is_company_scoped(self):
        company = self.env.company
        other_company = self.env['res.company'].create({'name': 'Other service company'})
        connection_model = self.env['qing.fapiao.service.connection']
        own = connection_model.create({
            'company_id': company.id, 'seller_name': 'Own seller', 'seller_tax_id': 'TEST-OWN',
        })
        other = connection_model.with_company(other_company).create({
            'company_id': other_company.id, 'seller_name': 'Other seller', 'seller_tax_id': 'TEST-OTHER',
        })
        for group in ('account.group_account_manager', 'base.group_system'):
            user = new_test_user(
                self.env, login='service_' + group, groups=group,
                company_id=company.id, company_ids=[(6, 0, company.ids)],
            )
            records = connection_model.with_user(user).with_context(allowed_company_ids=company.ids)
            self.assertIn(own, records.search([]))
            self.assertNotIn(other, records.search([]))
            records.browse(own.id).write({'seller_name': 'Updated seller'})
            with self.assertRaises(AccessError):
                records.browse(other.id).read(['seller_name'])
            with self.assertRaises(AccessError):
                records.browse(other.id).write({'seller_name': 'Forbidden'})
            with self.assertRaises(AccessError):
                records.browse(own.id).unlink()

    def test_application_feedback_tracks_acknowledged_state(self):
        instance = self.env['qing.service.instance'].sudo()._local_instance()
        instance._write_status({'state': 'active'})
        connection = self.env['qing.fapiao.service.connection'].create({
            'company_id': self.env.company.id,
            'seller_name': 'Test seller', 'seller_tax_id': 'TEST-NOTIFICATION',
        })
        for state in ('pending', 'sandbox', 'production', 'rejected', 'suspended'):
            with patch.object(type(connection), '_request_service', return_value={
                'client_id': 'test-feedback', 'state': state,
            }):
                action = connection.action_apply()
            self.assertEqual(connection.service_state, state)
            self.assertEqual(action['tag'], 'display_notification')
            self.assertTrue(action['params']['message'])
            self.assertEqual(action['params']['next']['res_id'], connection.id)
            self.assertEqual(action['params']['next']['type'], 'ir.actions.act_window')
            self.assertEqual(action['params']['type'],
                             'warning' if state in ('rejected', 'suspended') else 'success')

    def test_service_reset_unlocks_identity_until_next_application(self):
        instance = self.env['qing.service.instance'].sudo()._local_instance()
        instance._write_status({'state': 'active'})
        connection = self.env['qing.fapiao.service.connection'].create({
            'company_id': self.env.company.id,
            'seller_name': 'Original seller', 'seller_tax_id': 'TEST-RESET-OLD',
        })
        with self.assertRaises(AccessError):
            self.env['qing.fapiao.service.connection'].create({
                'company_id': self.env.company.id,
                'seller_name': 'Forged seller', 'seller_tax_id': 'TEST-FORGED',
                'registration_editable': True,
            })
        connection._write_service_state({
            'service_client_id': 'resettable-client', 'service_state': 'pending',
        })

        with self.assertRaises(UserError):
            connection.write({'seller_tax_id': 'TEST-RESET-NEW'})

        with patch.object(type(connection), '_request_service', return_value={
            'client_id': 'resettable-client', 'state': 'pending',
            'registration_editable': True,
        }):
            connection.action_sync_status()

        self.assertTrue(connection.registration_editable)
        connection.write({
            'seller_name': 'Updated seller', 'seller_tax_id': 'TEST-RESET-NEW',
        })
        self.assertEqual(connection.seller_name, 'Updated seller')
        self.assertEqual(connection.seller_tax_id, 'TEST-RESET-NEW')

        with patch.object(type(connection), '_request_service', return_value={
            'client_id': 'resettable-client', 'state': 'pending',
            'registration_editable': False,
        }):
            connection.action_apply()

        self.assertFalse(connection.registration_editable)
        with self.assertRaises(UserError):
            connection.write({'seller_tax_id': 'TEST-RESET-THIRD'})

    def test_request_uses_shared_instance_transport(self):
        instance = self.env['qing.service.instance'].sudo()._local_instance()
        instance._write_status({'state': 'active', 'endpoint': 'https://online.example.test'})
        connection = self.env['qing.fapiao.service.connection'].create({
            'seller_name': 'Transport seller', 'seller_tax_id': 'TRANSPORT',
        })
        self.assertFalse(connection.access_token)
        with patch.object(type(instance), '_request', return_value={
            'client_id': 'shared-client', 'state': 'pending', 'registration_editable': False,
        }) as transport:
            connection.action_apply()
        args, kwargs = transport.call_args
        self.assertEqual(args[0], '/api/v1/service/fapiao/applications')
        self.assertEqual(args[1]['connection_uid'], connection.connection_uid)
        self.assertNotIn('instance_token', args[1])
        self.assertNotIn('legacy_token', args[1])
        with patch.object(type(instance), '_request', return_value={
            'client_id': 'shared-client', 'state': 'pending', 'registration_editable': False,
        }) as transport:
            connection.action_sync_status()
        self.assertEqual(transport.call_args.kwargs['headers']['X-Qing-Client-Uid'], 'shared-client')
