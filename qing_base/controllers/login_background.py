import io

from PIL import Image
from werkzeug.exceptions import NotFound

from odoo import http
from odoo.http import request


class LoginBackground(http.Controller):
    def _preview_settings(self, settings_id):
        if not request.env.user.has_group('base.group_system'):
            raise NotFound()
        settings = request.env['res.config.settings'].browse(settings_id).exists()
        if not settings or settings.create_uid != request.env.user:
            raise NotFound()
        return settings

    @http.route('/qing/login/background', type='http', auth='public', methods=['GET'], readonly=True)
    def background(self):
        backgrounds = request.env['qing.login.background']
        if backgrounds._saved_values()['style'] != 'image':
            raise NotFound()
        attachment = backgrounds._attachment()
        if not attachment:
            raise NotFound()
        return request.make_response(attachment.raw.content, [
            ('Content-Type', attachment.mimetype or 'application/octet-stream'),
            ('X-Content-Type-Options', 'nosniff'), ('Cache-Control', 'no-store'),
        ])

    @http.route('/qing/login/preview/<int:settings_id>', type='http', auth='user', methods=['GET'],
                website=True, sitemap=False, readonly=True)
    def preview(self, settings_id):
        settings = self._preview_settings(settings_id)
        background = request.env['qing.login.background']._display_values(
            settings.qing_login_background_style, settings.qing_login_background_color,
            bool(settings.qing_login_background_image), f'/qing/login/preview-image/{settings.id}',
        )
        return request.render('qing_base.login_background_preview', {
            'qing_preview': True, 'qing_background': background,
        })

    @http.route('/qing/login/preview-image/<int:settings_id>', type='http', auth='user', methods=['GET'], readonly=True)
    def preview_image(self, settings_id):
        settings = self._preview_settings(settings_id)
        if not settings.qing_login_background_image:
            raise NotFound()
        content = settings.qing_login_background_image.content
        with Image.open(io.BytesIO(content)) as image:
            mimetype = Image.MIME[image.format]
        return request.make_response(content, [
            ('Content-Type', mimetype), ('X-Content-Type-Options', 'nosniff'), ('Cache-Control', 'no-store'),
        ])
