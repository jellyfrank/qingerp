from odoo import fields, models

from .knowledge_member import MEMBER_ROLES


class KnowledgeMemberWizard(models.TransientModel):
    _name = 'qing.knowledge.member.wizard'
    _description = 'Set Knowledge Space Member'

    active = fields.Boolean(default=True)
    role = fields.Selection(MEMBER_ROLES, required=True, default='reader')
    space_id = fields.Many2one('qing.knowledge.space', required=True, readonly=True)
    user_id = fields.Many2one('res.users', required=True, domain=[('share', '=', False)])

    def action_apply(self):
        self.ensure_one()
        self.space_id.set_member(self.user_id.id, self.role, self.active)
        return {'type': 'ir.actions.act_window_close'}
