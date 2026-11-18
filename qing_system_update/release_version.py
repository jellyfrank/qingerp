"""Order historical and date-based QingERP releases."""
import datetime
import re


DATE_VERSION = re.compile(r"20\.0\.([0-9]{8})([0-9]{2})?\Z")
LEGACY_VERSION = re.compile(r"20\.0\.([1-9][0-9]{0,6})\Z")


def version_key(version):
    """Return (calendar date, daily serial), with legacy counters sorting first."""
    if not isinstance(version, str):
        return None
    match = DATE_VERSION.fullmatch(version)
    if match:
        date, serial = match.groups()
        if serial == "00":
            return None
        try:
            datetime.date(int(date[:4]), int(date[4:6]), int(date[6:]))
        except ValueError:
            return None
        return int(date), int(serial or 0)
    match = LEGACY_VERSION.fullmatch(version)
    return (0, int(match[1])) if match else None


def is_release_version(version):
    """New releases always include a non-zero two-digit serial."""
    key = version_key(version)
    return bool(key and key[0] and len(version) == 15)
