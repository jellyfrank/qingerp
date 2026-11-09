from odoo import http
from odoo.addons.web.controllers.webmanifest import WebManifest
from odoo.http import request
from odoo.tools import file_open
from odoo.tools.image import image_process
from werkzeug.exceptions import NotFound


BRAND_ICON_PATH = 'qing_base/static/img/qing_logo.png'
BRAND_ICON_SIZES = (180, 192, 512)


class QingWebManifest(WebManifest):
    def _get_webmanifest(self):
        manifest = super()._get_webmanifest()
        manifest['name'] = 'QingERP'
        manifest['short_name'] = 'QingERP'
        manifest['icons'] = [{
            'src': f'/qing/brand/icon/{size}',
            'sizes': f'{size}x{size}',
            'type': 'image/png',
            'purpose': 'any',
        } for size in (192, 512)]
        return manifest

    def _icon_path(self):
        return BRAND_ICON_PATH

    @http.route('/qing/brand/icon/<int:size>', type='http', auth='public',
                methods=['GET'], readonly=True)
    def brand_icon(self, size):
        if size not in BRAND_ICON_SIZES:
            raise NotFound()
        with file_open(BRAND_ICON_PATH, 'rb') as image:
            content = image_process(image.read(), size=(size, size))
        return request.make_response(content, [
            ('Content-Type', 'image/png'),
            ('Cache-Control', 'public, max-age=3600'),
        ])
