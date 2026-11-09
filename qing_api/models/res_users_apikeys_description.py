from odoo import fields, models


class ResUsersApiKeysDescription(models.TransientModel):
    _inherit = "res.users.apikeys.description"

    def _default_duration(self):
        if self.env.user._is_portal() and "365" in dict(self._selection_duration()):
            return "365"
        return super()._default_duration()

    duration = fields.Selection(default=_default_duration)
    scope = fields.Selection(
        selection_add=[("qing_api", "QingERP API")],
        ondelete={"qing_api": "cascade"},
    )
