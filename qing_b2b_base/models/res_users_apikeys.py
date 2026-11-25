from odoo import _, models
from odoo.addons.base.models.res_users import INDEX_SIZE, KEY_CRYPT_CONTEXT
from odoo.exceptions import AccessError
from odoo.tools import SQL

from ..constants import B2B_SCOPE


class ResUsersApikeys(models.Model):
    _inherit = "res.users.apikeys"

    def _b2b_current_device(self, token):
        self.env.cr.execute(SQL(
            "SELECT id, key FROM %s WHERE user_id = %s AND scope = %s AND index = %s",
            SQL.identifier(self._table), self.env.uid, B2B_SCOPE, token[:INDEX_SIZE],
        ))
        for device_id, digest in self.env.cr.fetchall():
            if KEY_CRYPT_CONTEXT.verify(token, digest):
                return self.browse(device_id)
        return self.browse()

    def action_b2b_revoke(self):
        if not self.env.is_system() or any(key.scope != B2B_SCOPE for key in self):
            raise AccessError(_("Only an administrator can revoke another buyer's ordering device."))
        return self._remove()
