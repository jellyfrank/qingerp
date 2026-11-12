"""Date helpers exposed to workbench data formulas.

Formulas written for the Solutions module call ``dateutil.<method>()``;
keep the same names and return values so existing formulas keep working.
"""
import calendar
import datetime

from dateutil.relativedelta import relativedelta


class FormulaDateUtil:
    """Read-only date ranges available as ``dateutil`` in workbench formulas."""

    @staticmethod
    def get_first_and_last_day(year=None, month=None):
        today = datetime.datetime.now()
        year = year or today.year
        month = month or today.month
        last = calendar.monthrange(year, month)[1]
        return datetime.date(year, month, 1), datetime.date(year, month, last)

    @classmethod
    def get_current_month_range(cls):
        return cls.get_first_and_last_day()

    @staticmethod
    def get_season_range():
        today = datetime.datetime.now()
        quarter = (today.month - 1) // 3
        first_month = quarter * 3 + 1
        last_month = first_month + 2
        last_day = calendar.monthrange(today.year, last_month)[1]
        return (
            "%d-%02d-01" % (today.year, first_month),
            "%d-%02d-%02d" % (today.year, last_month, last_day),
        )

    @staticmethod
    def get_week_range(date=None):
        date = date or datetime.date.today()
        index = date.isoweekday()
        return date + relativedelta(days=1 - index), date + relativedelta(days=7 - index)
