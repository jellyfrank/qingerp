from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestChinaConfiguration(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env['res.company'].with_context(chart_template_load=True).create({
            'name': 'Qing CN configuration test', 'country_id': cls.env.ref('base.cn').id,
            'currency_id': cls.env.ref('base.CNY').id,
        })
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=company.ids))
        cls.company = company.with_env(cls.env)
        cls.env['account.chart.template'].try_loading('cn', cls.company, install_demo=False)
        cls.manager = new_test_user(cls.env, login='cn_manager', groups='account.group_account_manager',
                                    company_id=company.id, company_ids=[(6, 0, company.ids)])
        cls.config_admin = new_test_user(cls.env, login='cn_config_admin',
                                         groups='base.group_system,account.group_account_manager',
                                         company_id=company.id, company_ids=[(6, 0, company.ids)])
        cls.billing = new_test_user(cls.env, login='cn_billing', groups='account.group_account_invoice',
                                    company_id=company.id, company_ids=[(6, 0, company.ids)])

    def test_unconfirmed_is_not_inferred_from_chart(self):
        self.assertEqual(self.company.qing_cn_taxpayer_type, 'unconfirmed')
        issues = self.company._qing_cn_configuration_issues()
        self.assertTrue(any('身份' in issue for issue in issues))
        for identity in ('general', 'small'):
            self.company.qing_cn_taxpayer_type = identity
            self.assertEqual(self.company.chart_template, 'cn')

    def test_complete_configuration(self):
        self.company.write({'qing_cn_taxpayer_type': 'general', 'vat': '91110000600037341L'})
        self.assertFalse(self.company._qing_cn_configuration_issues())
        result = self.company.with_user(self.manager).action_qing_cn_check_configuration()
        self.assertEqual(result['params']['type'], 'info')
        self.assertIn('复核', result['params']['message'])

    def test_check_does_not_change_accounting(self):
        before = self.company.read(['write_date', 'chart_template', 'qing_cn_taxpayer_type'])
        result = self.company.with_user(self.manager).action_qing_cn_check_configuration()
        self.assertEqual(result['params']['type'], 'warning')
        self.assertEqual(before, self.company.read(['write_date', 'chart_template', 'qing_cn_taxpayer_type']))

    def test_billing_user_cannot_check_or_write(self):
        company = self.company.with_user(self.billing)
        with self.assertRaises(AccessError):
            company.action_qing_cn_check_configuration()
        with self.assertRaises(AccessError):
            company.write({'qing_cn_taxpayer_type': 'general'})

    def test_missing_accounts_journals_and_taxes(self):
        self.company.receivable_account_id = False
        self.company.account_sale_tax_id = False
        journals = self.env['account.journal'].search([
            ('company_id', '=', self.company.id), ('type', '=', 'bank'),
        ])
        journals.active = False
        issues = self.company._qing_cn_configuration_issues()
        self.assertTrue(any('应收账款' in issue for issue in issues))
        self.assertTrue(any('银行' in issue for issue in issues))
        self.assertTrue(any('默认税项' in issue for issue in issues))

    def test_other_company_is_not_accessible(self):
        other = self.env['res.company'].with_context(chart_template_load=True).create({'name': 'Other CN test'})
        with self.assertRaises(AccessError):
            other.with_user(self.manager).action_qing_cn_check_configuration()
        self.company.qing_cn_taxpayer_type = 'general'
        self.assertEqual(other.qing_cn_taxpayer_type, 'unconfirmed')

    def test_settings_related_field_and_view(self):
        settings = self.env['res.config.settings'].with_user(self.config_admin).create({'company_id': self.company.id})
        settings.qing_cn_taxpayer_type = 'small'
        self.assertEqual(self.company.qing_cn_taxpayer_type, 'small')
        self.assertEqual(settings.qing_cn_chart_template, 'cn')
        result = settings.with_user(self.manager).action_qing_cn_check_configuration()
        self.assertEqual(result['tag'], 'display_notification')
        view = self.env['res.config.settings'].get_view(view_type='form')
        self.assertIn('qing_cn_check_setting', view['arch'])

    def test_settings_cannot_write_unauthorized_company(self):
        other = self.env['res.company'].with_context(chart_template_load=True).create({'name': 'Other settings CN test'})
        with self.assertRaises(AccessError):
            self.env['res.config.settings'].with_user(self.config_admin).create({
                'company_id': other.id, 'qing_cn_taxpayer_type': 'general',
            })
        self.assertEqual(other.qing_cn_taxpayer_type, 'unconfirmed')
