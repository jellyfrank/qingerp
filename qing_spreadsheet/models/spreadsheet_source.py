import copy
import re

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


SOURCE_FIELD_TYPES = {'char', 'text', 'boolean', 'integer', 'float', 'monetary', 'date', 'datetime', 'selection', 'many2one'}


class QingSpreadsheetSource(models.Model):
    _name = 'qing.spreadsheet.source'
    _description = 'Spreadsheet Data Source Policy'
    _order = 'name, id'

    _model_unique = models.Constraint('unique(model_id)', 'Configure a model only once.')

    def _readable_fields(self):
        self.ensure_one()
        model = self.env[self.model_name]
        if not model.has_access('read'):
            return {}
        names = self.field_ids.mapped('name')
        return {name: value for name, value in model.fields_get(names).items()
                if value['type'] in SOURCE_FIELD_TYPES}

    def _validate_definition(self, definition, company_id):
        self.ensure_one()
        if not self.active or definition.get('model') != self.model_name:
            raise ValidationError(_('This data source is not enabled.'))
        allowed = self._readable_fields()
        if not allowed:
            raise ValidationError(_('You cannot read this data source.'))
        for key in ('columns', 'rows', 'measures', 'orderBy'):
            value = definition.get(key, [])
            if not isinstance(value, list) or len(value) > 100:
                raise ValidationError(_('Invalid data source fields.'))
            if any(not isinstance(item, (str, dict)) for item in value):
                raise ValidationError(_('Invalid data source field definition.'))
        requested = []
        for column in definition.get('columns', []):
            requested.append(column if isinstance(column, str) else column.get('fieldName', column.get('name')))
        for row in definition.get('rows', []):
            requested.append(row if isinstance(row, str) else row.get('fieldName', row.get('name')))
        for measure in definition.get('measures', []):
            if not isinstance(measure, dict):
                raise ValidationError(_('Invalid measure definition.'))
            field = measure.get('fieldName')
            if field != '__count':
                requested.append(field)
            if measure.get('aggregator') not in (None, 'sum', 'avg', 'min', 'max', 'count', 'count_distinct'):
                raise ValidationError(_('Unsupported aggregation.'))
        for order in definition.get('orderBy', []):
            if not isinstance(order, dict):
                raise ValidationError(_('Invalid ordering definition.'))
            requested.append(order.get('name'))
        if any(name not in allowed for name in requested):
            raise ValidationError(_('The data source contains a field that is not allowed.'))
        domain = definition.get('domain', [])
        if not isinstance(domain, list) or len(domain) > 100:
            raise ValidationError(_('Use a domain with at most 100 conditions.'))
        for term in domain:
            if isinstance(term, str) and term in ('&', '|', '!'):
                continue
            if not isinstance(term, (list, tuple)) or len(term) != 3:
                raise ValidationError(_('Invalid filter condition.'))
            if term[0] not in allowed or term[1] not in ('=', '!=', '>', '>=', '<', '<=', 'in', 'not in', 'like', 'ilike', 'not like', 'not ilike', '=?'):
                raise ValidationError(_('The filter uses an unsupported field or operator.'))
        try:
            fields.Domain(domain)
        except (ValueError, TypeError) as exc:
            raise ValidationError(_('Invalid filter domain.')) from exc
        # Prevent imported contexts from disabling rules or expanding the company scope.
        definition['context'] = {'allowed_company_ids': [company_id]}
        return definition

    def _query(self, method, args, kwargs, company):
        """Constrain native list/pivot reads without widening the caller's ORM rights."""
        self.ensure_one()
        if method not in ('web_search_read', 'search_read', 'search_count', 'formatted_read_group', 'formatted_read_grouping_sets'):
            raise ValidationError(_('Unsupported spreadsheet query.'))
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise ValidationError(_('Invalid spreadsheet query.'))
        kwargs = copy.deepcopy(kwargs)
        domain = args[0] if method == 'search_count' and args else kwargs.get('domain', [])
        if args and not (method == 'search_count' and len(args) == 1):
            raise ValidationError(_('Positional query arguments are not supported.'))
        allowed = self._readable_fields()
        definition = {'model': self.model_name, 'domain': domain}
        self._validate_definition(definition, company.id)
        if method in ('web_search_read', 'search_read'):
            specification = kwargs.get('specification', {})
            names = kwargs.get('fields', []) if method == 'search_read' else list(specification)
            if not names or any(name not in allowed and name != 'id' for name in names):
                raise ValidationError(_('The data source contains a field that is not allowed.'))
            for name, spec in specification.items():
                if spec.get('fields') and (set(spec['fields']) - {'id', 'display_name'} or any(spec['fields'].values())):
                    raise ValidationError(_('Nested business fields are not supported.'))
            limit = kwargs.get('limit', 0)
            if type(limit) is not int or not 1 <= limit <= 5000:
                raise ValidationError(_('Record lists are limited to 5,000 rows.'))
            order = kwargs.get('order', '') or ''
            for term in order.split(','):
                if term.strip() and not re.fullmatch(r'[a-zA-Z_][a-zA-Z_0-9]*(?: (?:asc|desc))?', term.strip(), re.I):
                    raise ValidationError(_('Invalid ordering definition.'))
                if term.strip() and term.strip().split()[0] not in allowed and term.strip().split()[0] != 'id':
                    raise ValidationError(_('The ordering field is not allowed.'))
        if method.startswith('formatted_read_group'):
            grouping_sets = kwargs.get('grouping_sets', [kwargs.get('groupby', [])])
            if not isinstance(grouping_sets, list) or len(grouping_sets) > 16:
                raise ValidationError(_('Use at most 16 grouping combinations.'))
            for grouping in grouping_sets:
                if not isinstance(grouping, list) or len(grouping) > 4:
                    raise ValidationError(_('Use at most four grouping fields.'))
                for name in grouping:
                    if name.split(':')[0] not in allowed:
                        raise ValidationError(_('The grouping field is not allowed.'))
            for aggregate in kwargs.get('aggregates', []):
                if aggregate == '__count':
                    continue
                match = re.fullmatch(r'([a-zA-Z_][a-zA-Z_0-9]*):(sum|avg|min|max|count|count_distinct)', aggregate)
                if not match or match[1] not in allowed:
                    raise ValidationError(_('The aggregation field is not allowed.'))
            if method == 'formatted_read_group':
                kwargs['limit'] = min(kwargs.get('limit') or 5001, 5001)
        if method == 'search_count':
            kwargs['limit'] = min(kwargs.get('limit') or 10000, 10000)
        model = self.env[self.model_name].with_company(company).with_context(allowed_company_ids=[company.id])
        # Only the scoped server context is passed to native business models.
        kwargs.pop('context', None)
        allowed_kwargs = {
            'web_search_read': {'domain', 'specification', 'offset', 'limit', 'order', 'count_limit'},
            'search_read': {'domain', 'fields', 'offset', 'limit', 'order'},
            'search_count': {'domain', 'limit'},
            'formatted_read_group': {'domain', 'groupby', 'aggregates', 'offset', 'limit', 'order'},
            'formatted_read_grouping_sets': {'domain', 'grouping_sets', 'aggregates', 'order'},
        }
        if set(kwargs) - allowed_kwargs[method]:
            raise ValidationError(_('Unsupported spreadsheet query options.'))
        result = getattr(model, method)(*args, **kwargs)
        groups = result if method == 'formatted_read_grouping_sets' else [result] if method == 'formatted_read_group' else []
        if any(len(group) > 5000 for group in groups):
            raise ValidationError(_('Too many pivot groups. Add filters or use fewer grouping fields.'))
        return result

    active = fields.Boolean(default=True)
    field_ids = fields.Many2many('ir.model.fields', string='Allowed Fields', required=True,
                                 domain="[('model_id', '=', model_id), ('ttype', 'in', ['char', 'text', 'boolean', 'integer', 'float', 'monetary', 'date', 'datetime', 'selection', 'many2one'])]")
    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade')
    model_name = fields.Char(related='model_id.model', store=True, index=True)
    name = fields.Char(required=True, translate=True)

    @api.constrains('field_ids', 'model_id')
    def _check_fields(self):
        for source in self:
            if not source.field_ids or any(field.model_id != source.model_id or field.ttype not in SOURCE_FIELD_TYPES for field in source.field_ids):
                raise ValidationError(_('Choose supported fields from the selected model.'))
            if source.model_name not in self.env or self.env[source.model_name]._transient:
                raise ValidationError(_('Transient models cannot be spreadsheet data sources.'))

    @api.model
    def available_sources(self):
        sources = self.search([])
        result = []
        for source in sources:
            readable = source._readable_fields()
            if readable:
                result.append({'id': source.id, 'name': source.name, 'model': source.model_name,
                               'fields': [{'name': name, **value} for name, value in readable.items()]})
        return result

    def validate_source(self, definition, company_id):
        self.ensure_one()
        self.check_access('read')
        if company_id not in self.env.companies.ids:
            raise ValidationError(_('Choose an allowed company.'))
        return self._validate_definition(definition, company_id)
