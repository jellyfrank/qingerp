import hashlib
import json
import math
import uuid

from odoo.exceptions import AccessError, UserError


INTERNAL = object()
SESSION_STATES = [
    ('scanning', 'Scanning'), ('review', 'Awaiting review'),
    ('applied', 'Awaiting validation'), ('done', 'Done'), ('cancelled', 'Cancelled'),
]
SESSION_KINDS = [('picking', 'Transfer'), ('inventory', 'Inventory count')]
EVENT_STATES = [('accepted', 'Accepted'), ('rejected', 'Rejected')]
ACTIVE_STATES = ('scanning', 'review', 'applied')
MAX_CODE_LENGTH = 512
MAX_LINES = 2000


def internal(records):
    return records.with_context(_qing_scan_internal=INTERNAL)


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def check_operator(env):
    if not env.user.has_group('qing_stock_barcode.group_scan_user'):
        raise AccessError(env._('Warehouse scanning permission is required.'))


def is_manager(env):
    return env.user.has_group('qing_stock_barcode.group_scan_manager')


def request_key(env, value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise UserError(env._('A valid request UUID is required.')) from None


def quantity(env, value, uom, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise UserError(env._('Enter a finite numeric quantity.'))
    if value < 0 or (not allow_zero and value == 0) or value > 1e9:
        raise UserError(env._('The quantity is outside the permitted range.'))
    rounded = uom.round(value)
    if abs(value - rounded) > 1e-8:
        raise UserError(env._('The quantity does not match the unit rounding.'))
    return rounded


def lock_records(records, serialize=False):
    if not records:
        return
    records.check_access('read')
    records.flush_recordset()
    # Table names are model metadata, never user input.
    from odoo.tools import SQL
    if serialize:
        # A row lock alone does not refresh an existing REPEATABLE READ snapshot.
        # Touch the MVCC row version without changing business fields so Odoo's
        # transaction retry sees concurrent session/event/serial creation.
        for record_id in sorted(records.ids):
            records.env.cr.execute(SQL('UPDATE %s SET id = id WHERE id = %s',
                                      SQL.identifier(records._table), record_id))
    else:
        records.env.cr.execute(SQL(
            'SELECT id FROM %s WHERE id IN %s ORDER BY id FOR UPDATE',
            SQL.identifier(records._table), tuple(records.ids),
        ))
    records.invalidate_recordset()


def scoped_record(env, model, record_id, operation='read'):
    if type(record_id) is not int or record_id <= 0:
        raise UserError(env._('Select a valid record.'))
    record = env[model].browse(record_id).exists()
    if not record:
        raise UserError(env._('The selected record no longer exists.'))
    record.check_access(operation)
    if 'company_id' in record._fields and record.company_id and record.company_id != env.company:
        raise AccessError(env._('Select a record in the current company.'))
    return record
