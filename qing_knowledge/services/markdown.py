"""Deterministic Markdown with safe editor line breaks, no arbitrary HTML."""

import hashlib
import json
import re
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from odoo import _
from odoo.exceptions import ValidationError
from odoo.tools import html_sanitize

MAX_MARKDOWN_BYTES = 1024 * 1024
PARSER_VERSION = 'qing-md/1'
_PARSER = MarkdownIt('js-default').enable('table').enable('strikethrough')
_ASSET_PATH = re.compile(r'^assets/[0-9a-f]{64}\.(?:png|jpe?g|webp|gif|pdf)$')
_LINE_BREAK = re.compile(r'<br\s*/?>', re.IGNORECASE)


def _editor_line_break(state, silent):
    """Accept only attribute-free breaks emitted by Milkdown, outside code."""
    match = _LINE_BREAK.match(state.src, state.pos)
    if not match:
        return False
    if not silent:
        state.push('hardbreak', 'br', 0)
    state.pos = match.end()
    return True


# Keep html=False: enabling raw HTML would bypass link/image policy checks.
_PARSER.inline.ruler.before('html_inline', 'qing_editor_line_break', _editor_line_break)


def normalize_markdown(source):
    if not isinstance(source, str):
        raise ValidationError(_("Markdown source must be text."))
    source = source.replace('\r\n', '\n').replace('\r', '\n')
    if len(source.encode('utf-8')) > MAX_MARKDOWN_BYTES:
        raise ValidationError(_("Markdown exceeds the 1 MiB limit."))
    if '\x00' in source:
        raise ValidationError(_("Markdown contains an invalid character."))
    return source


def asset_paths(source):
    paths = set()
    for token in _PARSER.parse(source):
        for part in token.children or ():
            if part.type not in ('image', 'link_open'):
                continue
            path = part.attrGet('src' if part.type == 'image' else 'href')
            if path and _ASSET_PATH.fullmatch(path):
                paths.add(path)
    return paths


def image_sources(source):
    return [part.attrGet('src') or ''
            for token in _PARSER.parse(normalize_markdown(source))
            for part in token.children or () if part.type == 'image']


def is_asset_path(path):
    return bool(_ASSET_PATH.fullmatch(path))


def render_markdown(source, asset_url=None):
    tokens = _PARSER.parse(normalize_markdown(source))
    for token in tokens:
        for part in token.children or ():
            if part.type not in ('image', 'link_open'):
                continue
            key = 'src' if part.type == 'image' else 'href'
            value = part.attrGet(key) or ''
            if _ASSET_PATH.fullmatch(value) and asset_url:
                part.attrSet(key, asset_url(value))
                continue
            url = urlsplit(value)
            if url.scheme not in ('', 'http', 'https', 'mailto') or value.startswith('//'):
                part.attrSet(key, '#')
            elif part.type == 'image':
                # External images can track readers; only bound assets render.
                part.attrSet(key, '#')
    return html_sanitize(_PARSER.renderer.render(tokens, _PARSER.options, {}), sanitize_attributes=True)


def content_hash(title, summary, source, asset_checksums=(), tags=()):
    payload = json.dumps({
        'schema': PARSER_VERSION,
        'title': title,
        'summary': summary or '',
        'markdown': normalize_markdown(source),
        'assets': sorted(asset_checksums),
        'tags': sorted(tags),
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()
