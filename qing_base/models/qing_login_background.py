import re

from odoo import api, models


BACKGROUND_STYLES = frozenset(('default', 'dawn', 'aurora', 'silk', 'qingdao', 'image', 'solid', 'sea', 'forest', 'sand', 'twilight'))
DEFAULT_COLOR = '#dfe9ee'
# Scenes pale enough at the foot of the page to need dark footer text.
LIGHT_BACKDROP_STYLES = frozenset(('silk', 'sea', 'forest', 'sand', 'twilight'))


class QingLoginBackground(models.AbstractModel):
    _name = 'qing.login.background'
    _description = 'Login Background Configuration'

    @api.model
    def _qing_background_styles(self):
        """Styles the login page can draw; modules that add a scene extend this."""
        return BACKGROUND_STYLES

    @api.model
    def _display_values(self, style, color, has_image=False, image_url=None):
        if style not in self._qing_background_styles() or (style == 'image' and not has_image):
            style = 'default'
        if not color or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            color = DEFAULT_COLOR
        return {'style': style, 'color': color, 'image_url': image_url, 'backdrop': self._backdrop(style, color)}

    @api.model
    def _backdrop(self, style, color):
        """'light' or 'dark': what the footer under the login card is read against."""
        if style == 'solid':
            red, green, blue = (int(color[index:index + 2], 16) for index in (1, 3, 5))
            return 'light' if 0.2126 * red + 0.7152 * green + 0.0722 * blue > 150 else 'dark'
        return 'light' if style in LIGHT_BACKDROP_STYLES else 'dark'

    @api.model
    def _attachment(self):
        attachment_id = self.env['ir.config_parameter'].sudo().get_str('qing_base.login_background_attachment_id', '')
        attachment = self.env['ir.attachment'].sudo().browse(int(attachment_id)).exists() if attachment_id.isdigit() else self.env['ir.attachment']
        if attachment and (attachment.res_model != 'ir.ui.view' or attachment.res_field != 'qing_login_background' or attachment.res_id != self.env.ref('qing_base.login_page').id):
            return self.env['ir.attachment']
        return attachment

    @api.model
    def _saved_values(self):
        params = self.env['ir.config_parameter'].sudo()
        return self._display_values(
            params.get_str('qing_base.login_background_style', 'default'),
            params.get_str('qing_base.login_background_color', DEFAULT_COLOR),
            bool(self._attachment()), '/qing/login/background',
        )
