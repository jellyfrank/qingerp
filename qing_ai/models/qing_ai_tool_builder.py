"""Configure common operations without writing Python or JSON schemas."""

from odoo import api, fields, models, Command
from odoo.exceptions import ValidationError

from .qing_ai_tool_orm import ALL_READABLE_FIELDS_HELP, ORM_OPERATIONS


class QingAiToolBuilder(models.TransientModel):
    _name = 'qing.ai.tool.builder'
    _description = 'Configure AI Tools'

    def _template_tools(self):
        return self.env['qing.ai.tool']

    def _selected_operations(self):
        return [key for key, _label in ORM_OPERATIONS if self['use_' + key]]

    @api.onchange('model_id')
    def _onchange_model(self):
        self.all_read_fields = True
        self.read_field_ids = self.model_id.field_id.filtered(lambda f: f.name == 'name')
        self.write_field_ids = self.read_field_ids
        self.fixed_domain, self.fixed_values = '[]', '{}'
        self.group_field_ids = False
        self.measure_field_ids = False

    all_read_fields = fields.Boolean(
        string='Discover Readable Fields Automatically', default=True, help=ALL_READABLE_FIELDS_HELP,
    )
    fixed_domain = fields.Char(string='Record Filter', default='[]')
    fixed_values = fields.Text(string='Fixed Values (JSON)', default='{}')
    group_ids = fields.Many2many('res.groups', string='Allowed Groups',
                                 help='Leave empty for all internal users. Native access rights still apply.')
    group_field_ids = fields.Many2many('ir.model.fields', 'qing_ai_builder_group_field_rel', string='Group By Fields')
    measure_field_ids = fields.Many2many('ir.model.fields', 'qing_ai_builder_measure_field_rel', string='Measure Fields')
    model_id = fields.Many2one('ir.model', string='Business Model', ondelete='cascade')
    model_name = fields.Char(related='model_id.model')
    query_limit = fields.Integer(string='Maximum Query Results', default=20)
    read_field_ids = fields.Many2many('ir.model.fields', 'qing_ai_builder_read_field_rel',
                                     string='Readable and Filter Fields')
    skill_id = fields.Many2one('qing.ai.skill', string='Add to Skill')
    template = fields.Selection([('custom', 'Choose Model and Operations')], default='custom', required=True)
    use_archive = fields.Boolean(string='Archive')
    use_count = fields.Boolean(string='Count Records')
    use_create = fields.Boolean(string='Create')
    use_read = fields.Boolean(string='Read Details')
    use_read_group = fields.Boolean(string='Grouped Statistics')
    use_restore = fields.Boolean(string='Restore')
    use_search = fields.Boolean(string='Query Records', default=True)
    use_unlink = fields.Boolean(string='Delete')
    use_write = fields.Boolean(string='Update')
    write_field_ids = fields.Many2many('ir.model.fields', 'qing_ai_builder_write_field_rel',
                                      string='Writable Fields')

    def action_generate(self):
        self.ensure_one()
        Tool = self.env['qing.ai.tool']
        Tool._require_admin()
        with self.env.cr.savepoint():
            if self.template != 'custom':
                tools = self._template_tools()
                if not tools:
                    raise ValidationError(self.env._('This business template is unavailable.'))
            else:
                operations = self._selected_operations()
                if not self.model_id or not operations:
                    raise ValidationError(self.env._('Choose a model and at least one operation.'))
                labels = dict(Tool._fields['orm_operation']._description_selection(self.env))
                tools = Tool.create([{
                    'name': '%s · %s' % (self.model_id.name, labels[key]),
                    'description': self.env._('%(operation)s on %(model)s within the configured fields and record filter.',
                                              operation=labels[key], model=self.model_id.name),
                    'execution_type': 'orm', 'model_id': self.model_id.id, 'orm_operation': key,
                    'orm_all_fields': self.all_read_fields,
                    'orm_group_field_ids': [Command.set(self.group_field_ids.ids)] if key == 'read_group' else [],
                    'orm_measure_field_ids': [Command.set(self.measure_field_ids.ids)] if key == 'read_group' else [],
                    'orm_field_ids': [Command.set(self.read_field_ids.ids)],
                    'orm_write_field_ids': [Command.set(self.write_field_ids.ids)] if key in ('create', 'write') else [],
                    'orm_domain': self.fixed_domain, 'orm_defaults': self.fixed_values if key in ('create', 'write') else '{}',
                    'orm_limit': self.query_limit, 'group_ids': [Command.set(self.group_ids.ids)],
                } for key in operations])
            if self.skill_id:
                self.skill_id.write({'tool_ids': [Command.link(tool.id) for tool in tools]})
        return {'type': 'ir.actions.act_window', 'name': self.env._('Configured AI Tools'),
                'res_model': 'qing.ai.tool', 'view_mode': 'list,form',
                'domain': [('id', 'in', tools.ids)]}
