"""Bounded, path-safe Markdown package conversion without filesystem extraction."""

import io
import json
import posixpath
import stat
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from odoo import _
from odoo.exceptions import ValidationError

from .markdown import MAX_MARKDOWN_BYTES, image_sources, is_asset_path, normalize_markdown

SCHEMA = 'qing-knowledge-package/1'
MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_FILE_COUNT = 2000
MAX_ARTICLE_COUNT = 500
MAX_INTERACTIVE_ARTICLES = 30
MAX_INTERACTIVE_EXPANDED_BYTES = 25 * 1024 * 1024


def _safe_name(name):
    if not name or '\\' in name or '\x00' in name or name.startswith('/') or ':' in name:
        raise ValidationError(_("Package contains an unsafe path."))
    normalized = posixpath.normpath(name)
    if normalized in ('.', '..') or normalized.startswith('../') or normalized != name.rstrip('/'):
        raise ValidationError(_("Package contains an unsafe or duplicate path."))
    if len(PurePosixPath(name).parts) > 20:
        raise ValidationError(_("Package directory nesting exceeds 20 levels."))
    return normalized


def parse_package(raw, max_articles=MAX_ARTICLE_COUNT, max_expanded_bytes=MAX_EXPANDED_BYTES):
    """Return manifest articles and asset bytes after a complete safety preflight."""
    if not raw or len(raw) > MAX_ZIP_BYTES:
        raise ValidationError(_("ZIP size must be between 1 byte and 50 MiB."))
    try:
        package = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, OSError) as error:
        raise ValidationError(_("Invalid ZIP archive.")) from error
    with package:
        infos = package.infolist()
        if len(infos) > MAX_FILE_COUNT:
            raise ValidationError(_("ZIP contains too many files."))
        names = set()
        normalized_names = set()
        expanded = 0
        for info in infos:
            name = _safe_name(info.filename)
            if name in names:
                raise ValidationError(_("ZIP contains duplicate paths."))
            names.add(name)
            normalized_key = unicodedata.normalize('NFC', name).casefold()
            if normalized_key in normalized_names:
                raise ValidationError(_("ZIP contains case or Unicode equivalent paths."))
            normalized_names.add(normalized_key)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
                raise ValidationError(_("ZIP contains a link or device."))
            if info.flag_bits & 1:
                raise ValidationError(_("Encrypted ZIP files are not supported."))
            expanded += info.file_size
            if expanded > max_expanded_bytes or (info.compress_size and info.file_size > info.compress_size * 100):
                raise ValidationError(_("ZIP expanded size or compression ratio exceeds the limit."))
        if 'manifest.json' in names:
            try:
                manifest = json.loads(package.read('manifest.json', pwd=None))
            except (ValueError, RuntimeError, zipfile.BadZipFile) as error:
                raise ValidationError(_("Invalid package manifest.")) from error
            if manifest.get('schema') != SCHEMA or not isinstance(manifest.get('articles'), list):
                raise ValidationError(_("Unsupported package schema."))
            specs = manifest['articles']
        else:
            specs = [{'path': name, 'title': PurePosixPath(name).stem}
                     for name in sorted(names) if name.lower().endswith('.md')]
        if not specs or len(specs) > max_articles:
            if max_articles == MAX_ARTICLE_COUNT:
                raise ValidationError(_("Package must contain 1–500 Markdown articles."))
            raise ValidationError(_("This import exceeds the current interactive article limit."))
        articles = []
        article_paths = set()
        source_uuids = set()
        for spec in specs:
            if not isinstance(spec, dict):
                raise ValidationError(_("Invalid article entry in package manifest."))
            path = _safe_name(spec.get('path'))
            if path not in names or not path.lower().endswith('.md'):
                raise ValidationError(_("Package article path is missing or invalid."))
            if path in article_paths:
                raise ValidationError(_("Package contains duplicate article paths."))
            article_paths.add(path)
            info = package.getinfo(path)
            if info.file_size > MAX_MARKDOWN_BYTES:
                raise ValidationError(_("Markdown article exceeds 1 MiB."))
            try:
                source = normalize_markdown(package.read(path).decode('utf-8'))
            except (UnicodeError, RuntimeError, zipfile.BadZipFile) as error:
                raise ValidationError(_("Markdown article must be valid UTF-8.")) from error
            for image in image_sources(source):
                if not is_asset_path(image) and urlsplit(image).scheme not in ('http', 'https') and not image.startswith('//'):
                    raise ValidationError(_("Relative images must use the package assets/<checksum>.<ext> convention."))
            title = spec.get('title') or PurePosixPath(path).stem
            if not isinstance(title, str) or not title or len(title) > 255:
                raise ValidationError(_("Invalid article title in package."))
            tags = spec.get('tags') or []
            if not isinstance(tags, list) or len(tags) > 20 or any(not isinstance(tag, str) for tag in tags):
                raise ValidationError(_("Invalid article tags in package."))
            try:
                source_uuid = str(UUID(spec['article_uuid'])) if spec.get('article_uuid') else False
                parent_uuid = str(UUID(spec['parent_uuid'])) if spec.get('parent_uuid') else False
            except (ValueError, TypeError, AttributeError) as error:
                raise ValidationError(_("Invalid article identity in package.")) from error
            if source_uuid:
                if source_uuid in source_uuids:
                    raise ValidationError(_("Package contains duplicate article identities."))
                source_uuids.add(source_uuid)
            articles.append({'path': path, 'title': title, 'markdown': source, 'tags': tags,
                             'source_uuid': source_uuid, 'parent_uuid': parent_uuid})
        assets = {}
        for name in sorted(names):
            if name.startswith('assets/') and not name.endswith('/'):
                info = package.getinfo(name)
                if info.file_size > 20 * 1024 * 1024:
                    raise ValidationError(_("An asset exceeds 20 MiB."))
                try:
                    assets[name] = package.read(name)
                except (RuntimeError, zipfile.BadZipFile, OSError) as error:
                    raise ValidationError(_("Invalid package asset.")) from error
        return articles, assets


def build_package(articles):
    """Build a portable ZIP from authorized (article, revision) pairs."""
    manifest = {
        'schema': SCHEMA, 'export_uuid': str(uuid4()),
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'articles': [], 'assets': [],
    }
    buffer = io.BytesIO()
    written_assets = set()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for article, revision in articles:
            path = 'articles/%s.md' % article.uuid
            package.writestr(path, revision.markdown.encode('utf-8'))
            manifest['articles'].append({
                'path': path, 'title': revision.title, 'article_uuid': article.uuid,
                'revision_uuid': revision.uuid, 'content_hash': revision.content_hash,
                'parent_uuid': article.parent_id.uuid or False,
                'tags': revision.tag_ids.mapped('name'),
            })
            for asset in revision.asset_ids:
                if asset.logical_path in written_assets:
                    continue
                raw = bytes(asset.attachment_id.raw)
                written_assets.add(asset.logical_path)
                package.writestr(asset.logical_path, raw)
                # Relative paths in articles/*.md remain usable after unzipping.
                package.writestr('articles/' + asset.logical_path, raw)
                manifest['assets'].append({'path': asset.logical_path, 'checksum': asset.checksum,
                                           'mime': asset.mime, 'size': asset.size})
        package.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    result = buffer.getvalue()
    if len(result) > MAX_ZIP_BYTES:
        raise ValidationError(_("Export exceeds the 50 MiB ZIP limit."))
    return result
