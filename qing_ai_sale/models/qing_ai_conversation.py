"""A small, allow-listed sales workflow for the Qing AI conversation."""

import hashlib
import json
import re
from datetime import timedelta

from odoo import Command, fields, models
from odoo.exceptions import AccessError, UserError


TOOL_DESCRIPTIONS = {
    'create_customer': (
        'Preview a new customer/contact. arguments: '
        '{"name": string, "phone": string, "email": string, "street": string, '
        '"street2": string, "city": string, "zip": string, "website": string, "comment": string}. '
        'Only name is required; omit other keys unless the user supplied them.'
    ),
    'update_customer': (
        'Preview editing an existing customer. arguments: {"customer": exact name, '
        '"values": {"name": string, "phone": string, "email": string, "street": string, '
        '"street2": string, "city": string, "zip": string, "website": string, "comment": string}}. '
        'Use customer_id instead of customer if the user supplied a record ID. '
        'Include only the fields the user explicitly asks to change; empty string clears a field.'
    ),
    'find_customers': (
        'Legacy quick lookup only by customer name, phone or email. arguments: '
        '{"search": optional string}. Use the generic customer query, count or group tools '
        'for geography, relations and every other field.'
    ),
    'find_products': (
        'Legacy quick lookup only by sellable product name or SKU. arguments: {"search": string}. '
        'Use the generic product tools for every other field and statistics.'
    ),
    'find_quotes': (
        'Legacy quick lookup only by SO number, customer or state. arguments: '
        '{"search": optional string, "state": optional "draft"|"sent"|"sale"|"cancel"}. '
        'Use the generic sales order tools for every other field and statistics.'
    ),
    'find_activities': 'List follow-up activities for one customer. arguments: {"customer": string}',
    'create_followup': (
        'Preview a to-do activity for one customer. arguments: '
        '{"customer": string, "summary": string, "due_date": "YYYY-MM-DD", "note": string}'
    ),
    'create_quote_draft': (
        'Preview a new SO sales order as a draft quotation (never confirm it). '
        'arguments: {"customer": string, '
        '"lines": [{"product": string, "quantity": number}]}'
    ),
}
CUSTOMER_PROPERTIES = {
    key: {'type': 'string', 'description': description}
    for key, description in (
        ('name', 'Customer or contact name'), ('phone', 'Phone number'), ('email', 'Email address'),
        ('street', 'Street'), ('street2', 'Street line 2'), ('city', 'City'), ('zip', 'Postal code'),
        ('website', 'Website'), ('comment', 'Internal note'),
    )
}
# Native function definitions mirror the argument contracts validated by the dispatch methods.
TOOL_SUMMARIES = {
    'create_customer': 'Preview a new customer/contact. Only name is required; include only details '
                       'the user supplied.',
    'update_customer': 'Preview editing an existing customer, identified by exact name or record ID. '
                       'Include only the fields the user asks to change; an empty string clears a field.',
    'find_customers': 'Legacy quick lookup by customer name, phone or email. Prefer the generic customer '
                      'tools for other fields, counts and statistics.',
    'find_products': 'Legacy quick lookup of sellable products by name or SKU.',
    'find_quotes': 'Legacy quick lookup of quotations by SO number, customer or state.',
    'find_activities': 'List follow-up activities for one customer.',
    'create_followup': 'Preview a to-do activity for one customer.',
    'create_quote_draft': 'Preview a new draft quotation (never confirmed). Products must already exist; '
                          'prices, taxes and totals come from Odoo.',
}
TOOL_SCHEMAS = {
    'create_customer': {
        'type': 'object', 'properties': CUSTOMER_PROPERTIES, 'required': ['name'],
        'additionalProperties': False,
    },
    'update_customer': {
        'type': 'object',
        'properties': {
            'customer': {'type': 'string', 'description': 'Exact customer name'},
            'customer_id': {'type': 'integer', 'description': 'Customer record ID, when known'},
            'values': {'type': 'object', 'properties': CUSTOMER_PROPERTIES, 'additionalProperties': False},
        },
        'required': ['values'], 'additionalProperties': False,
    },
    'find_customers': {
        'type': 'object', 'properties': {'search': {'type': 'string'}}, 'additionalProperties': False,
    },
    'find_products': {
        'type': 'object', 'properties': {'search': {'type': 'string', 'description': 'Name or SKU'}},
        'required': ['search'], 'additionalProperties': False,
    },
    'find_quotes': {
        'type': 'object',
        'properties': {'search': {'type': 'string'},
                       'state': {'type': 'string', 'enum': ['draft', 'sent', 'sale', 'cancel']}},
        'additionalProperties': False,
    },
    'find_activities': {
        'type': 'object', 'properties': {'customer': {'type': 'string'}}, 'required': ['customer'],
        'additionalProperties': False,
    },
    'create_followup': {
        'type': 'object',
        'properties': {'customer': {'type': 'string'}, 'summary': {'type': 'string'},
                       'due_date': {'type': 'string', 'description': 'YYYY-MM-DD'},
                       'note': {'type': 'string'}},
        'required': ['customer', 'summary', 'due_date'], 'additionalProperties': False,
    },
    'create_quote_draft': {
        'type': 'object',
        'properties': {
            'customer': {'type': 'string', 'description': 'Exact customer name'},
            'lines': {'type': 'array', 'minItems': 1, 'maxItems': 10, 'items': {
                'type': 'object',
                'properties': {'product': {'type': 'string', 'description': 'Exact product name or SKU'},
                               'quantity': {'type': 'number'}},
                'required': ['product', 'quantity'], 'additionalProperties': False,
            }},
        },
        'required': ['customer', 'lines'], 'additionalProperties': False,
    },
}
TOOL_ACCESS = {
    'create_customer': ('res.partner', 'create'),
    'update_customer': ('res.partner', 'write'),
    'find_customers': ('res.partner', 'read'),
    'find_products': ('product.product', 'read'),
    'find_quotes': ('sale.order', 'read'),
    'find_activities': ('mail.activity', 'read'),
    'create_followup': ('mail.activity', 'create'),
    'create_quote_draft': ('sale.order', 'create'),
}
WRITE_TOOLS = {'create_customer', 'update_customer', 'create_followup', 'create_quote_draft'}
PREVIEW_LIFETIME = timedelta(minutes=15)
CUSTOMER_FIELD_LIMITS = {
    'name': 160, 'phone': 64, 'email': 254, 'street': 200, 'street2': 200,
    'city': 100, 'zip': 32, 'website': 254, 'comment': 2000,
}
QUERY_LIMIT = 10


def escape_markdown(value):
    return re.sub(r'([\\`*_{}\[\]()#+.!|>~-])', r'\\\1', str(value))


class QingAiConversation(models.Model):
    _inherit = 'qing.ai.conversation'

    def _allowed_sale_tools(self):
        if self.env.user.share:
            return set()
        if self.agent_id and self.agent_id not in self.agent_id.available_for_user():
            return set()
        allowed = set()
        for key in self._agent_tool_keys() & TOOL_ACCESS.keys():
            model_name, operation = TOOL_ACCESS[key]
            try:
                self.env[model_name].check_access(operation)
            except AccessError:
                continue
            allowed.add(key)
        return allowed

    def _business_tool_descriptions(self, native=False):
        sale_catalog = TOOL_SUMMARIES if native else TOOL_DESCRIPTIONS
        return {**super()._business_tool_descriptions(native=native),
                **{key: sale_catalog[key] for key in self._allowed_sale_tools()}}

    def _business_tool_specs(self):
        return {**super()._business_tool_specs(),
                **{key: {'description': TOOL_SUMMARIES[key], 'parameters': TOOL_SCHEMAS[key]}
                   for key in self._allowed_sale_tools()}}

    def _business_enabled(self):
        return bool(self._allowed_sale_tools()) or super()._business_enabled()

    def _dispatch_business_plan(self, key, arguments):
        if key in self._allowed_sale_tools():
            if key in WRITE_TOOLS:
                return self._prepare_business_operation(key, arguments)
            return self._run_business_query(key, arguments)
        return super()._dispatch_business_plan(key, arguments)

    def _pending_business_operations(self):
        self.ensure_one()
        domain = [
            ('conversation_id', '=', self.id),
            ('owner_id', '=', self.env.uid),
            ('company_id', '=', self.env.company.id),
            ('state', '=', 'pending'),
            ('expires_at', '>', fields.Datetime.now()),
        ]
        operations = self.env['qing.ai.sale.operation'].search(domain, limit=3)
        return [{
            'id': item.id, 'preview': item.preview,
            'confirm_label': self.env._('Confirm changes') if item.tool_key == 'update_customer'
            else self.env._('Confirm and create'),
        } for item in operations] + super()._pending_business_operations()

    def _search_term(self, arguments, key='search'):
        if set(arguments) != {key} or not isinstance(arguments[key], str):
            raise UserError(self.env._('A specific search term is required.'))
        term = arguments[key].strip()
        if not 2 <= len(term) <= 100:
            raise UserError(self.env._('A specific search term is required.'))
        return term

    def _resolve_record(self, model_name, term, extra_domain=None):
        if not isinstance(term, str) or not 1 <= len(term.strip()) <= 100:
            raise UserError(self.env._('Specify an exact customer or product name.'))
        Model = self.env[model_name].with_context(qing_role_company_id=self.env.company.id)
        Model.check_access('read')
        domain = [('company_id', 'in', [False, self.env.company.id])]
        domain += list(extra_domain or [])
        field_name = 'default_code' if model_name == 'product.product' else 'name'
        exact = Model.search(domain + [(field_name, '=ilike', term.strip())], limit=2)
        if len(exact) == 1:
            return exact
        matches = exact or Model.search(domain + [('name', 'ilike', term.strip())], limit=2)
        if not matches:
            raise UserError(self.env._('No accessible customer or product matches that name.'))
        if len(matches) != 1:
            raise UserError(self.env._('Several records match. Use an exact customer name or product SKU.'))
        return matches

    def _customer_domain(self):
        Partner = self.env['res.partner']
        role_field = 'qing_is_customer' if 'qing_is_customer' in Partner._fields else 'customer_rank'
        return [
            ('company_id', 'in', [False, self.env.company.id]),
            (role_field, '=', True) if role_field == 'qing_is_customer' else (role_field, '>', 0),
        ]

    def _query_arguments(self, arguments, order=False):
        allowed = {'search', 'state'} if order else {'search'}
        if not isinstance(arguments, dict) or set(arguments) - allowed:
            raise UserError(self.env._('The query filters are invalid.'))
        term = arguments.get('search', '')
        if not isinstance(term, str) or len(term) > 100:
            raise UserError(self.env._('The search text must contain at most 100 characters.'))
        state = arguments.get('state')
        if state is not None and state not in ('draft', 'sent', 'sale', 'cancel'):
            raise UserError(self.env._('The sales order state is invalid.'))
        return term.strip(), state

    def _run_business_query(self, key, arguments):
        total = None
        if key == 'find_customers':
            term, _state = self._query_arguments(arguments)
            Partner = self.env['res.partner'].with_context(qing_role_company_id=self.env.company.id)
            domain = self._customer_domain()
            if term:
                domain += ['|', '|', ('name', 'ilike', term),
                           ('phone', 'ilike', term), ('email', 'ilike', term)]
            total = Partner.search_count(domain)
            records = Partner.search(domain, limit=QUERY_LIMIT, order='id desc')
            lines = [
                f'- {escape_markdown(item.display_name)} (ID {item.id})'
                f' · {escape_markdown(item.phone or "—")} · {escape_markdown(item.email or "—")}'
                for item in records
            ]
        elif key == 'find_products':
            term = self._search_term(arguments)
            domain = [('sale_ok', '=', True), ('company_id', 'in', [False, self.env.company.id]), '|',
                      ('name', 'ilike', term), ('default_code', 'ilike', term)]
            records = self.env['product.product'].search(domain, limit=6)
            lines = [
                f'- {escape_markdown(item.display_name)} (ID {item.id})'
                for item in records
            ]
        elif key == 'find_quotes':
            term, state = self._query_arguments(arguments, order=True)
            SaleOrder = self.env['sale.order']
            domain = [('company_id', '=', self.env.company.id)]
            if term:
                domain += ['|', ('name', 'ilike', term), ('partner_id.name', 'ilike', term)]
            if state:
                domain += [('state', '=', state)]
            total = SaleOrder.search_count(domain)
            records = SaleOrder.search(domain, limit=QUERY_LIMIT, order='id desc')
            labels = dict(SaleOrder._fields['state']._description_selection(self.env))
            lines = [
                f'- {escape_markdown(item.name)} · {escape_markdown(item.partner_id.display_name)} '
                f'· {escape_markdown(labels[item.state])} · {item.amount_total:.2f} '
                f'{escape_markdown(item.currency_id.name)}'
                for item in records
            ]
            if len(records) == 1 and term.casefold() == records.name.casefold():
                lines += [
                    f'  - {escape_markdown(line.product_id.display_name)} '
                    f'× {line.product_uom_qty:g} · {line.price_subtotal:.2f}'
                    for line in records.order_line.filtered(lambda line: not line.display_type)[:QUERY_LIMIT]
                ]
        elif key == 'find_activities':
            customer = self._resolve_record('res.partner', self._search_term(arguments, 'customer'))
            domain = [('res_model', '=', 'res.partner'), ('res_id', '=', customer.id)]
            records = self.env['mail.activity'].search(domain, limit=6)
            lines = [
                f'- {escape_markdown(item.summary or item.activity_type_id.name)} '
                f'· {item.date_deadline} · {escape_markdown(item.user_id.name)}'
                for item in records
            ]
        else:
            raise UserError(self.env._('The AI returned an unsupported business request.'))
        if total is not None:
            return self.env._(
                'Matching records in the current company: %(count)s. Showing up to %(limit)s.',
                count=total, limit=QUERY_LIMIT,
            ) + ('\n' + '\n'.join(lines) if lines else '')
        if not lines:
            return self.env._('No accessible matching records were found.')
        return self.env._('Matching records:') + '\n' + '\n'.join(lines)

    def _operation_fingerprint(self, records):
        snapshot = []
        for record in sorted(records, key=lambda record: (record._name, record.id)):
            entry = [record._name, record.id, str(record.write_date or '')]
            if record._name == 'product.product':
                entry.extend([record.name, record.list_price, record.sale_ok])
            elif record._name == 'res.partner':
                entry.extend([record.name, record.property_product_pricelist.id])
                entry.extend(record[key] or '' for key in CUSTOMER_FIELD_LIMITS)
                if 'qing_is_customer' in record._fields:
                    entry.append(record.qing_is_customer)
            snapshot.append(entry)
        return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()

    def _prepare_business_operation(self, key, arguments):
        self.ensure_one()
        if key == 'create_customer':
            values, records, preview = self._prepare_customer(arguments)
        elif key == 'update_customer':
            values, records, preview = self._prepare_customer_update(arguments)
        elif key == 'create_followup':
            values, records, preview = self._prepare_followup(arguments)
        elif key == 'create_quote_draft':
            values, records, preview = self._prepare_quote(arguments)
        else:
            raise UserError(self.env._('The AI returned an unsupported business request.'))
        self.env['qing.ai.sale.operation'].sudo().create({
            'arguments': values,
            'company_id': self.env.company.id,
            'conversation_id': self.id,
            'expires_at': fields.Datetime.now() + PREVIEW_LIFETIME,
            'fingerprint': self._operation_fingerprint(records),
            'owner_id': self.env.uid,
            'preview': preview,
            'tool_key': key,
        })
        return self.env._('Review the proposed business action below and confirm it to apply the changes.')

    def _prepare_customer(self, arguments):
        values = self._customer_values(arguments, creating=True)
        self._check_customer_creation(values)
        self._check_customer_duplicate(values)
        preview = self.env._('Create customer in %(company)s:', company=self.env.company.display_name)
        preview += '\n' + self._customer_value_preview(values)
        return values, [], preview

    def _customer_values(self, arguments, creating=False):
        if not isinstance(arguments, dict) or not arguments or set(arguments) - CUSTOMER_FIELD_LIMITS.keys():
            raise UserError(self.env._('The customer details are invalid.'))
        if creating and not arguments.get('name'):
            raise UserError(self.env._('A customer name is required.'))
        values = {}
        for key, value in arguments.items():
            if not isinstance(value, str) or len(value.strip()) > CUSTOMER_FIELD_LIMITS[key]:
                raise UserError(self.env._('The customer %(field)s is invalid.', field=key))
            if key == 'name' and not value.strip():
                raise UserError(self.env._('A customer name is required.'))
            values[key] = value.strip()
        return values

    def _customer_value_preview(self, values):
        labels = self.env['res.partner'].fields_get(list(values), attributes=['string'])
        return '\n'.join(f'{labels[key]["string"]}: {value or "—"}' for key, value in values.items())

    def _check_customer_creation(self, values):
        Partner = self.env['res.partner']
        Partner.check_access('create')
        if 'qing_is_customer' in Partner._fields:
            if not Partner._qing_is_manager():
                raise AccessError(self.env._(
                    'Creating a customer identity requires partner manager permission. '
                    'Contact your administrator.'
                ))
            Partner._qing_check_maintenance(dict(values, qing_is_customer=True), creating=True)

    def _prepare_customer_update(self, arguments):
        if (not isinstance(arguments, dict) or set(arguments) not in (
                {'customer', 'values'}, {'customer_id', 'values'})):
            raise UserError(self.env._('Specify one customer and the fields to update.'))
        Partner = self.env['res.partner'].with_context(qing_role_company_id=self.env.company.id)
        domain = self._customer_domain()
        if 'customer_id' in arguments:
            customer_id = arguments['customer_id']
            if type(customer_id) is not int or customer_id <= 0:
                raise UserError(self.env._('The customer identifier is invalid.'))
            customer = Partner.search(domain + [('id', '=', customer_id)], limit=1)
            if not customer:
                raise UserError(self.env._('No accessible customer matches that identifier.'))
        else:
            customer = self._resolve_record('res.partner', arguments['customer'], domain)
        customer.check_access('write')
        values = self._customer_values(arguments['values'])
        if 'qing_is_customer' in Partner._fields:
            customer._qing_check_maintenance(values)
        self._check_customer_duplicate(values, exclude_id=customer.id)
        preview = self.env._('Update customer %(customer)s (ID %(id)s):',
                             customer=customer.display_name, id=customer.id)
        preview += '\n' + self._customer_value_preview(values)
        return {'customer_id': customer.id, 'values': values}, [customer], preview

    def _check_customer_duplicate(self, values, exclude_id=None):
        Partner = self.env['res.partner']
        Partner.check_access('read')
        company_domain = [('company_id', 'in', [False, self.env.company.id])]
        if exclude_id:
            company_domain.append(('id', '!=', exclude_id))
        identifiers = [(field, values[field]) for field in ('phone', 'email') if values.get(field)]
        if not identifiers and values.get('name'):
            identifiers = [('name', values['name'])]
        for field, value in identifiers:
            if Partner.search_count(company_domain + [(field, '=ilike', value)], limit=1):
                raise UserError(self.env._('An accessible customer with the same %(field)s already exists.',
                                           field=field))

    def _prepare_followup(self, arguments):
        if not isinstance(arguments, dict) or set(arguments) - {'customer', 'summary', 'due_date', 'note'}:
            raise UserError(self.env._('The follow-up details are invalid.'))
        customer = self._resolve_record('res.partner', arguments.get('customer'))
        summary = arguments.get('summary')
        note = arguments.get('note') or ''
        if not isinstance(summary, str) or not 1 <= len(summary.strip()) <= 120:
            raise UserError(self.env._('A short follow-up summary is required.'))
        if not isinstance(note, str) or len(note) > 2000:
            raise UserError(self.env._('The follow-up note is too long.'))
        try:
            due_date = fields.Date.from_string(arguments.get('due_date'))
        except (TypeError, ValueError) as exc:
            raise UserError(self.env._('Use a valid follow-up due date.')) from exc
        today = fields.Date.context_today(self)
        if not due_date or not today <= due_date <= today + timedelta(days=365):
            raise UserError(self.env._('The follow-up due date must be within one year.'))
        values = {
            'customer_id': customer.id,
            'summary': summary.strip(),
            'note': note.strip(),
            'due_date': fields.Date.to_string(due_date),
        }
        preview = self.env._(
            'Follow-up for %(customer)s on %(date)s: %(summary)s. Assigned to you. Note: %(note)s',
            customer=customer.display_name, date=values['due_date'],
            summary=values['summary'], note=values['note'] or '—',
        )
        return values, [customer], preview

    def _prepare_quote(self, arguments):
        if not isinstance(arguments, dict) or set(arguments) != {'customer', 'lines'}:
            raise UserError(self.env._('The quotation details are invalid.'))
        customer = self._resolve_record('res.partner', arguments['customer'])
        if 'qing_is_customer' in customer._fields:
            customer._qing_check_business_role('customer', self.env.company)
        lines = arguments['lines']
        if not isinstance(lines, list) or not 1 <= len(lines) <= 10:
            raise UserError(self.env._('A quotation requires 1 to 10 product lines.'))
        values = {'customer_id': customer.id, 'lines': []}
        products = self.env['product.product']
        for line in lines:
            if not isinstance(line, dict) or set(line) != {'product', 'quantity'}:
                raise UserError(self.env._('A quotation line is invalid.'))
            quantity = line['quantity']
            if (isinstance(quantity, bool) or not isinstance(quantity, (int, float))
                    or not 0 < quantity <= 10000):
                raise UserError(self.env._('A quotation quantity is invalid.'))
            product = self._resolve_record(
                'product.product', line['product'], [('sale_ok', '=', True)],
            )
            products |= product
            values['lines'].append({'product_id': product.id, 'quantity': quantity})
        pricelist = customer.property_product_pricelist
        records = [customer, *products, pricelist]
        names = {product.id: product.display_name for product in products}
        item_text = ', '.join(
            f'{names[line["product_id"]]} × {line["quantity"]}' for line in values['lines']
        )
        preview = self.env._(
            'Draft quotation for %(customer)s: %(lines)s. Pricelist: %(pricelist)s. '
            'Odoo will calculate prices, taxes and totals when the draft is created.',
            customer=customer.display_name, lines=item_text, pricelist=pricelist.display_name,
        )
        return values, records, preview

    def _confirm_business_operation(self, operation_id):
        self.ensure_one()
        self._check_owner()
        if type(operation_id) is not int or operation_id <= 0:
            raise UserError(self.env._('The proposed business action is invalid.'))
        self.env.cr.execute('SELECT id FROM qing_ai_sale_operation WHERE id = %s FOR UPDATE', [operation_id])
        operation = self.env['qing.ai.sale.operation'].sudo().browse(operation_id).exists()
        if not operation or operation.conversation_id.id != self.id or not (
            operation.owner_id == self.env.user and operation.company_id == self.env.company
        ):
            raise AccessError(self.env._('The proposed business action is not yours.'))
        operation.invalidate_recordset()
        if operation.state == 'done':
            return self.assistant_open()
        if operation.state != 'pending' or operation.expires_at <= fields.Datetime.now():
            raise UserError(self.env._('This business action has expired. Ask again for a new preview.'))
        if operation.tool_key not in self._allowed_sale_tools():
            raise AccessError(self.env._('You are no longer allowed to use this AI sales tool.'))
        records = self._operation_records(operation)
        if operation.fingerprint != self._operation_fingerprint(records):
            raise UserError(self.env._('The customer or product changed. Ask again for a new preview.'))
        if operation.tool_key == 'create_customer':
            result = self._confirm_customer(operation.arguments)
        elif operation.tool_key == 'update_customer':
            result = self._confirm_customer_update(operation.arguments)
        elif operation.tool_key == 'create_followup':
            result = self._confirm_followup(operation.arguments)
        elif operation.tool_key == 'create_quote_draft':
            result = self._confirm_quote(operation.arguments)
        else:
            raise UserError(self.env._('The proposed business action is invalid.'))
        operation.write({'state': 'done', 'result_model': result._name, 'result_id': result.id})
        description = (
            {'create_customer': self.env._('customer'),
             'update_customer': self.env._('customer'),
             'create_followup': self.env._('follow-up activity'),
             'create_quote_draft': self.env._('quotation draft')}[operation.tool_key]
        )
        content = self.env._('Created %(type)s %(document)s (ID %(id)s).',
                            type=description, document=result.display_name, id=result.id)
        if operation.tool_key == 'update_customer':
            content = self.env._('Updated customer %(document)s (ID %(id)s).',
                                document=result.display_name, id=result.id)
        self.env['qing.ai.message'].sudo().create({
            'content': content,
            'conversation_id': self.id,
            'role': 'assistant', 'state': 'done',
        })
        self.last_message_at = fields.Datetime.now()
        self._continue_pending_plan()
        return self.assistant_open()

    def _operation_records(self, operation):
        arguments = operation.arguments
        if operation.tool_key == 'create_customer':
            return []
        Partner = self.env['res.partner'].with_context(qing_role_company_id=self.env.company.id)
        customer = Partner.search([
            ('id', '=', arguments['customer_id']),
            ('company_id', 'in', [False, self.env.company.id]),
        ], limit=1)
        if not customer:
            raise UserError(self.env._('The customer is no longer available.'))
        customer.check_access('read')
        if operation.tool_key in ('create_followup', 'update_customer'):
            return [customer]
        if operation.tool_key != 'create_quote_draft':
            raise UserError(self.env._('The proposed business action is invalid.'))
        product_ids = {item['product_id'] for item in arguments['lines']}
        products = self.env['product.product'].browse(list(product_ids)).exists()
        if len(products) != len(product_ids):
            raise UserError(self.env._('A product is no longer available.'))
        products.check_access('read')
        return [customer, *products, customer.property_product_pricelist]

    def _confirm_customer(self, arguments):
        Partner = self.env['res.partner'].with_context(qing_role_company_id=self.env.company.id)
        values = self._customer_values(arguments, creating=True)
        self._check_customer_creation(values)
        self._check_customer_duplicate(values)
        values['company_id'] = self.env.company.id
        if 'qing_is_customer' in Partner._fields:
            values['qing_is_customer'] = True
        else:
            values['customer_rank'] = 1
        return Partner.create(values)

    def _confirm_customer_update(self, arguments):
        Partner = self.env['res.partner'].with_context(qing_role_company_id=self.env.company.id)
        domain = self._customer_domain() + [('id', '=', arguments['customer_id'])]
        customer = Partner.search(domain, limit=1)
        if not customer:
            raise UserError(self.env._('The customer is no longer available.'))
        customer.check_access('write')
        values = self._customer_values(arguments['values'])
        self._check_customer_duplicate(values, exclude_id=customer.id)
        customer.write(values)
        return customer

    def _confirm_followup(self, arguments):
        Activity = self.env['mail.activity']
        Activity.check_access('create')
        activity = Activity.create({
            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
            'date_deadline': arguments['due_date'],
            'note': arguments['note'],
            'res_model_id': self.env['ir.model']._get('res.partner').id,
            'res_id': arguments['customer_id'],
            'summary': arguments['summary'],
            'user_id': self.env.uid,
        })
        return activity

    def _confirm_quote(self, arguments):
        SaleOrder = self.env['sale.order']
        SaleOrder.check_access('create')
        order = SaleOrder.create({
            'partner_id': arguments['customer_id'],
            'company_id': self.env.company.id,
            'order_line': [Command.create({
                'product_id': item['product_id'],
                'product_uom_qty': item['quantity'],
            }) for item in arguments['lines']],
        })
        if order.state != 'draft':
            raise UserError(self.env._('The quotation was not created as a draft.'))
        return order

    def assistant_confirm_operation(self, operation_id):
        if isinstance(operation_id, str) and operation_id.startswith('orm:'):
            return super().assistant_confirm_operation(operation_id)
        # action_send records UserError as a chat message; roll back partial ORM
        # writes here before the outer handler catches an exception.
        with self.env.cr.savepoint():
            return self._confirm_business_operation(operation_id)
