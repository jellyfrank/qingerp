from odoo import _, fields, models
from odoo.exceptions import UserError

# Only these callbacks may be triggered from the confirmation dialog.
ALLOWED_CALLBACKS = {
    "hr.department": {"_action_pops_up_confirm", "_sync_employees"},
}


class WeworkConfirmWizard(models.TransientModel):
    _name = "wework.confirm.wizard"
    _description = "WeCom Confirmation"

    callback = fields.Char(required=True, readonly=True)
    content = fields.Text(readonly=True)
    name = fields.Char(readonly=True)
    res_ids = fields.Char(readonly=True)
    res_model = fields.Char(required=True, readonly=True)

    def action_confirm(self):
        self.ensure_one()
        if self.callback not in ALLOWED_CALLBACKS.get(self.res_model, set()):
            raise UserError(_("This confirmation is not allowed."))
        ids = [int(record_id) for record_id in (self.res_ids or "").split(",") if record_id]
        records = self.env[self.res_model].browse(ids)
        return getattr(records, self.callback)()
