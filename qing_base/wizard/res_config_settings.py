import io
import re

from PIL import Image, UnidentifiedImageError

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError


LOGIN_BACKGROUND_STYLES = [
    ('default', 'QingERP Default (Restore)'),
    ('image', 'Custom Image'),
    ('solid', 'Solid Color'),
    ('sea', 'Sea Blue'),
    ('forest', 'Forest Green'),
    ('sand', 'Warm Sand'),
    ('twilight', 'Twilight Violet'),
]
MAX_BACKGROUND_BYTES = 2 * 1024 * 1024


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    @api.constrains('qing_login_background_color', 'qing_login_background_image')
    def _check_login_background(self):
        for settings in self:
            if settings.qing_login_background_color and not re.fullmatch(r'#[0-9a-fA-F]{6}', settings.qing_login_background_color):
                raise ValidationError(self.env._('Choose a valid background color.'))
            if not settings.qing_login_background_image:
                continue
            try:
                data = settings.qing_login_background_image.content
                if len(data) > MAX_BACKGROUND_BYTES:
                    raise ValidationError(self.env._('The background image must be 2 MB or smaller.'))
                with Image.open(io.BytesIO(data)) as image:
                    if image.format not in ('JPEG', 'PNG', 'WEBP') or image.width * image.height > 20000000:
                        raise ValidationError(self.env._('Use a JPEG, PNG or WebP image of at most 20 megapixels.'))
                    image.verify()
            except (ValueError, UnidentifiedImageError, OSError, Image.DecompressionBombError):
                raise ValidationError(self.env._('Use a valid JPEG, PNG or WebP image.'))

    qing_login_background_color = fields.Char(
        string='Background Color', default='#dfe9ee',
        config_parameter='qing_base.login_background_color',
    )
    qing_login_background_image = fields.Binary(string='Background Image', attachment=False)
    qing_login_background_style = fields.Selection(
        LOGIN_BACKGROUND_STYLES, string='Login Background', default='default',
        config_parameter='qing_base.login_background_style', required=True,
    )

    @api.model
    def get_values(self):
        values = super().get_values()
        attachment = self.env['qing.login.background']._attachment()
        if attachment:
            values['qing_login_background_image'] = attachment.raw
        return values

    def set_values(self):
        self.ensure_one()
        self._check_login_background()
        result = super().set_values()
        params = self.env['ir.config_parameter'].sudo()
        attachment = self.env['qing.login.background']._attachment()
        if self.qing_login_background_image:
            values = {
                'name': 'QingERP login background', 'raw': self.qing_login_background_image,
                'res_model': 'ir.ui.view', 'res_id': self.env.ref('qing_base.login_page').id,
                'res_field': 'qing_login_background', 'public': False,
            }
            if attachment:
                attachment.write(values)
            else:
                attachment = self.env['ir.attachment'].sudo().create(values)
            params.set_str('qing_base.login_background_attachment_id', str(attachment.id))
        elif attachment:
            params.set_str('qing_base.login_background_attachment_id', False)
            attachment.unlink()
        return result

    @api.model
    def preview_login_background(self, style, color, image):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._('Only administrators can preview login backgrounds.'))
        settings = self.create({
            'qing_login_background_style': style,
            'qing_login_background_color': color,
            'qing_login_background_image': image or False,
        })
        settings._check_login_background()
        return f'/qing/login/preview/{settings.id}'
