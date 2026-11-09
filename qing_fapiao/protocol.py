"""Provider-independent protocol v1. No HTTP, ORM or financial-ledger dependency."""
import base64
import binascii
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

SCHEMA_VERSION = '1.0'
RESULT_STATES = ('pending', 'unknown', 'issued', 'rejected')
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_RESULT_BYTES = 32 * 1024 * 1024
DECIMAL_PATTERN = re.compile(r'^\d+(?:\.\d+)?$')
DOCUMENT_TYPES = ('application/pdf', 'application/ofd', 'application/xml', 'text/xml', 'text/plain')


class ProtocolError(ValueError):
    """A malformed provider-independent message; never a definitive issuance rejection."""


def _text(value, label, required=True, limit=256):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ProtocolError(f'Invalid {label}.')
    return value


def decimal_value(value, label, precision=None):
    if not isinstance(value, str) or len(value) > 40 or not DECIMAL_PATTERN.fullmatch(value):
        raise ProtocolError(f'{label} must be a non-negative decimal string.')
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ProtocolError(f'Invalid {label}.') from error
    if not result.is_finite() or result > Decimal('100000000000000'):
        raise ProtocolError(f'Invalid {label}.')
    if precision is not None and result != result.quantize(Decimal(1).scaleb(-precision)):
        raise ProtocolError(f'{label} exceeds currency precision.')
    return result


def canonical(value):
    """Detach caller-owned containers and reject non-JSON/NaN values."""
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > MAX_RESULT_BYTES:
            raise ProtocolError('Protocol message is too large.')
        return json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise ProtocolError('Invalid JSON protocol message.') from error


def validate_payload(payload, precision=2):
    payload = canonical(payload)
    if not isinstance(payload, dict) or payload.get('schema_version') != SCHEMA_VERSION:
        raise ProtocolError('Unsupported invoice protocol version.')
    _text(payload.get('request_key'), 'request key', limit=128)
    if payload.get('color') != 'blue' or payload.get('invoice_type') not in ('normal', 'special'):
        raise ProtocolError('Protocol v1 supports normal or special blue invoices.')
    _text(payload.get('currency'), 'currency', limit=3)
    for role in ('buyer', 'seller'):
        party = payload.get(role)
        if not isinstance(party, dict):
            raise ProtocolError(f'Invalid {role}.')
        _text(party.get('name'), f'{role} name', limit=256)
        required = role == 'seller' or payload['invoice_type'] == 'special'
        _text(party.get('tax_id', ''), f'{role} tax ID', required=required, limit=64)
    lines = payload.get('lines')
    if not isinstance(lines, list) or not 1 <= len(lines) <= 10000:
        raise ProtocolError('At least one invoice line is required (maximum 10000).')
    keys = set()
    net = tax = Decimal(0)
    for line in lines:
        if not isinstance(line, dict):
            raise ProtocolError('Invalid invoice line.')
        if type(line.get('price_include')) is not bool:
            raise ProtocolError('Line price_include must be a boolean.')
        key = _text(line.get('line_key'), 'line key', limit=128)
        if key in keys:
            raise ProtocolError('Duplicate invoice line key.')
        keys.add(key)
        _text(line.get('description'), 'line description', limit=1000)
        _text(line.get('tax_code'), 'tax classification code', limit=64)
        if not decimal_value(line.get('quantity'), 'quantity', 12):
            raise ProtocolError('Invoice quantity must be positive.')
        rate = decimal_value(line.get('tax_rate'), 'tax rate', 8)
        if rate > 1:
            raise ProtocolError('Tax rate must be between 0 and 1.')
        line_net = decimal_value(line.get('amount_untaxed'), 'line amount', precision)
        line_tax = decimal_value(line.get('amount_tax'), 'line tax', precision)
        if not line_net and not line_tax:
            raise ProtocolError('An invoice line cannot be empty.')
        net += line_net
        tax += line_tax
    total_net = decimal_value(payload.get('amount_untaxed'), 'amount', precision)
    total_tax = decimal_value(payload.get('amount_tax'), 'tax', precision)
    gross = decimal_value(payload.get('amount_total'), 'total', precision)
    if net != total_net or tax != total_tax or gross != net + tax or gross <= 0:
        raise ProtocolError('Invoice line sums do not equal the approved totals.')
    return payload


def validate_result(result, snapshot, precision=2):
    result = canonical(result)
    if not isinstance(result, dict) or result.get('status') not in RESULT_STATES:
        raise ProtocolError('Invalid provider result status.')
    _text(result.get('external_id', ''), 'provider reference', required=False, limit=256)
    _text(result.get('error_code', ''), 'error code', required=False, limit=128)
    _text(result.get('message', ''), 'message', required=False, limit=2000)
    invoices = result.get('invoices', [])
    if not isinstance(invoices, list) or len(invoices) > 100:
        raise ProtocolError('Invalid issued invoice list.')
    if result['status'] != 'issued':
        if invoices:
            raise ProtocolError('Only issued results may contain invoices.')
        return result
    if not invoices:
        raise ProtocolError('An issued result must contain at least one invoice.')
    seen = set()
    net = tax = Decimal(0)
    for invoice in invoices:
        if not isinstance(invoice, dict):
            raise ProtocolError('Invalid issued invoice.')
        if invoice.get('currency') != snapshot['currency']:
            raise ProtocolError('Issued invoice currency differs from the request.')
        for role in ('buyer', 'seller'):
            party = invoice.get(role)
            if not isinstance(party, dict):
                raise ProtocolError(f'Issued invoice has no {role} identity.')
            _text(party.get('name'), f'{role} name', limit=256)
            # Seller tax identity is authoritative; provider may return the legal
            # registered name instead of the local company display name.
            keys = ('tax_id',) if role == 'seller' else ('name', 'tax_id')
            for key in keys:
                if party.get(key, '') != snapshot[role].get(key, ''):
                    raise ProtocolError(f'Issued invoice {role} differs from the request.')
        number = _text(invoice.get('number'), 'invoice number', limit=64)
        code = _text(invoice.get('code', ''), 'invoice code', required=False, limit=64)
        if (code, number) in seen:
            raise ProtocolError('Duplicate invoice identity.')
        seen.add((code, number))
        try:
            date.fromisoformat(invoice['date'])
        except (KeyError, TypeError, ValueError) as error:
            raise ProtocolError('Invalid invoice date.') from error
        net += decimal_value(invoice.get('amount_untaxed'), 'issued amount', precision)
        tax += decimal_value(invoice.get('amount_tax'), 'issued tax', precision)
        documents = invoice.get('files', [])
        if not isinstance(documents, list) or len(documents) > 10:
            raise ProtocolError('Invalid document list.')
        for document in documents:
            if not isinstance(document, dict):
                raise ProtocolError('Invalid document.')
            name = _text(document.get('name'), 'document name', limit=256)
            if '/' in name or '\\' in name or document.get('mimetype') not in DOCUMENT_TYPES:
                raise ProtocolError('Unsupported document name or type.')
            try:
                data = base64.b64decode(document['data'], validate=True)
            except (KeyError, ValueError, TypeError, binascii.Error) as error:
                raise ProtocolError('Invalid document encoding.') from error
            if not data or len(data) > MAX_DOCUMENT_BYTES:
                raise ProtocolError('Document is empty or exceeds 10 MB.')
    if net != Decimal(snapshot['amount_untaxed']) or tax != Decimal(snapshot['amount_tax']):
        raise ProtocolError('Issued amount or tax does not equal the approved totals.')
    return result


def invoice_identity(result):
    """Ignore file availability when comparing an already issued business outcome."""
    return sorted((
        item.get('code', ''), item['number'], item['date'],
        Decimal(item['amount_untaxed']), Decimal(item['amount_tax']),
    ) for item in result.get('invoices', []))


def event_summary(result):
    """Audit normalized status/identity only; do not retain credential-bearing raw responses."""
    return {
        'status': result.get('status'),
        'external_id': result.get('external_id', ''),
        'error_code': result.get('error_code', ''),
        'invoices': [
            {key: value for key, value in invoice.items() if key != 'files'}
            for invoice in result.get('invoices', [])
        ],
    }
