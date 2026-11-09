"""Permission-scoped database aggregation for configurable AI tools."""

from datetime import date, datetime

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .qing_ai_tool_orm import SENSITIVE_FIELD


GROUP_TYPES = {'char', 'boolean', 'integer', 'selection', 'date', 'datetime', 'many2one'}
MEASURE_TYPES = {'integer', 'float', 'monetary'}
AGGREGATIONS = ('sum', 'avg', 'min', 'max')
DATE_INTERVALS = ('day', 'week', 'month', 'quarter', 'year')
# Longer option lists are described by rule instead of enumerated in the planner schema;
# _group_arguments still validates every value against the full option list.
GROUP_ENUM_LIMIT = 20
GROUPBY_RULE = (
    'Stored fields from the model catalog of type char, boolean, integer, selection, date, datetime '
    'or many2one. Date and datetime fields need an interval suffix: :day, :week, :month, :quarter '
    'or :year, for example create_date:month.'
)
AGGREGATE_RULE = (
    '"__count", or a stored integer, float or monetary field from the model catalog with :sum, '
    ':avg, :min or :max, for example amount_total:sum.'
)


class QingAiTool(models.Model):
    _inherit = 'qing.ai.tool'

    def _group_metadata(self, Model, measure=False):
        types = MEASURE_TYPES if measure else GROUP_TYPES
        selected = self.sudo().orm_measure_field_ids if measure else self.sudo().orm_group_field_ids
        if self.orm_all_fields:
            metadata = Model.fields_get(attributes=['string', 'type', 'selection'])
            result = {}
            for name, meta in metadata.items():
                field = Model._fields.get(name)
                if (meta['type'] in types and field and field.store and field.column_type
                        and not SENSITIVE_FIELD.search(name)):
                    result[name] = meta
            return result
        metadata = Model.fields_get(selected.mapped('name'), attributes=['string', 'type', 'selection']) if selected else {}
        for item in selected:
            field = Model._fields.get(item.name)
            if (item.model != Model._name or item.name not in metadata or not field
                    or field.type not in types or not field.store or not field.column_type
                    or SENSITIVE_FIELD.search(item.name)):
                raise ValidationError(self.env._('The statistics field is unavailable: %s', item.name))
        return metadata

    def _orm_metadata(self, Model, writing=False):
        metadata = super()._orm_metadata(Model, writing=writing)
        if self.orm_operation == 'read_group' and not writing:
            metadata.update(self._group_metadata(Model))
            metadata.update(self._group_metadata(Model, measure=True))
        return metadata

    def _orm_check(self):
        Model = super()._orm_check()
        if self.orm_operation == 'read_group':
            if not self.orm_all_fields and not self._group_metadata(Model):
                raise ValidationError(self.env._('Select at least one group by field.'))
            if len(self.orm_group_field_ids) > 3 or len(self.orm_measure_field_ids) > 7:
                raise ValidationError(self.env._('Choose at most three group by fields and seven measures.'))
            groups = self._group_metadata(Model)
            measures = self._group_metadata(Model, measure=True)
            for item in self.sudo().orm_group_field_ids:
                if item.model != Model._name or item.name not in groups:
                    raise ValidationError(self.env._('The statistics field is unavailable: %s', item.name))
            for item in self.sudo().orm_measure_field_ids:
                if item.model != Model._name or item.name not in measures:
                    raise ValidationError(self.env._('The statistics field is unavailable: %s', item.name))
        return Model

    def _group_defaults(self, Model, measure=False):
        metadata = self._group_metadata(Model, measure=measure)
        selected = self.sudo().orm_measure_field_ids if measure else self.sudo().orm_group_field_ids
        if measure:
            return [item.name + ':sum' for item in selected if item.name in metadata][:7]
        return [
            item.name + ':month' if metadata[item.name]['type'] in ('date', 'datetime') else item.name
            for item in selected if item.name in metadata
        ][:3]

    def _group_options(self, Model):
        groups = []
        for name, meta in sorted(self._group_metadata(Model).items()):
            groups.extend([name + ':' + interval for interval in DATE_INTERVALS]
                          if meta['type'] in ('date', 'datetime') else [name])
        measures = ['__count']
        measures.extend(name + ':' + fn for name in sorted(self._group_metadata(Model, measure=True)) for fn in AGGREGATIONS)
        return groups, measures

    def _orm_schema(self):
        if self.orm_operation != 'read_group':
            return super()._orm_schema()
        Model = self._orm_check()
        groups, measures = self._group_options(Model)
        defaults = self._group_defaults(Model)
        group_items, measure_items = {'type': 'string'}, {'type': 'string'}
        group_help, measure_help = '', ''
        if len(groups) <= GROUP_ENUM_LIMIT:
            group_items['enum'] = groups
        else:
            group_help = GROUPBY_RULE + ' '
        if len(measures) <= GROUP_ENUM_LIMIT:
            measure_items['enum'] = measures
        else:
            measure_help = AGGREGATE_RULE + ' '
        return {
            'type': 'object', 'additionalProperties': False,
            'required': ['groupby'] if self.orm_all_fields and not defaults else [],
            'properties': {
                'domain': {'type': 'array', 'description': 'Optional simple filters; combined with the fixed scope. Date/time filters use UTC.'},
                'groupby': {'type': 'array', 'items': group_items,
                            'minItems': 1, 'maxItems': 3, 'uniqueItems': True,
                            'description': group_help + 'Omit to use the configured groups (month for date fields).'},
                'aggregates': {'type': 'array', 'items': measure_items,
                               'minItems': 1, 'maxItems': 8, 'uniqueItems': True,
                               'description': measure_help + 'Omit for count plus sums of configured measures. Monetary results are always separated by currency.'},
            },
        }

    def _group_arguments(self, Model, arguments):
        if not isinstance(arguments, dict) or set(arguments) - {'domain', 'groupby', 'aggregates'}:
            raise ValidationError(self.env._('The tool arguments do not match its configuration.'))
        allowed_groups, allowed_measures = self._group_options(Model)
        defaults = self._group_defaults(Model)
        groupby = arguments.get('groupby', defaults)
        aggregates = arguments.get('aggregates', ['__count'] + self._group_defaults(Model, measure=True))
        for values, allowed, limit in ((groupby, allowed_groups, 3), (aggregates, allowed_measures, 8)):
            if (not isinstance(values, list) or not 1 <= len(values) <= limit
                    or any(not isinstance(value, str) or value not in allowed for value in values)
                    or len(set(values)) != len(values)):
                raise ValidationError(self.env._('Choose only configured statistics fields and supported functions.'))
        if len({spec.split(':')[0] for spec in groupby}) != len(groupby):
            raise ValidationError(self.env._('Choose only one interval for each date field.'))
        groupby = list(groupby)
        readable = self._orm_metadata(Model)
        for spec in aggregates:
            field = Model._fields.get(spec.split(':')[0])
            if field and field.type == 'monetary':
                currency = field.get_currency_field(Model)
                if currency not in readable or not Model._fields[currency].store:
                    raise ValidationError(self.env._('Include the currency field in readable fields before aggregating amounts.'))
                if currency not in groupby:
                    groupby.append(currency)
        return self._orm_scope(Model, arguments.get('domain', [])), groupby, list(aggregates)

    def _group_cell(self, value, meta):
        if isinstance(value, models.BaseModel):
            if not value:
                return self.env._('Not set')
            try:
                value.check_access('read')
                return value.display_name
            except AccessError:
                return self.env._('Restricted record')
        if value is False or value is None:
            return self.env._('No') if meta.get('type') == 'boolean' else self.env._('Not set')
        if meta.get('type') == 'selection':
            return str(dict(meta.get('selection', [])).get(value, value))
        if isinstance(value, (date, datetime)):
            return value.isoformat(sep=' ') if isinstance(value, datetime) else value.isoformat()
        return str(value)

    def _group_metric_cell(self, Model, spec, value, groupby, row):
        field = Model._fields.get(spec.split(':')[0])
        if field and field.type == 'monetary':
            currency = row[groupby.index(field.get_currency_field(Model))]
            if currency:
                currency.check_access('read')
                return f'{currency.round(value or 0):.{currency.decimal_places}f}'
        return str(value if value is not None else 0)

    def _group_report(self, Model, domain, groupby, aggregates):
        rows = Model._read_group(domain, groupby=groupby, aggregates=aggregates, limit=self.orm_limit + 1)
        truncated = len(rows) > self.orm_limit
        metadata = self._orm_metadata(Model)
        labels = {'__count': self.env._('Count'), 'sum': self.env._('Sum'), 'avg': self.env._('Average'),
                  'min': self.env._('Minimum'), 'max': self.env._('Maximum')}
        headers = [metadata[spec.split(':')[0]]['string'] + (' (%s)' % spec.split(':')[1] if ':' in spec else '') for spec in groupby]
        headers += [labels['__count'] if spec == '__count' else '%s (%s)' % (
            metadata[spec.split(':')[0]]['string'], labels[spec.split(':')[1]]) for spec in aggregates]

        def escape(value):
            return str(value).replace('\\', '\\\\').replace('|', '\\|').replace('\r', ' ').replace('\n', ' ')

        lines = [self.name, self.env._('Company: %s', self.env.company.display_name),
                 self.env._('Matching records: %s', Model.search_count(domain)),
                 '| ' + ' | '.join(map(escape, headers)) + ' |',
                 '| ' + ' | '.join(['---'] * len(headers)) + ' |']
        for row in rows[:self.orm_limit]:
            cells = [self._group_cell(value, metadata[spec.split(':')[0]]) for spec, value in zip(groupby, row)]
            cells += [self._group_metric_cell(Model, spec, value, groupby, row)
                      for spec, value in zip(aggregates, row[len(groupby):])]
            lines.append('| ' + ' | '.join(map(escape, cells)) + ' |')
        if not rows:
            lines.append(self.env._('No matching records.'))
        if truncated:
            lines.append(self.env._('Only the first %s groups are shown. Narrow the filters for the remaining groups.', self.orm_limit))
        return '\n\n'.join(lines[:3]) + '\n\n' + '\n'.join(lines[3:])

    def _orm_query(self, arguments):
        if self.orm_operation != 'read_group':
            return super()._orm_query(arguments)
        Model = self._orm_check()
        domain, groupby, aggregates = self._group_arguments(Model, arguments)
        return self._group_report(Model, domain, groupby, aggregates)

    @api.constrains('execution_type', 'model_id', 'orm_operation', 'orm_group_field_ids', 'orm_measure_field_ids')
    def _check_group_configuration(self):
        for tool in self.filtered(lambda item: item.execution_type == 'orm' and item.orm_operation == 'read_group'):
            tool._orm_check()

    @api.onchange('model_id')
    def _onchange_group_model(self):
        self.orm_group_field_ids = False
        self.orm_measure_field_ids = False

    orm_group_field_ids = fields.Many2many('ir.model.fields', 'qing_ai_tool_group_field_rel', string='Group By Fields')
    orm_measure_field_ids = fields.Many2many('ir.model.fields', 'qing_ai_tool_measure_field_rel', string='Measure Fields',
                                           help='Optional numeric measures. Counts are always available; sums are used by default.')
