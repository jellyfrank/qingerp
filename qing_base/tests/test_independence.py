import io
import json
import os
from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.modules.module import get_manifest
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged
from odoo.tools.pdf import PdfFileReader, PdfFileWriter

from odoo.addons.base.models.ir_actions_report import IrActionsReport

from odoo.addons.qing_base.hooks import LEGACY_IMPORT_MARKER, import_legacy_mommy_base_settings
from odoo.addons.qing_base.tools import mommy_base_loaded


@tagged('post_install', '-at_install')
class TestMommyIndependence(TransactionCase):
    def test_installed_qing_addons_need_no_solutions_module(self):
        modules = self.env['ir.module.module'].search([('name', '=like', 'qing\\_%'), ('state', '=', 'installed')])
        self.assertTrue(modules)
        for module in modules:
            manifest = get_manifest(module.name)
            self.assertFalse(
                [name for name in manifest.get('depends', []) if name.startswith('mommy_')],
                f'{module.name} must not depend on a Solutions module',
            )

    def test_free_addons_are_lgpl(self):
        repository = get_manifest('qing_base')['addons_path']
        names = [name for name in os.listdir(repository) if name.startswith('qing_')]
        self.assertTrue(names)
        for name in names:
            manifest = get_manifest(name)
            if manifest:
                self.assertEqual(manifest.get('license'), 'LGPL-3', name)

    def test_ip_whitelist_rules(self):
        company = self.env.company
        company.qing_login_ip_whitelist = '\n'.join([
            '192.168.1.10   # office', '10.20.0.0/16', '172.16.0.10-172.16.0.20', 'not-an-ip', '2001:db8::/32',
        ])
        allowed = ('192.168.1.10', '10.20.3.4', '172.16.0.15', '2001:db8::1')
        denied = ('192.168.1.11', '10.21.0.1', '172.16.0.21', '', 'bogus')
        for address in allowed:
            self.assertTrue(company._qing_login_ip_matches(address), address)
        for address in denied:
            self.assertFalse(company._qing_login_ip_matches(address), address)

    def test_ip_whitelist_scope(self):
        if mommy_base_loaded(self.env):
            self.skipTest('mommy_base enforces its own whitelist on this database.')
        company = self.env.company
        user = new_test_user(self.env, login='qing_ip_user', groups='base.group_user')
        portal = new_test_user(self.env, login='qing_ip_portal', groups='base.group_portal')
        self.assertTrue(company._qing_is_login_ip_allowed(user, '8.8.8.8'))
        company.write({'qing_login_ip_whitelist_enabled': True, 'qing_login_ip_whitelist': '10.0.0.0/8'})
        self.assertFalse(company._qing_is_login_ip_allowed(user, '8.8.8.8'))
        self.assertTrue(company._qing_is_login_ip_allowed(user, '10.1.2.3'))
        self.assertTrue(company._qing_is_login_ip_allowed(portal, '8.8.8.8'))
        company.qing_login_ip_whitelist_group_ids = self.env.ref('base.group_system')
        self.assertTrue(company._qing_is_login_ip_allowed(user, '8.8.8.8'))
        company.write({'qing_login_ip_whitelist_group_ids': [(5, 0, 0)], 'qing_login_ip_whitelist': ''})
        self.assertFalse(company._qing_is_login_ip_allowed(user, '10.1.2.3'))

    def test_report_preview_and_pdf_protection(self):
        if mommy_base_loaded(self.env):
            self.skipTest('mommy_base handles report preview and PDF passwords on this database.')
        parameters = self.env['ir.config_parameter'].sudo()
        parameters.set_bool('qing_base.report.print_preview', False)
        report = self.env['ir.actions.report'].create({
            'name': 'Qing Protection Test', 'model': 'res.partner',
            'report_name': 'qing_base.report_protection_test', 'report_type': 'qweb-pdf',
        })
        self.assertFalse(report.qing_print_preview_effective)
        report.qing_print_preview = True
        self.assertTrue(report.qing_print_preview_effective)
        self.assertIn('qing_print_preview_effective', report._get_action_dict())

        self.assertEqual(report._qing_protect_pdf(b'%PDF'), b'%PDF')
        report.qing_pdf_password_enabled = True
        with self.assertRaises(UserError):
            report._qing_protect_pdf(b'%PDF')
        parameters.set_str('qing_base.pdf_password_read', 'secret-read')

        def fake_qpdf(arguments, **_kwargs):
            argument_file = arguments[1][1:]
            with open(argument_file, encoding='utf-8') as handle:
                self.assertIn('secret-read', handle.read())
            self.assertNotIn('secret-read', ' '.join(arguments))
            with open(arguments[3], 'wb') as target:
                target.write(b'%PDF-encrypted')

        with patch('odoo.addons.qing_base.models.ir_actions_report.subprocess.run', side_effect=fake_qpdf):
            self.assertEqual(report._qing_protect_pdf(b'%PDF'), b'%PDF-encrypted')

    def test_pdf_appendices(self):
        if mommy_base_loaded(self.env):
            self.skipTest('mommy_base merges PDF appendices on this database.')

        def blank_pdf(pages):
            writer = PdfFileWriter()
            for _page in range(pages):
                writer.add_blank_page(width=500, height=400)
            with io.BytesIO() as buffer:
                writer.write(buffer)
                return buffer.getvalue()

        report = self.env['ir.actions.report'].create({
            'name': 'Qing Appendix Test', 'model': 'ir.attachment',
            'report_name': 'qing_base.report_appendix_test', 'report_type': 'qweb-pdf',
            'qing_pdf_appendix_fields': 'raw',
        })
        with self.assertRaises(ValidationError):
            report.qing_pdf_appendix_fields = 'name'
        appendix = self.env['ir.attachment'].create({
            'name': 'appendix.pdf', 'raw': blank_pdf(2), 'mimetype': 'application/pdf',
        })

        def render(strict=True):
            with patch.object(IrActionsReport, '_render_qweb_pdf', return_value=(blank_pdf(1), 'pdf')):
                content, _kind = report.with_context(report_pdf_strict_merge=strict)._render_qweb_pdf(
                    report.id, res_ids=appendix.ids,
                )
            return len(PdfFileReader(io.BytesIO(content), strict=False).pages)

        self.assertEqual(render(), 3)
        self.assertEqual(report._qing_pdf_appendix_field_names(), ['raw'])
        self.assertEqual(report._qing_print_signature()[0], 'raw')
        appendix.raw = b'not a PDF'
        with self.assertRaisesRegex(UserError, 'incomplete report was not delivered'):
            render()
        self.assertEqual(render(strict=False), 1)

    def test_legacy_settings_import_runs_once(self):
        parameters = self.env['ir.config_parameter'].sudo()
        parameters.search([('key', '=', LEGACY_IMPORT_MARKER)]).unlink()
        parameters.set_str('qing_base.title', 'Odoo')
        parameters.set_str('mommy.title', 'Imported Title')
        self.assertTrue(import_legacy_mommy_base_settings(self.env))
        self.assertEqual(parameters.get_str('qing_base.title'), 'Imported Title')
        parameters.set_str('mommy.title', 'Changed Later')
        self.assertFalse(import_legacy_mommy_base_settings(self.env))
        self.assertEqual(parameters.get_str('qing_base.title'), 'Imported Title')

    def test_action_helpers(self):
        partners = self.env['res.partner'].create([{'name': 'Qing A'}, {'name': 'Qing B'}])
        single = partners[0]._qing_view_action('base.action_partner_form', 'base.view_partner_form')
        self.assertEqual(single['res_id'], partners[0].id)
        self.assertEqual(single['views'], [(self.env.ref('base.view_partner_form').id, 'form')])
        several = partners._qing_view_action('base.action_partner_form')
        self.assertEqual(several['domain'], [('id', 'in', partners.ids)])
        message = partners._qing_message_action('Title', 'Body')
        self.assertEqual(message['tag'], 'display_notification')
        self.assertTrue(message['params']['sticky'])


@tagged('post_install', '-at_install')
class TestLoginSecurity(HttpCase):
    def _authenticate(self, password='admin'):
        payload = {'jsonrpc': '2.0', 'method': 'call', 'id': 1,
                   'params': {'db': self.env.cr.dbname, 'login': 'admin', 'password': password}}
        response = self.url_open('/web/session/authenticate', data=json.dumps(payload),
                                 headers={'Content-Type': 'application/json'})
        return response.json()

    def test_whitelist_blocks_and_logs(self):
        if mommy_base_loaded(self.env):
            self.skipTest('mommy_base enforces its own whitelist on this database.')
        Log = self.env['qing.login.log']
        Users = type(self.env['res.users'])
        admin = self.env.ref('base.user_admin')
        admin.company_id.write({'qing_login_ip_whitelist_enabled': True, 'qing_login_ip_whitelist': '10.9.9.9'})
        # A refused request rolls back, and HttpCase nests every cursor in one
        # transaction, so capture the separate-transaction log call itself.
        with patch.object(Users, '_qing_record_login', autospec=True) as record:
            self.assertIn('error', self._authenticate())
        _users, credential, user_id, reason = record.call_args.args
        self.assertEqual(credential['login'], 'admin')
        self.assertFalse(user_id)
        self.assertIn('IP address', reason)

        admin.company_id.qing_login_ip_whitelist = '127.0.0.1'
        self.assertNotIn('error', self._authenticate())
        succeeded = Log.search([('login', '=', 'admin')], order='id desc', limit=1)
        self.assertTrue(succeeded.success)
        self.assertEqual(succeeded.user_id, admin)
        self.assertEqual(succeeded.ip_address, '127.0.0.1')
