"""Reusable instructions and tool bundles for Qing AI agents."""

from odoo import api, fields, models
from odoo.exceptions import AccessError


class QingAiSkill(models.Model):
    _name = 'qing.ai.skill'
    _description = 'Qing AI Skill'
    _order = 'sequence, name, id'

    active = fields.Boolean(default=True)
    description = fields.Text()
    group_ids = fields.Many2many('res.groups', string='Allowed Groups',
                                 help='Leave empty for all internal users.')
    instructions = fields.Text(help='Instructions for the assistant. Do not put secrets here.')
    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    tool_ids = fields.Many2many('qing.ai.tool', string='Available Actions')
    skill_type = fields.Selection(
        [('guidance', 'Guidance'), ('operation', 'Executable')],
        compute='_compute_skill_type', string='Skill Type',
    )

    @api.depends('tool_ids', 'tool_ids.review_state', 'tool_ids.active')
    def _compute_skill_type(self):
        for skill in self:
            skill.skill_type = ('operation' if skill.tool_ids.filtered(
                lambda tool: tool.review_state == 'published' and tool.active)
                else 'guidance')

    def action_open_discovery(self):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._('Only system administrators can scan AI actions.'))
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Discover AI Actions'),
            'res_model': 'qing.ai.discovery.wizard',
            'view_mode': 'form', 'target': 'new',
        }

    def action_open_candidates(self):
        return self.env['qing.ai.tool'].action_open_candidates()

    def action_open_builder(self):
        self.ensure_one()
        action = self.env['qing.ai.tool'].action_open_builder()
        action['context'] = {'default_skill_id': self.id}
        return action

    def available_for_user(self):
        if self.env.user.share:
            return self.browse()
        groups = self.env.user.all_group_ids
        return self.filtered(lambda skill: skill.active and (not skill.group_ids or bool(skill.group_ids & groups)))
