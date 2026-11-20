from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools import BinaryBytes, file_open

from odoo.addons.qing_website.models.website import binary_content


@tagged('post_install', '-at_install')
class TestQingWebsiteFavicon(TransactionCase):

    def test_new_website_uses_the_q_mark(self):
        Website = self.env['website']
        website = Website.create({'name': 'Qing favicon'})
        self.assertEqual(binary_content(website.favicon), binary_content(Website._qing_default_favicon()))
        self.assertNotIn(binary_content(website.favicon), Website._qing_stock_favicons())

    def test_stock_favicon_is_replaced_and_uploaded_one_kept(self):
        Website = self.env['website']
        with file_open('web/static/img/favicon.ico', 'rb') as image:
            stock = image.read()
        with file_open('qing_base/static/img/qing_wordmark.png', 'rb') as image:
            uploaded = image.read()
        stock_site = Website.create({'name': 'Stock favicon', 'favicon': BinaryBytes(stock, filename='favicon.ico')})
        own_site = Website.create({'name': 'Own favicon', 'favicon': BinaryBytes(uploaded, filename='own.png')})
        own_before = binary_content(own_site.favicon)
        self.assertIn(binary_content(stock_site.favicon), Website._qing_stock_favicons())

        replaced = Website._qing_replace_stock_favicons()

        self.assertIn(stock_site, replaced)
        self.assertNotIn(own_site, replaced)
        self.assertEqual(binary_content(stock_site.favicon), binary_content(Website._qing_default_favicon()))
        self.assertEqual(binary_content(own_site.favicon), own_before)


@tagged('post_install', '-at_install')
class TestQingWebsiteFaviconPage(HttpCase):

    def test_login_page_serves_the_q_mark(self):
        website = self.env['website'].search([], order='id', limit=1)
        response = self.url_open('/web/login')
        self.assertEqual(response.status_code, 200)
        self.assertIn('/web/image/website/%s/favicon' % website.id, response.text)
        icon = self.url_open('/web/image/website/%s/favicon' % website.id)
        self.assertEqual(icon.status_code, 200)
        self.assertTrue(icon.content.startswith(b'\x00\x00\x01\x00'), 'favicon should be an ICO file')
        self.assertNotIn(icon.content, self.env['website']._qing_stock_favicons())

    def test_favicon_ico_is_not_cached_for_long(self):
        response = self.url_open('/favicon.ico', allow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/favicon', response.headers['Location'])
        self.assertEqual(response.headers['Cache-Control'], 'public, max-age=3600')
