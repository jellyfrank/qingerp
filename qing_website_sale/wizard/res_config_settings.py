from odoo import fields, models
from odoo.http import request


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    def _qing_default_website(self):
        website = self._default_website()
        if website:
            return website
        # Backend routes use host_id, while website routes use website_id.
        website = self.env.website or self.env['website'].browse(self.env.context.get('host_id'))
        if not website and request:
            website = request.env.website or request.env['website'].browse(
                request.env.context.get('host_id')
            )
        return website.exists() or self.env.ref('base.default_website', raise_if_not_found=False)

    qing_login_to_see_price = fields.Boolean(
        related='website_id.qing_login_to_see_price',
        readonly=False,
    )
    website_id = fields.Many2one(default=lambda self: self._qing_default_website())
