from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.qing_base.hooks import disable_publisher_warranty_notification


@tagged('post_install', '-at_install')
class TestPublisherWarrantyDisabled(TransactionCase):
    def test_publisher_notification_never_sends_http(self):
        contract = self.env['publisher_warranty.contract']
        with patch('odoo.addons.mail.models.update.requests.post') as post:
            self.assertEqual(contract._get_sys_logs(), {'messages': []})
            self.assertTrue(contract.update_notification(cron_mode=False))
        post.assert_not_called()

    def test_publisher_cron_is_disabled_and_stays_disabled(self):
        cron = self.env.ref('mail.ir_cron_module_update_notification')
        self.assertFalse(cron.active)

        cron.active = True
        disable_publisher_warranty_notification(self.env)
        self.assertFalse(cron.active)

    def test_partner_autocomplete_does_not_contact_odoo(self):
        api = self.env['iap.autocomplete.api']
        with patch('odoo.addons.partner_autocomplete.models.iap_autocomplete_api.iap_tools.iap_jsonrpc') as request:
            with patch.object(type(self.env['iap.account']), 'get') as get_account:
                self.assertEqual(api._contact_iap('/api/dnb/1', 'search_by_name', {'query': 'Customer'}), {})
                self.assertEqual(self.env['res.partner'].autocomplete_by_name('Customer', False), [])
                self.assertEqual(self.env['res.partner'].enrich_by_domain('customer.example'), {})
            get_account.assert_not_called()
        request.assert_not_called()
