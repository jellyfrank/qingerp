from odoo import api, fields, models
from odoo.exceptions import ValidationError


class PartnerTeam(models.Model):
    _name = 'qing.partner.team'
    _description = 'Partner Business Team'
    _check_company_auto = True

    @api.constrains('company_id', 'member_ids')
    def _check_members(self):
        for team in self:
            if any(user.share or team.company_id not in user.company_ids for user in team.member_ids):
                raise ValidationError(self.env._('Team members must be internal users of the team company.'))

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    member_ids = fields.Many2many('res.users', string='Members', domain=[('share', '=', False)])
    name = fields.Char(required=True)

    def write(self, values):
        if 'company_id' in values and any(team.company_id.id != values['company_id'] for team in self):
            raise ValidationError(self.env._('Create a new team instead of changing its company.'))
        result = super().write(values)
        self.env.invalidate_all()
        return result
