"""Bounded, inert workbook serialization shared by import and persistence."""

import base64
import io
import json
import math
import re
import zipfile
from pathlib import PurePosixPath

from odoo.tools.translate import LazyTranslate

_ = LazyTranslate(__name__)
from odoo.exceptions import ValidationError

MAX_BYTES = 20 * 1024 * 1024
MAX_UNPACKED_BYTES = 40 * 1024 * 1024
MAX_CELLS = 100_000
MAX_SHEETS = 50
MAX_ROWS = 100_000
MAX_COLUMNS = 1000
INTERNAL_WRITE = object()
CELL_ADDRESS = re.compile(r'^([A-Z]{1,4})([1-9][0-9]{0,5})$')

def cell_position(value):
    match = CELL_ADDRESS.fullmatch(value)
    if not match:
        raise ValidationError(_('Invalid cell address: %s', value))
    column = 0
    for char in match[1]:
        column = column * 26 + ord(char) - 64
    row = int(match[2])
    if column > MAX_COLUMNS or row > MAX_ROWS:
        raise ValidationError(_('Cell coordinates exceed the supported limit.'))
    return column, row


def encode(data):
    try:
        value = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValidationError(_('The workbook is not valid JSON.')) from exc
    if len(value.encode()) > MAX_BYTES:
        raise ValidationError(_('The workbook exceeds the 20 MB limit.'))
    return value


def decode(data):
    if isinstance(data, str):
        if len(data.encode()) > MAX_BYTES:
            raise ValidationError(_('The workbook exceeds the 20 MB limit.'))
        try:
            data = json.loads(data)
        except (ValueError, RecursionError) as exc:
            raise ValidationError(_('The workbook is not valid JSON.')) from exc
    if not isinstance(data, dict):
        raise ValidationError(_('A workbook must be a JSON object.'))
    encode(data)
    return data


def validate(data):
    data = decode(data)
    sheets = data.get('sheets')
    if not isinstance(sheets, list) or not 1 <= len(sheets) <= MAX_SHEETS:
        raise ValidationError(_('A workbook must contain between 1 and 50 worksheets.'))
    ids = set()
    cell_count = 0
    for sheet in sheets:
        if not isinstance(sheet, dict) or not isinstance(sheet.get('id'), str) or sheet['id'] in ids:
            raise ValidationError(_('Each worksheet needs a unique identifier.'))
        ids.add(sheet['id'])
        if not isinstance(sheet.get('name'), str) or not sheet['name'].strip():
            raise ValidationError(_('Every worksheet needs a name.'))
        for key, maximum in [('rowNumber', MAX_ROWS), ('colNumber', MAX_COLUMNS)]:
            value = sheet.get(key, 100 if key == 'rowNumber' else 26)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValidationError(_('Worksheet dimensions exceed the supported limit.'))
        cells = sheet.get('cells', {})
        if not isinstance(cells, dict):
            raise ValidationError(_('Worksheet cells must be an object.'))
        for address, cell in cells.items():
            parts = address.split(':')
            if not 1 <= len(parts) <= 2:
                raise ValidationError(_('Invalid cell address: %s', address))
            start = cell_position(parts[0])
            end = cell_position(parts[-1])
            cell_count += (abs(end[0] - start[0]) + 1) * (abs(end[1] - start[1]) + 1)
            if cell_count > MAX_CELLS:
                raise ValidationError(_('A workbook may contain at most 100,000 populated cells.'))
            # Native exports use string cells; old imports can use content objects.
            if not isinstance(cell, (str, dict)):
                raise ValidationError(_('Invalid cell value: %s', address))
        figures = sheet.get('figures', [])
        if not isinstance(figures, list) or any(not isinstance(figure, dict) for figure in figures):
            raise ValidationError(_('Invalid worksheet figures.'))
        for figure in figures:
            if not isinstance(figure.get('data', {}), dict):
                raise ValidationError(_('Invalid worksheet figure data.'))
            if figure.get('tag') == 'image':
                path = figure.get('data', {}).get('path', '')
                if not path.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,')):
                    raise ValidationError(_('Embed PNG or JPEG images; remote image URLs are not supported.'))
    for key in ('lists', 'pivots', 'charts'):
        definitions = data.get(key, {})
        if not isinstance(definitions, dict) or len(definitions) > 100 or any(not isinstance(value, dict) for value in definitions.values()):
            raise ValidationError(_('Invalid data source definitions.'))
    # Limit depth and reject JS prototype keys before loading the native engine.
    stack = [(data, 0)]
    while stack:
        value, depth = stack.pop()
        if depth > 40:
            raise ValidationError(_('Workbook nesting is too deep.'))
        if isinstance(value, dict):
            if {'__proto__', 'constructor', 'prototype'} & value.keys():
                raise ValidationError(_('The workbook contains an unsupported object key.'))
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValidationError(_('Numbers must be finite.'))
    return data


def unpack_xlsx(content):
    if len(content) > MAX_BYTES:
        raise ValidationError(_('The file exceeds the 20 MB limit.'))
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValidationError(_('This is not a valid XLSX file.')) from exc
    with archive:
        entries = archive.infolist()
        if len(entries) > 5000 or sum(item.file_size for item in entries) > MAX_UNPACKED_BYTES:
            raise ValidationError(_('The uncompressed workbook is too large.'))
        if '[Content_Types].xml' not in archive.namelist() or 'xl/workbook.xml' not in archive.namelist():
            raise ValidationError(_('This archive is not an XLSX workbook.'))
        data = {}
        for item in entries:
            path = PurePosixPath(item.filename)
            if path.is_absolute() or '..' in path.parts or item.flag_bits & 1:
                raise ValidationError(_('Encrypted files and unsafe archive paths are not supported.'))
            if item.is_dir():
                continue
            if item.filename in data:
                raise ValidationError(_('Duplicate archive entries are not supported.'))
            if 'vbaproject' in item.filename.lower() or 'externallinks/' in item.filename.lower():
                raise ValidationError(_('Remove macros and external workbook links before importing.'))
            raw = archive.read(item)
            if item.filename.endswith(('.xml', '.rels')):
                if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                    raise ValidationError(_('XML entities are not supported.'))
                try:
                    data[item.filename] = raw.decode('utf-8-sig')
                except UnicodeDecodeError as exc:
                    raise ValidationError(_('Only UTF-8 XLSX XML is supported.')) from exc
            elif item.filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                mime = 'png' if item.filename.lower().endswith('.png') else 'jpeg'
                data[item.filename] = f'data:image/{mime};base64,' + base64.b64encode(raw).decode()
        encode(data)
        return data


def pack_xlsx(files):
    if not isinstance(files, list) or len(files) > 5000:
        raise ValidationError(_('Invalid export package.'))
    output = io.BytesIO()
    size = 0
    seen = set()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for item in files:
            if not isinstance(item, dict):
                raise ValidationError(_('Invalid export package.'))
            path = item.get('path', '')
            parts = PurePosixPath(path)
            if not path or parts.is_absolute() or '..' in parts.parts or path in seen:
                raise ValidationError(_('Invalid export path.'))
            seen.add(path)
            if 'imageSrc' in item:
                image = item['imageSrc']
                if not image.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,')):
                    raise ValidationError(_('Only embedded PNG or JPEG images can be exported.'))
                try:
                    content = base64.b64decode(image.split(',', 1)[1], validate=True)
                except ValueError as exc:
                    raise ValidationError(_('Invalid embedded image.')) from exc
            else:
                content = item.get('content', '').encode()
            size += len(content)
            if size > MAX_UNPACKED_BYTES:
                raise ValidationError(_('The export is too large.'))
            archive.writestr(path, content)
    return output.getvalue()
