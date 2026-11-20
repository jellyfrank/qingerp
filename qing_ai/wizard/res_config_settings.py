from odoo import api, fields, models
from odoo.exceptions import UserError


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    qing_ai_professional_guide = fields.Boolean(string='Enable Professional Knowledge')

    @api.model
    def get_values(self):
        values = super().get_values()
        values['qing_ai_professional_guide'] = self.env['qing.ai.guide']._professional_enabled()
        return values

    def set_values(self):
        super().set_values()
        guide = self.env['qing.ai.guide']
        enabled = self.qing_ai_professional_guide and guide._online_allowed()
        guide._parameters().set_str(
            'qing_ai.professional_guide_consent', guide._database_uuid() if enabled else '',
        )

    def action_open_guide_registration(self):
        Instance = self.env['qing.service.instance']
        Instance._check_admin()
        if not self.env['qing.ai.guide']._professional_enabled():
            raise UserError(self.env._('Enable and save Professional Knowledge before registering the instance.'))
        instance = Instance._local_instance()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Professional Knowledge Registration'),
            'res_model': instance._name,
            'res_id': instance.id,
            'views': [(self.env.ref('qing_ai.view_qing_ai_guide_registration_form').id, 'form')],
            'target': 'current',
        }
