from odoo import api, models
from odoo.exceptions import AccessError

PROTECTED_MODEL = 'qing.knowledge.asset'


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('res_model') == PROTECTED_MODEL and not self.env.su:
                raise AccessError(self.env._('Use the knowledge upload action.'))
            if vals.get('res_model') == PROTECTED_MODEL and (vals.get('public') or vals.get('access_token') or vals.get('url')):
                raise AccessError(self.env._('Knowledge files must remain private.'))
        return super().create(vals_list)

    def write(self, vals):
        protected = self.sudo().filtered(lambda attachment: attachment.res_model == PROTECTED_MODEL)
        if protected and (not self.env.su or set(vals) - {'res_name'}):
            raise AccessError(self.env._('Knowledge file content and ownership are immutable.'))
        if vals.get('res_model') == PROTECTED_MODEL:
            raise AccessError(self.env._('Use the knowledge upload action.'))
        return super().write(vals)

    def unlink(self):
        if any(attachment.res_model == PROTECTED_MODEL for attachment in self.sudo()):
            raise AccessError(self.env._('Knowledge files cannot be deleted through RPC.'))
        return super().unlink()

    def _generate_access_token(self):
        if any(attachment.res_model == PROTECTED_MODEL for attachment in self.sudo()):
            raise AccessError(self.env._('Knowledge files cannot use share tokens.'))
        return super()._generate_access_token()
