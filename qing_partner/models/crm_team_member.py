from odoo import api, models


class CrmTeamMember(models.Model):
    _inherit = "crm.team.member"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env.invalidate_all()
        return records

    def write(self, values):
        result = super().write(values)
        self.env.invalidate_all()
        return result

    def unlink(self):
        result = super().unlink()
        self.env.invalidate_all()
        return result
