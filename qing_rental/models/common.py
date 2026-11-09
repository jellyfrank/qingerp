import math

from odoo.exceptions import AccessError, ValidationError


INTERNAL_TOKEN = object()
PERIODS = [('day', 'Day'), ('week', 'Week'), ('month', 'Month (30 days)')]
PERIOD_SECONDS = {'day': 86400, 'week': 604800, 'month': 2592000}


def internal(records):
    return records.with_context(_qing_rental_internal=INTERNAL_TOKEN)


def is_internal(env):
    return env.context.get('_qing_rental_internal') is INTERNAL_TOKEN


def check_operator(env):
    if not env.user.has_group('qing_rental.group_rental_user'):
        raise AccessError(env._('Rental permission is required.'))


def finite_nonnegative(env, value):
    if not math.isfinite(value) or value < 0:
        raise ValidationError(env._('Enter a finite, non-negative value.'))


def periods(start, end, period):
    return math.ceil(max(0, (end - start).total_seconds()) / PERIOD_SECONDS[period])
