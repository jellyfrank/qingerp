import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from odoo.addons.qing_channel.errors import ChannelReviewRequired


KEY_ENV = 'QING_SHOPIFY_CREDENTIAL_KEY'
KEY_FILE_ENV = 'QING_SHOPIFY_CREDENTIAL_KEY_FILE'
PREFIX = 'fernet:'


def _cipher():
    keys = os.environ.get(KEY_ENV, '').strip()
    try:
        if not keys and (key_file := os.environ.get(KEY_FILE_ENV)):
            keys = Path(key_file).read_text(encoding='ascii')
        return MultiFernet([Fernet(key.strip().encode()) for key in keys.split(',') if key.strip()])
    except (OSError, TypeError, ValueError):
        raise ChannelReviewRequired('shopify_credential_key_unavailable') from None


def storage_ready():
    try:
        _cipher()
    except ChannelReviewRequired:
        return False
    return True


def seal(value):
    if not value:
        return False
    return PREFIX + _cipher().encrypt(value.encode()).decode()


def open_secret(value):
    if not value:
        return False
    if not value.startswith(PREFIX):
        raise ChannelReviewRequired('shopify_credential_requires_reentry')
    try:
        return _cipher().decrypt(value[len(PREFIX):].encode()).decode()
    except (InvalidToken, UnicodeDecodeError):
        raise ChannelReviewRequired('shopify_credential_key_mismatch') from None
