"""Phone number helpers for domestic SMS providers."""
import phonenumbers


def plain_number(number, region='CN'):
    """Return the national digits of ``number`` (for example ``13800138000``).

    Unparseable input keeps only its digits, so the provider can reject it.
    """
    if not number:
        return ''
    try:
        parsed = phonenumbers.parse(number, region)
    except phonenumbers.NumberParseException:
        return phonenumbers.normalize_digits_only(number)
    national = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL)
    return phonenumbers.normalize_digits_only(national)
