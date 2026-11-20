from odoo import fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    qing_ai_guide_titles_consent = fields.Char(copy=False, groups='base.group_system')
