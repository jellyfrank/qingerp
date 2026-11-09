"""Bounded local document recognition. No business models or network clients."""

import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from PIL import Image, ImageOps

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_TEXT = 100_000
MAX_PAGES = 10
COMMAND_TIMEOUT = 25
DOCUMENT_TIMEOUT = 90
MIN_TEXT_CHARACTERS = 10


class RecognitionError(Exception):
    """A document or engine error; file content must never be included."""

    def __init__(self, code, message=None):
        self.code = code
        super().__init__(message or code)


def _run(args, deadline, output=None):
    executable = shutil.which(args[0])
    if not executable:
        raise RecognitionError('missing_engine')
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RecognitionError('timeout')
    try:
        subprocess.run(
            [executable, *args[1:]], check=True,
            timeout=min(COMMAND_TIMEOUT, remaining),
            stdout=output if output is not None else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, 'OMP_THREAD_LIMIT': '1', 'LC_ALL': 'C'},
        )
    except subprocess.TimeoutExpired as error:
        raise RecognitionError('timeout') from error
    except (subprocess.SubprocessError, OSError) as error:
        raise RecognitionError('engine_failed') from error


def _text(path):
    try:
        if path.stat().st_size > MAX_TEXT:
            raise RecognitionError('text_limit')
        return path.read_text(encoding='utf-8', errors='replace').strip()
    except OSError as error:
        raise RecognitionError('engine_failed') from error


def _usable_pdf_text(text):
    # Script-independent: English, Chinese and other alphanumeric text are valid.
    return '\ufffd' not in text and sum(character.isalnum() for character in text) >= MIN_TEXT_CHARACTERS


def _image_text(data, root, language, deadline):
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ('PNG', 'JPEG') or getattr(image, 'n_frames', 1) != 1:
                raise RecognitionError('invalid_image')
            if image.width * image.height > MAX_PIXELS:
                raise RecognitionError('pixel_limit')
            image = ImageOps.exif_transpose(image).convert('RGB')
            image.thumbnail((3500, 3500))
            image.save(root / 'input.png')
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise RecognitionError('invalid_image') from error
    _run(['tesseract', str(root / 'input.png'), str(root / 'ocr'), '-l', language, '--psm', '3'], deadline)
    text = _text(root / 'ocr.txt')
    return text


def extract_text(data, *, language='chi_sim+eng', max_pages=1):
    """Return text, numbered pages/sources and warnings; limits apply to the whole file.

    Single-page input remains the default. Trusted business callers can explicitly
    allow up to ten pages. OCR language never controls PDF text-layer eligibility.
    """
    if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
        raise RecognitionError('file_limit')
    if not isinstance(language, str) or len(language) > 100 or not re.fullmatch(r'[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*', language):
        raise RecognitionError('invalid_language')
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES:
        raise RecognitionError('invalid_page_limit')
    deadline = time.monotonic() + DOCUMENT_TIMEOUT
    pages = []
    warnings = []
    with tempfile.TemporaryDirectory(prefix='qing-ocr-') as directory:
        root = Path(directory)
        if not data.startswith(b'%PDF-'):
            text = _image_text(data, root, language, deadline)
            pages.append({'number': 1, 'text': text, 'source': 'ocr'})
        else:
            source = root / 'source.pdf'
            source.write_bytes(data)
            with (root / 'info.txt').open('wb') as output:
                _run(['pdfinfo', str(source)], deadline, output)
            info = _text(root / 'info.txt')
            count = re.search(r'^Pages:\s+(\d+)\s*$', info, re.M)
            if re.search(r'^Encrypted:\s+yes', info, re.M):
                raise RecognitionError('encrypted_pdf')
            if not count or not 1 <= int(count[1]) <= max_pages:
                raise RecognitionError('page_limit')
            for number in range(1, int(count[1]) + 1):
                page = str(number)
                _run(['pdftotext', '-f', page, '-l', page, '-layout', str(source), str(root / 'text.txt')], deadline)
                text = _text(root / 'text.txt')
                origin = 'pdf_text'
                if not _usable_pdf_text(text):
                    _run(['pdftoppm', '-f', page, '-l', page, '-singlefile', '-scale-to', '3500', '-png', str(source), str(root / 'page')], deadline)
                    try:
                        image_data = (root / 'page.png').read_bytes()
                    except OSError as error:
                        raise RecognitionError('engine_failed') from error
                    text = _image_text(image_data, root, language, deadline)
                    origin = 'ocr'
                    warnings.append({'code': 'pdf_text_fallback', 'page': number})
                pages.append({'number': number, 'text': text, 'source': origin})
                if not text:
                    warnings.append({'code': 'empty_page', 'page': number})
                if sum(len(item['text'].encode('utf-8')) for item in pages) > MAX_TEXT:
                    raise RecognitionError('text_limit')
        text = '\n\n'.join(page['text'] for page in pages)
        if not text.strip():
            raise RecognitionError('empty_text')
        if len(text.encode('utf-8')) > MAX_TEXT:
            raise RecognitionError('text_limit')
        return {'text': text, 'engine': 'local', 'pages': pages, 'warnings': warnings}
