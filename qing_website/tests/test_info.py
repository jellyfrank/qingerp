from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestQingWebsiteInfo(HttpCase):

    def test_info_page_names_qingerp(self):
        self.env.ref('website.show_website_info').active = True
        arch = self.env.ref('website.website_info').with_context(lang='en_US').get_combined_arch()
        self.assertIn('This website runs on QingERP.', arch)
        self.assertNotIn('Open Source ERP', arch)
        self.assertNotIn('odoo.com', arch)

    def test_info_page_renders(self):
        self.env.ref('website.show_website_info').active = True
        response = self.url_open('/website/info')
        self.assertEqual(response.status_code, 200)
        self.assertIn('This website runs on QingERP.', response.text)
        self.assertNotIn('odoo.com/app/accounting', response.text)
        self.assertNotIn('Open Source ERP', response.text)
