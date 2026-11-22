from odoo.tests import HttpCase, TransactionCase, tagged


def combined(env, xmlid):
    return env.ref(xmlid).get_combined_arch()


@tagged('post_install', '-at_install')
class TestQingWebsiteBranding(TransactionCase):

    def test_odoo_promotion_replaced(self):
        for xmlid in ('web.brand_promotion_message', 'portal.portal_record_sidebar',
                      'mail.mail_notification_layout', 'mail.mail_notification_light'):
            arch = combined(self.env, xmlid)
            self.assertNotIn('www.odoo.com', arch, xmlid)
        self.assertIn('qingsolution.com', combined(self.env, 'web.brand_promotion_message'))
        # The message is cleared right before the call (website_sale replaces
        # the call with its own message, so the arch keeps Odoo's text).
        arch = combined(self.env, 'website.brand_promotion')
        self.assertLess(arch.rindex('t-value="False"'), arch.rindex("t-call=\"web.brand_promotion_message\""))
        self.assertIn('content="QingERP"', combined(self.env, 'website.layout'))


@tagged('post_install', '-at_install')
class TestQingWebsiteBrandingPage(HttpCase):

    def test_homepage_signed_by_qingerp(self):
        page = self.url_open('/').text
        self.assertIn('<meta name="generator" content="QingERP"', page)
        self.assertIn('o_qing_powered_by', page)
        self.assertNotIn('odoo.com?utm_source=db', page)
        self.assertNotIn('www.odoo.com', page)
        self.assertNotIn('free website', page)
