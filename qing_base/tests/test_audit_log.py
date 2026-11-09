import json
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestQingAuditLog(TransactionCase):
    def _logs_for(self, record_id):
        domain = [
            ('model_name', '=', 'res.partner'),
            ('record_id', '=', record_id),
        ]
        return self.env['qing.audit.log'].sudo().search(domain, order='id')

    def test_create_write_unlink_and_sensitive_values(self):
        partner = self.env['res.partner'].create({
            'name': 'Audit Original',
            'email': 'private@example.com',
        })
        create_log = self._logs_for(partner.id).filtered(lambda log: log.operation == 'create')[-1]
        self.assertEqual(create_log.user_id.id, self.env.uid)
        self.assertEqual(json.loads(create_log.changes)['name']['new'], 'Audit Original')
        self.assertEqual(json.loads(create_log.changes)['email'], {'redacted': True})

        partner.write({'name': 'Audit Updated', 'email': 'other@example.com'})
        write_log = self._logs_for(partner.id).filtered(lambda log: log.operation == 'write')[-1]
        changes = json.loads(write_log.changes)
        self.assertEqual(changes['name'], {'old': 'Audit Original', 'new': 'Audit Updated'})
        self.assertEqual(changes['email'], {'redacted': True})
        self.assertNotIn('private@example.com', write_log.changes)

        partner_id = partner.id
        partner.unlink()
        unlink_log = self._logs_for(partner_id).filtered(lambda log: log.operation == 'unlink')[-1]
        self.assertEqual(json.loads(unlink_log.changes)['name']['old'], 'Audit Updated')

    def test_batch_write_and_sudo_identity(self):
        partners = self.env['res.partner'].create([
            {'name': 'Audit Batch A'},
            {'name': 'Audit Batch B'},
            {'name': 'Audit Batch C'},
        ])
        partners.sudo().write({'name': 'Audit Batch Updated'})
        for partner in partners:
            logs = self._logs_for(partner.id).filtered(lambda log: log.operation == 'write')
            self.assertEqual(len(logs), 1)
            self.assertTrue(logs.is_sudo)
            self.assertEqual(logs.user_id.id, self.env.uid)

    def test_savepoint_rollback_removes_audit_too(self):
        record_id = None
        try:
            with self.env.cr.savepoint():
                partner = self.env['res.partner'].create({'name': 'Audit Rolled Back'})
                record_id = partner.id
                self.assertTrue(self._logs_for(record_id))
                raise ValueError('roll back the savepoint')
        except ValueError:
            pass
        self.assertFalse(self._logs_for(record_id))

    def test_transient_model_is_not_audited(self):
        settings = self.env['res.config.settings'].create({})
        domain = [
            ('model_name', '=', 'res.config.settings'),
            ('record_id', '=', settings.id),
        ]
        self.assertFalse(self.env['qing.audit.log'].sudo().search(domain))

    def test_audit_can_be_disabled_globally(self):
        parameters = self.env['ir.config_parameter'].sudo()
        parameters.set_bool('qing_base.audit_enabled', False)
        partner = self.env['res.partner'].create({'name': 'Audit Disabled'})
        partner.write({'name': 'Still Disabled'})
        partner_id = partner.id
        partner.unlink()
        self.assertFalse(self._logs_for(partner_id))

    def test_large_batch_keeps_metadata_without_value_snapshots(self):
        partners = self.env['res.partner'].create([
            {'name': 'Audit Large %s' % index}
            for index in range(101)
        ])
        partners.write({'name': 'Audit Large Updated'})
        logs = self.env['qing.audit.log'].sudo().search([
            ('model_name', '=', 'res.partner'),
            ('record_id', 'in', partners.ids),
            ('operation', '=', 'write'),
        ])
        self.assertEqual(len(logs), len(partners))
        for log in logs:
            self.assertEqual(json.loads(log.changes)['name'], {'changed': True})

    def test_only_system_administrators_can_read_logs(self):
        partner = self.env['res.partner'].create({'name': 'Audit Permissions'})
        log = self._logs_for(partner.id)[-1]
        with self.assertRaises(AccessError):
            log.with_user(self.env.ref('base.public_user')).read(['model_name'])
        self.assertEqual(
            log.with_user(self.env.ref('base.user_admin')).read(['model_name'])[0]['model_name'],
            'res.partner',
        )
        with self.assertRaises(AccessError):
            log.with_user(self.env.ref('base.user_admin')).write({'field_names': 'tampered'})

    def test_retention_removes_only_expired_rows(self):
        old_partner = self.env['res.partner'].create({'name': 'Audit Expired'})
        recent_partner = self.env['res.partner'].create({'name': 'Audit Current'})
        old_log = self._logs_for(old_partner.id)[-1]
        recent_log = self._logs_for(recent_partner.id)[-1]
        old_date = fields.Datetime.now() - timedelta(days=8)
        self.env.cr.execute(
            'UPDATE qing_audit_log SET event_date = %s WHERE id = %s',
            (old_date, old_log.id),
        )
        cutoff = fields.Datetime.now() - timedelta(days=7)
        self.assertEqual(self.env['qing.audit.log']._purge_batch(cutoff), 1)
        self.assertFalse(old_log.exists())
        self.assertTrue(recent_log.exists())
