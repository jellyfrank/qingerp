import io

from PIL import Image

from odoo.exceptions import AccessError, ValidationError
from odoo.tools.binary import BinaryBytes
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged


@tagged('at_install', '-post_install')
class TestLoginBackground(TransactionCase):
    def test_styles_and_fallbacks(self):
        background = self.env['qing.login.background']
        for style in ('default', 'dawn', 'aurora', 'silk', 'qingdao', 'solid', 'sea', 'forest', 'sand', 'twilight'):
            self.assertEqual(background._display_values(style, '#abc123')['style'], style)
        self.assertEqual(background._display_values('image', '#abc123')['style'], 'default')
        self.assertEqual(background._display_values('invalid', 'bad')['color'], '#dfe9ee')

    def test_backdrop_tells_the_footer_which_text_to_use(self):
        background = self.env['qing.login.background']
        for style, color, backdrop in (
            ('default', '#dfe9ee', 'dark'), ('aurora', '#dfe9ee', 'dark'), ('dawn', '#dfe9ee', 'dark'),
            ('sea', '#dfe9ee', 'light'), ('silk', '#dfe9ee', 'light'), ('qingdao', '#dfe9ee', 'dark'),
            ('solid', '#ffffff', 'light'), ('solid', '#1b2a3a', 'dark'), ('solid', 'bad', 'light'),
        ):
            self.assertEqual(background._display_values(style, color)['backdrop'], backdrop, (style, color))

    def test_image_save_reload_and_restore(self):
        image = io.BytesIO()
        Image.new('RGB', (100, 80), '#246abc').save(image, 'PNG')
        image_data = BinaryBytes(image.getvalue())
        settings = self.env['res.config.settings'].create({
            'qing_login_background_style': 'image', 'qing_login_background_image': image_data,
        })
        settings.set_values()
        background = self.env['qing.login.background']
        self.assertEqual(background._saved_values()['style'], 'image')
        self.assertEqual(background._attachment().raw.content, image.getvalue())
        settings = self.env['res.config.settings'].create({})
        self.assertEqual(settings.qing_login_background_image.content, image_data.content)
        settings.qing_login_background_style = 'default'
        settings.set_values()
        self.assertEqual(background._saved_values()['style'], 'default')

    def test_invalid_inputs(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['res.config.settings'].create({'qing_login_background_image': BinaryBytes(b'not an image')})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['res.config.settings'].create({'qing_login_background_color': '#000; background:red'})

    def test_preview_is_private_and_does_not_apply(self):
        background = self.env['qing.login.background']
        before = background._saved_values()
        url = self.env['res.config.settings'].preview_login_background('forest', '#123456', False)
        self.assertTrue(url.startswith('/qing/login/preview/'))
        self.assertEqual(background._saved_values(), before)
        with self.assertRaises(AccessError):
            self.env['res.config.settings'].with_user(self.env.ref('base.public_user')).preview_login_background('forest', '#123456', False)


@tagged('post_install', '-at_install')
class TestLoginBackgroundPreview(HttpCase):
    def test_admin_can_render_unsaved_preview(self):
        admin = new_test_user(self.env, login='background_preview_admin', groups='base.group_system')
        url = self.env['res.config.settings'].with_user(admin).preview_login_background('forest', '#123456', False)
        self.authenticate(admin.login, admin.login)
        response = self.url_open(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('qing-login-scene-forest', response.text)
