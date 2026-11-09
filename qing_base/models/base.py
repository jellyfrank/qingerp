import json
from contextvars import ContextVar
from datetime import date, datetime

from odoo import api, models
from odoo.http import request
from ..tools import (
    RestrictedComputeError,
    are_computes_strict,
    is_observing_computes,
    observe_compute,
    observe_computes,
    strict_computes,
)


EXCLUDED_AUDIT_MODELS = frozenset({
    'bus.bus',
    'ir.cron',
    'ir.cron.progress',
    'ir.logging',
    'ir.model',
    'ir.model.data',
    'ir.model.fields',
    'ir.module.module',
    'mail.followers',
    'mail.notification',
    'qing.audit.log',
})
AUDIT_VALUE_TYPES = frozenset({
    'boolean', 'char', 'date', 'datetime', 'float', 'integer',
    'many2one', 'monetary', 'selection',
})
SENSITIVE_FIELD_FRAGMENTS = (
    'password', 'secret', 'token', 'credential', 'private_key', 'api_key',
    'access_key', 'session', 'signature', 'otp', 'totp', 'email', 'phone',
    'mobile', 'street', 'iban', 'account_number', 'vat',
)
MAX_AUDIT_VALUE_FIELDS = 20
MAX_AUDIT_VALUE_RECORDS = 100
MAX_AUDIT_STRING_LENGTH = 256
AUDIT_INSERT_BATCH_SIZE = 100
RESTRICTED_FIELDS_KEY = '__qing_restricted_fields'
MAX_READ_ATTEMPTS = 1024
READ_BUDGET = ContextVar('qing_restricted_read_budget', default=None)


class ReadBudget:
    def __init__(self):
        self.attempts = 0
        self.error = None


class Base(models.AbstractModel):
    _inherit = 'base'

    def _compute_field_value(self, field):
        if not is_observing_computes():
            return super()._compute_field_value(field)
        with observe_compute(self, field):
            return super()._compute_field_value(field)

    def _qing_notification_action(self, title, message='', notification_type='info', sticky=False,
                                  next_action=None):
        """Return a client action showing a notification, optionally followed by ``next_action``."""
        params = {'title': title, 'message': message, 'type': notification_type, 'sticky': sticky}
        if next_action:
            params['next'] = next_action
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': params}

    def _qing_message_action(self, title, message):
        """Show an informational message that stays until the user closes it."""
        return self._qing_notification_action(title, message, sticky=True)

    def _qing_view_action(self, action_xmlid, form_view_xmlid=None):
        """Open these records with a window action: one record in a form, several in a list.

        Callers check record access first; the action metadata is read like
        ``_for_xml_id`` does for the web client.
        """
        action = self.env['ir.actions.act_window']._for_xml_id(action_xmlid)
        if len(self) == 1:
            form_view = self.env.ref(form_view_xmlid) if form_view_xmlid else False
            action.update({
                'res_id': self.id,
                'view_mode': 'form',
                'views': [(form_view.id if form_view else False, 'form')],
            })
            return action
        action['domain'] = [('id', 'in', self.ids)]
        return action

    def _qing_read_attempt(self, callback):
        budget = READ_BUDGET.get()
        if budget and budget.attempts >= MAX_READ_ATTEMPTS:
            raise budget.error.original_error
        if budget:
            budget.attempts += 1
        # Flush before observing: pending stored computations remain strict.
        with self.env.cr.savepoint():
            with observe_computes():
                return callback()

    def _qing_restricted_dependents(self, names, specification):
        """Include compute siblings, declared dependencies and currency consumers."""
        names = set(names)
        changed = True
        while changed:
            changed = False
            for name in specification:
                if name in names:
                    continue
                field = self._fields.get(name)
                if not field:
                    continue
                dependencies = self.pool.field_depends.get(field, ()) if field.compute and not field.store else ()
                depends_on_restricted = any(dep.split('.')[0] in names for dep in dependencies)
                currency_restricted = field.type == 'monetary' and field.currency_field in names
                if not depends_on_restricted and not currency_restricted:
                    continue
                siblings = self.pool.field_computed[field] if field.compute and not field.store else (field,)
                names.update(sibling.name for sibling in siblings)
                changed = True
        return names.intersection(specification)

    def _qing_failed_fields(self, error, specification, fallback=None):
        names = {
            name
            for node in error.nodes
            if node.model_name == self._name and self.id in node.record_ids
            for name in node.field_names
        }
        if not names.intersection(specification) and fallback:
            names.add(fallback)
        return self._qing_restricted_dependents(names, specification)

    def _qing_partial_record(self, specification, error):
        self.ensure_one()
        values = {'id': self.id}
        restricted = self._qing_failed_fields(error, specification)
        for name, field_spec in specification.items():
            if name in restricted or name == 'id':
                continue
            try:
                read_values = self._qing_read_attempt(
                    lambda: super(Base, self).web_read({name: field_spec}),
                )
            except RestrictedComputeError as field_error:
                restricted.update(self._qing_failed_fields(field_error, specification, name))
                restricted = self._qing_restricted_dependents(restricted, specification)
                continue
            if read_values:
                values.update(read_values[0])
        for name in restricted:
            values.pop(name, None)
        values[RESTRICTED_FIELDS_KEY] = sorted(restricted)
        return [values]

    def _qing_read_records(self, specification):
        try:
            values = self._qing_read_attempt(lambda: super(Base, self).web_read(specification))
        except RestrictedComputeError as error:
            budget = READ_BUDGET.get()
            budget.error = budget.error or error
            if len(self) == 1:
                return self._qing_partial_record(specification, error)
            # Restrict prefetch too, so one bad record cannot poison a good batch.
            middle = len(self) // 2
            left = self[:middle].with_prefetch(self[:middle].ids)
            right = self[middle:].with_prefetch(self[middle:].ids)
            return left._qing_read_records(specification) + right._qing_read_records(specification)
        for value in values:
            value.setdefault(RESTRICTED_FIELDS_KEY, [])
        return values

    def _qing_can_read_partially(self):
        return (
            not is_observing_computes()
            and not are_computes_strict()
            and bool(self.env.registry.ready)
            and self.env.user._is_internal()
            and all(isinstance(record_id, int) and record_id > 0 for record_id in self._ids)
        )

    @api.readonly
    def web_read(self, specification):
        if not self._qing_can_read_partially():
            return super().web_read(specification)
        self.check_access('read')
        for name in specification:
            if name in self._fields:
                self.check_field_access(self._fields[name], 'read')
        budget_token = None
        if READ_BUDGET.get() is None:
            budget_token = READ_BUDGET.set(ReadBudget())
        try:
            return self._qing_read_records(specification)
        finally:
            if budget_token is not None:
                READ_BUDGET.reset(budget_token)

    @api.model
    @api.readonly
    def web_search_read(self, domain, specification, offset=0, limit=None, order=None, count_limit=None):
        if not self._qing_can_read_partially():
            return super().web_search_read(domain, specification, offset, limit, order, count_limit)
        budget_token = READ_BUDGET.set(ReadBudget())
        try:
            try:
                result = self._qing_read_attempt(
                    lambda: super(Base, self).web_search_read(
                        domain, specification, offset, limit, order, count_limit,
                    ),
                )
            except RestrictedComputeError as error:
                READ_BUDGET.get().error = error
                records = self.search_fetch(domain, [], offset=offset, limit=limit, order=order)
                values = records.web_read(specification)
                return self._format_web_search_read_results(domain, values, offset, limit, count_limit)
            for value in result['records']:
                value.setdefault(RESTRICTED_FIELDS_KEY, [])
            return result
        finally:
            READ_BUDGET.reset(budget_token)

    def web_save(self, vals, specification, next_id=None):
        with strict_computes():
            return super().web_save(vals, specification, next_id=next_id)

    def web_save_multi(self, vals_list, specification):
        with strict_computes():
            return super().web_save_multi(vals_list, specification)


    def _qing_audit_enabled(self):
        if (
            not self.env.registry.ready
            or not self._auto
            or self._abstract
            or self._transient
            or self._name in EXCLUDED_AUDIT_MODELS
        ):
            return False
        parameters = self.env['ir.config_parameter'].sudo()
        return parameters.get_bool('qing_base.audit_enabled', True)

    def _qing_audit_value_fields(self, names, record_count):
        if record_count > MAX_AUDIT_VALUE_RECORDS:
            return []
        result = []
        for name in names:
            field = self._fields.get(name)
            if (
                not field
                or not field.store
                or field.compute
                or field.groups
                or field.type not in AUDIT_VALUE_TYPES
                or self._qing_audit_sensitive(name)
            ):
                continue
            result.append(name)
            if len(result) == MAX_AUDIT_VALUE_FIELDS:
                break
        return result

    def _qing_audit_sensitive(self, name):
        if self._name == 'ir.config_parameter' and name == 'value':
            return True
        return any(fragment in name.lower() for fragment in SENSITIVE_FIELD_FRAGMENTS)

    def _qing_audit_read_values(self, records, names):
        if not records or not names:
            return {}
        return {
            row['id']: row
            for row in records.sudo().read(names, load=None)
        }

    def _qing_audit_normalize(self, value):
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, tuple):
            return value[0] if value else False
        if isinstance(value, str):
            return value[:MAX_AUDIT_STRING_LENGTH]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)[:MAX_AUDIT_STRING_LENGTH]

    def _qing_audit_changes(self, names, value_names, before, after):
        changes = {}
        for name in names:
            if self._qing_audit_sensitive(name):
                changes[name] = {'redacted': True}
            elif name in value_names:
                changes[name] = {
                    'old': self._qing_audit_normalize(before.get(name)),
                    'new': self._qing_audit_normalize(after.get(name)),
                }
            else:
                changes[name] = {'changed': True}
        return json.dumps(changes, ensure_ascii=False, separators=(',', ':'))

    def _qing_audit_actor(self):
        try:
            session_uid = request.session.uid
        except (AttributeError, RuntimeError):
            return None, 'server'
        return session_uid or None, 'http'

    def _qing_audit_insert(self, operation, entries):
        if not entries:
            return
        session_uid, source = self._qing_audit_actor()
        company_id = self.env.company.id
        values = [
            (
                self._name, record_id, operation, self.env.uid, session_uid,
                source, self.env.su, company_id,
                ', '.join(names)[:1000], changes,
            )
            for record_id, names, changes in entries
        ]
        columns = (
            'model_name, record_id, operation, user_id, session_user_id, '
            'source, is_sudo, company_id, field_names, changes'
        )
        placeholder = '(NOW(), ' + ', '.join(['%s'] * 10) + ')'
        for start in range(0, len(values), AUDIT_INSERT_BATCH_SIZE):
            chunk = values[start:start + AUDIT_INSERT_BATCH_SIZE]
            placeholders = ', '.join([placeholder] * len(chunk))
            params = [item for row in chunk for item in row]
            self.env.cr.execute(
                f'INSERT INTO qing_audit_log (event_date, {columns}) '
                f'VALUES {placeholders}',
                params,
            )

    @api.model_create_multi
    def create(self, vals_list):
        result = super().create(vals_list)
        if not self._qing_audit_enabled():
            return result
        all_names = sorted({name for vals in vals_list for name in vals})
        value_names = self._qing_audit_value_fields(all_names, len(result))
        after_values = self._qing_audit_read_values(result, value_names)
        entries = []
        for record, vals in zip(result, vals_list):
            names = sorted(vals)
            after = after_values.get(record.id, {})
            changes = self._qing_audit_changes(names, value_names, {}, after)
            entries.append((record.id, names, changes))
        self._qing_audit_insert('create', entries)
        return result

    def write(self, vals):
        enabled = bool(vals) and self._qing_audit_enabled()
        names = sorted(vals) if enabled else []
        value_names = self._qing_audit_value_fields(names, len(self)) if enabled else []
        before = self._qing_audit_read_values(self, value_names) if enabled else {}
        result = super().write(vals)
        if enabled:
            after = self._qing_audit_read_values(self, value_names)
            entries = [
                (
                    record_id,
                    names,
                    self._qing_audit_changes(
                        names, value_names, before.get(record_id, {}), after.get(record_id, {}),
                    ),
                )
                for record_id in self.ids
            ]
            self._qing_audit_insert('write', entries)
        return result

    def unlink(self):
        enabled = bool(self) and self._qing_audit_enabled()
        record_ids = self.ids if enabled else []
        names = [name for name in (self._rec_name, 'state') if name in self._fields]
        value_names = self._qing_audit_value_fields(names, len(self)) if enabled else []
        before = self._qing_audit_read_values(self, value_names) if enabled else {}
        result = super().unlink()
        if enabled:
            entries = [
                (
                    record_id,
                    names,
                    self._qing_audit_changes(names, value_names, before.get(record_id, {}), {}),
                )
                for record_id in record_ids
            ]
            self._qing_audit_insert('unlink', entries)
        return result
