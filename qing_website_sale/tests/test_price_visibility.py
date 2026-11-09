import json
from urllib.parse import parse_qs, urlsplit

from lxml import html

from odoo.fields import Command
from odoo.tests import Form, HttpCase, new_test_user, tagged

from odoo.addons.website_sale.tests.common import MockRequest


@tagged('post_install', '-at_install')
class TestPriceVisibility(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env.ref('base.default_website')
        cls.website.write({'qing_login_to_see_price': True, 'ecommerce_access': 'everyone'})
        cls.product = cls.env['product.template'].create({
            'name': 'Qing Price Visibility Test',
            'list_price': 1379.42,
            'taxes_id': [Command.clear()],
            'website_published': True,
        })
        cls.portal = new_test_user(
            cls.env, login='qing_price_portal', password='qing_price_portal',
            groups='base.group_portal',
        )
        cls.public_user = cls.website.user_id

    def _combination(self, user):
        product = self.product.with_user(user).with_context(website_id=self.website.id)
        with MockRequest(product.env, website=self.website.with_user(user)):
            return product._get_combination_info()

    def _rpc(self, path, params):
        response = self.url_open(
            path, data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': params, 'id': 1}),
            headers={'Content-Type': 'application/json'},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _assert_login_label(self, text):
        document = html.fromstring(text)
        labels = document.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " o_qing_login_price_label ")]')
        self.assertTrue(labels)
        self.assertFalse(document.xpath('//*[@aria-label="Sale price"]'))
        self.assertFalse(document.xpath('//div[@name="product_price"]'))
        return labels

    def test_policy_defaults_and_website_isolation(self):
        other = self.env['website'].create({'name': 'Public prices'})
        self.assertFalse(other.qing_login_to_see_price)
        self.assertFalse(other.with_user(self.public_user)._qing_is_price_hidden())
        self.assertTrue(self.website.with_user(self.public_user)._qing_is_price_hidden())
        self.assertFalse(self.website.with_user(self.portal)._qing_is_price_hidden())
        self.assertFalse(self.website.with_user(self.env.user)._qing_is_price_hidden())

    def test_settings_save_per_website(self):
        settings = self.env['res.config.settings'].create({'website_id': self.website.id})
        self.assertTrue(settings.qing_login_to_see_price)
        settings.qing_login_to_see_price = False
        self.assertFalse(self.website.qing_login_to_see_price)
        restored = self.env['res.config.settings'].create({'website_id': self.website.id})
        self.assertFalse(restored.qing_login_to_see_price)

    def test_settings_default_without_company_website(self):
        company = self.env['res.company'].create({'name': 'Company without a website'})
        settings_model = self.env['res.config.settings'].with_company(company)
        self.assertFalse(settings_model._default_website())
        with MockRequest(settings_model.env, website=self.website.with_company(company)):
            defaults = settings_model.default_get(['website_id'])
            self.assertEqual(defaults['website_id'], self.website.id)
            settings = settings_model.create({})
            self.assertEqual(settings.website_id, self.website)

    def test_settings_form_save_without_company_website(self):
        company = self.env['res.company'].create({'name': 'Settings without a website'})
        settings_model = self.env['res.config.settings'].with_company(company)
        self.website.qing_login_to_see_price = False
        # Exercise the settings save with only the relevant fields, avoiding unrelated O2Ms.
        view = self.env['ir.ui.view'].create({
            'name': 'test.settings.website.price', 'model': 'res.config.settings',
            'arch': '<form><field name="company_id"/><field name="website_id"/>'
                    '<field name="qing_login_to_see_price" readonly="not website_id"/></form>',
        })
        with MockRequest(settings_model.env, website=self.website.with_company(company)):
            with Form(settings_model, view=view) as form:
                self.assertEqual(form.website_id, self.website)
                form.qing_login_to_see_price = True
            self.env.flush_all()
            self.env.invalidate_all()
            self.assertTrue(self.website.qing_login_to_see_price)
            with Form(settings_model, view=view) as reopened:
                self.assertTrue(reopened.qing_login_to_see_price)
                reopened.qing_login_to_see_price = False
            self.assertFalse(self.website.qing_login_to_see_price)

    def test_settings_company_website_and_explicit_selection(self):
        company = self.env['res.company'].create({'name': 'Company with a website'})
        website = self.env['website'].create({'name': 'Company website', 'company_id': company.id})
        settings_model = self.env['res.config.settings'].with_company(company)
        self.assertEqual(settings_model.create({}).website_id, website)
        explicit = settings_model.with_context(default_website_id=self.website.id).create({})
        self.assertEqual(explicit.website_id, self.website)
        self.assertEqual(settings_model.create({'website_id': self.website.id}).website_id,
                         self.website)

    def test_settings_backend_host_website_fallback(self):
        company = self.env['res.company'].create({'name': 'Backend without a website'})
        website = self.env['website'].create({'name': 'Host website'})
        settings_model = self.env['res.config.settings'].with_company(company).with_context(
            host_id=website.id, website_id=False
        )
        self.assertEqual(settings_model.default_get(['website_id'])['website_id'], website.id)
        current = settings_model.with_context(host_id=False, website_id=self.website.id)
        self.assertEqual(current.default_get(['website_id'])['website_id'], self.website.id)

    def test_settings_request_host_website_fallback(self):
        company = self.env['res.company'].create({'name': 'Request without a website'})
        website = self.env['website'].create({'name': 'Request host website'})
        settings_model = self.env['res.config.settings'].with_company(company).with_context(
            host_id=False, website_id=False
        )
        request_env = settings_model.env(context=dict(settings_model.env.context,
                                                     host_id=website.id))
        with MockRequest(request_env, website=website.with_env(request_env)):
            self.assertEqual(settings_model.default_get(['website_id'])['website_id'], website.id)

    def test_public_payload_and_purchase_guard(self):
        values = self._combination(self.public_user)
        self.assertTrue(values['hide_price'])
        self.assertTrue(values['prevent_sale'])
        self.assertEqual(values['price'], 0)
        self.assertEqual(values['list_price'], 0)
        self.assertEqual(values['raw_pricelist_price'], 0)
        self.assertEqual(values['packaging_prices'], {})
        product = self.product.product_variant_id.with_user(self.public_user).with_context(
            website_id=self.website.id
        )
        self.assertFalse(product._is_add_to_cart_allowed())

    def test_logged_in_and_disabled_prices(self):
        self.assertAlmostEqual(self._combination(self.portal)['price'], 1379.42)
        self.assertAlmostEqual(self._combination(self.env.user)['price'], 1379.42)
        self.website.qing_login_to_see_price = False
        self.assertAlmostEqual(self._combination(self.public_user)['price'], 1379.42)

    def test_public_shop_product_and_structured_data(self):
        for path in ('/shop', self.product.website_url):
            response = self.url_open(path)
            self.assertEqual(response.status_code, 200)
            self._assert_login_label(response.text)
            self.assertNotIn('1379.42', response.text)
            document = html.fromstring(response.text)
            for script in document.xpath('//script[@type="application/ld+json"]/text()'):
                self.assertNotIn('"offers"', script)
        page = html.fromstring(self.url_open(self.product.website_url).text)
        self.assertFalse(page.xpath('//*[@id="contact_us_wrapper"]'))

    def test_portal_and_disabled_shop_pages(self):
        self.authenticate(self.portal.login, self.portal.login)
        response = self.url_open(self.product.website_url)
        self.assertEqual(response.status_code, 200)
        page = html.fromstring(response.text)
        self.assertTrue(page.xpath('//div[@name="product_price"]'))
        self.assertFalse(page.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " o_qing_login_price_label ")]'))
        self.authenticate(None, None)
        self.website.qing_login_to_see_price = False
        response = self.url_open('/shop')
        page = html.fromstring(response.text)
        self.assertTrue(page.xpath('//*[@aria-label="Sale price"]'))
        self.assertFalse(page.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " o_qing_login_price_label ")]'))

    def test_signup_visibility_and_return_url(self):
        self.website.auth_signup_uninvited = 'b2c'
        path = self.product.website_url + '?search=Qing'
        labels = self._assert_login_label(self.url_open(path).text)
        urls = [a.get('href') for a in labels[0].xpath('.//a')]
        self.assertEqual([urlsplit(url).path for url in urls], ['/web/login', '/web/signup'])
        for url in urls:
            self.assertEqual(parse_qs(urlsplit(url).query)['redirect'], [path])
        self.website.auth_signup_uninvited = 'b2b'
        labels = self._assert_login_label(self.url_open(path).text)
        self.assertEqual(len(labels[0].xpath('.//a')), 1)

    def test_cart_and_checkout_require_login(self):
        response = self.url_open('/shop/cart')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(urlsplit(response.url).path.endswith('/web/login'))
        result = self._rpc('/shop/cart/add', {
            'product_template_id': self.product.id,
            'product_id': self.product.product_variant_id.id,
            'quantity': 1,
        })
        self.assertEqual(result['error']['data']['name'], 'odoo.exceptions.AccessError')
        steps = self.env['website.checkout.step'].with_user(self.public_user).with_context(
            website_id=self.website.id
        )
        with MockRequest(steps.env, website=self.website.with_user(self.public_user)):
            self.assertTrue(steps.validate_checkout_progress('/shop/payment', self.env['sale.order'])
                            .startswith('/web/login?'))

    def test_variant_rpc_and_filter(self):
        result = self._rpc('/website_sale/get_combination_info', {
            'product_template_id': self.product.id,
            'product_id': self.product.product_variant_id.id,
            'combination': [], 'add_qty': 1,
        })['result']
        self.assertEqual(result['price'], 0)
        self.assertTrue(result['hide_price'])
        self.assertNotIn('1379.42', json.dumps(result))
        price_filter = self.env.ref('website_sale.filter_products_price')
        price_filter.active = True
        public_site = self.website.with_user(self.public_user).with_context(website_id=self.website.id)
        self.assertFalse(public_site.is_view_active('website_sale.filter_products_price'))
        self.assertTrue(self.website.is_view_active('website_sale.filter_products_price'))
        response = self.url_open('/shop')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('o_wsale_price_range_option', response.text)

    def test_search_and_dynamic_snippet_prices(self):
        result = self._rpc('/website/snippet/autocomplete', {
            'search_type': 'products', 'term': 'Qing Price', 'order': 'name asc',
            'options': {'displayDescription': True},
        })['result']
        self.assertNotIn('1379.42', json.dumps(result))
        public_env = self.env(user=self.public_user, context=dict(self.env.context,
                                                                website_id=self.website.id))
        snippet = public_env.ref('website_sale.dynamic_filter_all_products').sudo()
        self.assertTrue(snippet)
        with MockRequest(public_env, website=self.website.with_user(self.public_user)):
            data = snippet._filter_records_to_values(self.product.product_variant_id.with_env(public_env))
            self.assertTrue(data)
            self.assertTrue(data[0]['hide_price'])
            self.assertEqual(data[0]['price'], 0)
            markup = public_env['ir.ui.view']._render_template(
                'website_sale.price_dynamic_filter_template_product_product',
                {'data': data[0], 'website': public_env.website},
            )
            self.assertIn('o_qing_login_price_label', str(markup))

    def test_variant_extra_and_jsonld(self):
        attribute = self.env['product.attribute'].create({
            'name': 'Size', 'value_ids': [Command.create({'name': 'Small'}),
                                        Command.create({'name': 'Large'})],
        })
        self.product.attribute_line_ids = [Command.create({
            'attribute_id': attribute.id,
            'value_ids': [Command.set(attribute.value_ids.ids)],
        })]
        ptavs = self.product.attribute_line_ids.product_template_value_ids
        ptavs.write({'price_extra': 51.0, 'show_price_extra': True})
        public_env = self.env(user=self.public_user, context=dict(self.env.context,
                                                                website_id=self.website.id))
        with MockRequest(public_env, website=self.website.with_user(self.public_user)):
            self.assertEqual(ptavs[0].with_env(public_env)._get_extra_price(self.website.currency_id), 0)
            data = self.product.with_env(public_env)._prepare_jsonld_vals()
            self.assertNotIn('offers', json.dumps(data))
            self.assertNotIn('1379.42', json.dumps(data))
        portal_env = self.env(user=self.portal, context=dict(self.env.context,
                                                           website_id=self.website.id))
        with MockRequest(portal_env, website=self.website.with_user(self.portal)):
            self.assertGreater(ptavs[0].with_env(portal_env)._get_extra_price(self.website.currency_id), 0)
            data = self.product.with_env(portal_env)._prepare_jsonld_vals()
            self.assertIn('offers', json.dumps(data))

    def test_chinese_label_and_setting(self):
        self.env['res.lang']._activate_lang('zh_CN')
        self.env['ir.module.module']._load_module_terms(
            ['qing_website_sale'], ['zh_CN'], overwrite=True
        )
        label = self.env.ref('qing_website_sale.login_price_label').with_context(lang='zh_CN')
        self.assertIn('>登录<', label.arch_db)
        self.assertIn('>注册<', label.arch_db)
        self.assertIn('>查看价格<', label.arch_db)
        settings = self.env['res.config.settings'].with_context(lang='zh_CN')
        self.assertEqual(settings.fields_get(['qing_login_to_see_price'])
                         ['qing_login_to_see_price']['string'], '登录后可见商品价格')
