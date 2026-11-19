import re

from odoo import api, models


BACKGROUND_STYLES = frozenset(('default', 'image', 'solid', 'sea', 'forest', 'sand', 'twilight'))
DEFAULT_COLOR = '#dfe9ee'


class QingLoginBackground(models.AbstractModel):
    _name = 'qing.login.background'
    _description = 'Login Background Configuration'

    @api.model
    def _display_values(self, style, color, has_image=False, image_url=None):
        if style not in BACKGROUND_STYLES or (style == 'image' and not has_image):
            style = 'default'
        if not color or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            color = DEFAULT_COLOR
        return {'style': style, 'color': color, 'image_url': image_url}

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
