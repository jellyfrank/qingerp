import io

from lxml import etree
from PIL import Image

from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.qing_base.hooks import initialize_report_print_preview


@tagged('at_install', '-post_install')
class TestQingSettings(TransactionCase):
    def test_advanced_settings_app_branding_and_position(self):
        settings_view = self.env.ref('qing_base.view_res_config_settings_form')
        arch = etree.fromstring(settings_view.get_combined_arch().encode())
        apps = arch.xpath('//form/app')
        app_names = [app.get('name') for app in apps]

        self.assertEqual(app_names.count('qing_advanced_settings'), 1)
        self.assertNotIn('mommy', app_names)
        advanced_settings = apps[app_names.index('qing_advanced_settings')]
        self.assertEqual(advanced_settings.get('string'), 'Advanced Settings')
        self.assertEqual(advanced_settings.get('data-string'), 'Advanced Settings')
        self.assertEqual(
            advanced_settings.get('logo'),
            '/qing_base/static/img/qing_logo.png',
        )
        general_settings_index = app_names.index('general_settings')
        self.assertEqual(
            app_names[general_settings_index + 1],
            'qing_advanced_settings',
        )

    def test_report_print_mode_uses_qing_preview_setting(self):
        parameters = self.env['ir.config_parameter'].sudo()
        parameters.set_bool('qing_base.report.print_preview', True)
        report = self.env['ir.actions.report'].create({
            'name': 'Qing Print Mode Test',
            'model': 'res.partner',
            'report_name': 'qing_base.report_print_mode_test',
            'report_type': 'qweb-pdf',
        })
        settings = self.env['res.config.settings'].create({})
        self.assertEqual(settings.qing_report_print_mode, 'preview')
        self.assertTrue(report.qing_print_preview_effective)

        settings.qing_report_print_mode = 'download'
        self.assertFalse(settings.qing_report_print_preview)
        settings.set_values()
        self.assertFalse(parameters.get_bool('qing_base.report.print_preview'))
        report.invalidate_recordset(['qing_print_preview_effective'])
        self.assertFalse(report.qing_print_preview_effective)
        self.assertEqual(
            self.env['res.config.settings'].create({}).qing_report_print_mode,
            'download',
        )

        report.qing_print_preview = True
        self.assertTrue(report.qing_print_preview_effective)

        settings.qing_report_print_mode = 'preview'
        settings.set_values()
        self.assertTrue(parameters.get_bool('qing_base.report.print_preview'))

    def test_report_print_mode_is_visible_in_advanced_settings(self):
        settings_view = self.env.ref('qing_base.view_res_config_settings_form')
        arch = etree.fromstring(settings_view.get_combined_arch().encode())
        setting = arch.xpath('//app[@name="qing_advanced_settings"]//setting[@id="qing_report_print_mode"]')
        self.assertEqual(len(setting), 1)
        self.assertEqual(
            setting[0].xpath('field[@name="qing_report_print_mode"]/@widget'),
            ['radio'],
        )
        password_setting = arch.xpath(
            '//app[@name="qing_advanced_settings"]//setting[@id="qing_pdf_password_read"]'
        )
        self.assertEqual(len(password_setting), 1)
        self.assertIn('Enable protection per report action', password_setting[0].get('help'))

    def test_tax_invoice_module_switches_follow_installed_state(self):
        names = ('qing_account_fapiao', 'qing_account_fapiao_ocr')
        settings_model = self.env['res.config.settings']
        classified = settings_model._get_classified_fields(
            [f'module_{name}' for name in names]
        )
        self.assertEqual(set(classified['module'].mapped('name')), set(names))

        values = settings_model.default_get([f'module_{name}' for name in names])
        for name in names:
            module = self.env['ir.module.module']._get(name)
            self.assertEqual(values[f'module_{name}'], module.state in (
                'installed', 'to install', 'to upgrade',
            ))

        arch = etree.fromstring(
            self.env.ref('qing_base.view_res_config_settings_form').get_combined_arch().encode()
        )
        for name in names:
            settings = arch.xpath(
                f'//app[@name="qing_advanced_settings"]//setting[@id="{name}"]'
            )
            self.assertEqual(len(settings), 1)
            self.assertEqual(settings[0].xpath('field/@name'), [f'module_{name}'])

    def test_aliyun_sms_switch_targets_qingerp_adapter(self):
        field_name = 'module_qing_sms_aliyun'
        settings_model = self.env['res.config.settings']
        classified = settings_model._get_classified_fields([field_name])
        self.assertEqual(
            set(classified['module'].mapped('name')),
            {'qing_sms_aliyun'},
        )

        module = self.env['ir.module.module']._get('qing_sms_aliyun')
        self.assertEqual(
            settings_model.default_get([field_name])[field_name],
            module.state in ('installed', 'to install', 'to upgrade'),
        )

        arch = etree.fromstring(
            self.env.ref('qing_base.view_res_config_settings_form').get_combined_arch().encode()
        )
        setting = arch.xpath('//setting[@id="qing_aliyun_sms"]')
        self.assertEqual(len(setting), 1)
        self.assertEqual(setting[0].xpath('field/@name'), [field_name])
        self.assertFalse(arch.xpath('//field[@name="module_mommy_aliyun_sms"]'))

    def test_general_settings_offer_wework_instead_of_barcode_lookup(self):
        field_name = 'module_qing_wework'
        settings_model = self.env['res.config.settings']
        classified = settings_model._get_classified_fields([field_name])
        self.assertEqual(set(classified['module'].mapped('name')), {'qing_wework'})

        arch = etree.fromstring(
            self.env.ref('base_setup.res_config_settings_view_form').get_combined_arch().encode()
        )
        setting = arch.xpath('//app[@name="general_settings"]//setting[@id="qing_wework"]')
        self.assertEqual(len(setting), 1)
        self.assertEqual(setting[0].xpath('field/@name'), [field_name])
        self.assertFalse(arch.xpath('//setting[@id="process_barcode_lookup_setting"]'))
        self.assertFalse(arch.xpath('//field[@name="module_product_barcodelookup"]'))

    def test_report_print_default_preserves_existing_choice(self):
        parameters = self.env['ir.config_parameter'].sudo()
        parameters.set_bool('qing_base.report.print_preview', False)
        initialize_report_print_preview(self.env)
        self.assertFalse(parameters.get_bool('qing_base.report.print_preview'))

        parameters.search([('key', '=', 'qing_base.report.print_preview')], limit=1).unlink()
        initialize_report_print_preview(self.env)
        self.assertTrue(parameters.get_bool('qing_base.report.print_preview'))


@tagged('post_install', '-at_install')
class TestQingBranding(HttpCase):
    def test_public_branding(self):
        login = self.url_open('/web/login')
        self.assertEqual(login.status_code, 200)
        self.assertIn('<title>QingERP 20.0</title>', login.text)
        self.assertIn('/qing_base/static/img/qing_logo.png', login.text)
        self.assertNotIn('/web/static/img/favicon.ico', login.text)
        self.assertIn('qing-login-layout', login.text)
        self.assertIn('qing-login-scene', login.text)
        self.assertIn('欢迎回来', login.text)
        self.assertIn('青岛欧姆网络科技 2026', login.text)
        self.assertIn('name="csrf_token"', login.text)
        self.assertIn('name="login"', login.text)
        self.assertIn('name="password"', login.text)
        manifest = self.url_open('/web/manifest.webmanifest').json()
        self.assertEqual(manifest['name'], 'QingERP')
        self.assertEqual(manifest['short_name'], 'QingERP')
        self.assertEqual(manifest['start_url'], '/odoo')
        self.assertEqual(manifest['scope'], '/odoo')
        for icon in manifest['icons']:
            response = self.url_open(icon['src'])
            self.assertEqual(response.status_code, 200)
            image = Image.open(io.BytesIO(response.content))
            self.assertEqual(f'{image.width}x{image.height}', icon['sizes'])
        self.assertEqual(self.url_open('/qing/brand/icon/181').status_code, 404)
        offline = self.url_open('/odoo/offline')
        self.assertEqual(offline.status_code, 200)
        self.assertIn('alt="Qing"', offline.text)
        self.assertNotIn('alt="Odoo logo"', offline.text)

    def test_backend_touch_icon_and_company_logo_preserved(self):
        company_logo = self.env.company.logo.to_base64()
        self.authenticate('admin', 'admin')
        for path in ('/odoo', '/odoo?debug=assets'):
            response = self.url_open(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('/qing/brand/icon/180', response.text)
            self.assertNotIn('/web/static/img/odoo-icon-ios.png', response.text)
        response = self.url_open('/qing/brand/icon/180')
        self.assertEqual(Image.open(io.BytesIO(response.content)).size, (180, 180))
        self.assertEqual(self.env.company.logo.to_base64(), company_logo)

    def test_login_keeps_qing_layout_when_page_wrapper_is_replaced(self):
        # Same override as website.login_layout: swap the page wrapper at priority 20.
        self.env['ir.ui.view'].create({
            'name': 'Test website-like login layout',
            'type': 'qweb',
            'inherit_id': self.env.ref('web.login_layout').id,
            'priority': 20,
            'arch_db': """<data>
                <xpath expr="t[@t-call]" position="replace">
                    <t t-call="web.frontend_layout"><div class="o_test_site_wrapper" t-out="0"/></t>
                </xpath>
            </data>""",
        })
        response = self.url_open('/web/login')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('o_test_site_wrapper', response.text)
        for marker in ('qing-login-layout', 'qing-login-scene', '/qing_base/static/img/qing_logo.png',
                       '欢迎回来', 'name="password"'):
            self.assertIn(marker, response.text)
        self.env['ir.config_parameter'].sudo().set_str('auth_signup.reset_password', 'True')
        reset = self.url_open('/web/reset_password')
        self.assertEqual(reset.status_code, 200)
        self.assertIn('/qing_base/static/img/qing_logo.png', reset.text)
        self.assertNotIn('qing-login-scene', reset.text)

    def test_login_with_another_footer_extension(self):
        self.env['ir.ui.view'].create({
            'name': 'Test third-party login footer',
            'type': 'qweb',
            'inherit_id': self.env.ref('web.login_layout').id,
            'priority': 17,
            'arch_db': """<data>
                <xpath expr="//div[hasclass('card-body')]/div[hasclass('small', 'border-top')]" position="replace">
                    <div class="text-center small border-top">Custom footer</div>
                </xpath>
            </data>""",
        })
        response = self.url_open('/web/login')
        self.assertEqual(response.status_code, 200)
        self.assertIn('/qing_base/static/img/qing_logo.png', response.text)
