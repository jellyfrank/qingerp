import io
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

from PIL import Image, ImageFont, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from odoo.tests import TransactionCase, tagged

from ..services import local_ocr
from ..services.local_ocr import RecognitionError


def image_bytes(text=None, format='PNG'):
    image = Image.new('RGB', (1100, 180), 'white')
    if text:
        font = ImageFont.truetype('DejaVuSans.ttf', 42)
        ImageDraw.Draw(image).text((30, 55), text, fill='black', font=font)
    data = io.BytesIO()
    image.save(data, format=format)
    return data.getvalue()


def pdf_bytes(text='Purchase order number 12345', pages=1, scanned=False):
    data = io.BytesIO()
    document = canvas.Canvas(data)
    for number in range(pages):
        if scanned:
            document.drawImage(ImageReader(io.BytesIO(image_bytes(text))), 30, 500, 500, 82)
        else:
            document.drawString(40, 750, f'{text} Page {number + 1}')
        document.showPage()
    document.save()
    return data.getvalue()


@tagged('post_install', '-at_install')
class TestOcr(TransactionCase):
    def test_invalid_files_and_limits(self):
        for data in (b'', b'x' * (local_ocr.MAX_BYTES + 1), b'invalid', 'not bytes'):
            with self.assertRaises(RecognitionError):
                local_ocr.extract_text(data)
        with self.assertRaisesRegex(RecognitionError, 'invalid_image'):
            local_ocr.extract_text(image_bytes(format='GIF'))
        with patch.object(local_ocr, 'MAX_PIXELS', 100):
            with self.assertRaisesRegex(RecognitionError, 'pixel_limit'):
                local_ocr.extract_text(image_bytes())

    def test_language_and_page_options(self):
        for language in ('', '../eng', 'eng --psm 1', 'eng;curl', None):
            with self.assertRaisesRegex(RecognitionError, 'invalid_language'):
                local_ocr.extract_text(b'%PDF-test', language=language)
        for pages in (0, 11, True, 1.5, '2'):
            with self.assertRaisesRegex(RecognitionError, 'invalid_page_limit'):
                local_ocr.extract_text(b'%PDF-test', max_pages=pages)

    def test_script_independent_pdf_text(self):
        for text in ('Purchase order number 12345', '通用文件识别不需要税票标签信息'):
            self.assertTrue(local_ocr._usable_pdf_text(text))
        for text in ('\ufffd' * 12, ' ! : . --- ', '123'):
            self.assertFalse(local_ocr._usable_pdf_text(text))

    def test_missing_engine_and_timeout_cleanup(self):
        paths = []
        real_directory = tempfile.TemporaryDirectory

        def directory(*args, **kwargs):
            result = real_directory(*args, **kwargs)
            paths.append(result.name)
            return result

        with patch.object(local_ocr.tempfile, 'TemporaryDirectory', side_effect=directory), \
                patch.object(local_ocr.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(RecognitionError, 'missing_engine'):
                local_ocr.extract_text(image_bytes())
        self.assertTrue(paths)
        self.assertTrue(all(not Path(path).exists() for path in paths))
        with patch.object(local_ocr.shutil, 'which', return_value='/usr/bin/tesseract'), \
                patch.object(local_ocr.subprocess, 'run', side_effect=subprocess.TimeoutExpired('tesseract', 25)):
            with self.assertRaisesRegex(RecognitionError, 'timeout'):
                local_ocr.extract_text(image_bytes())
        with self.assertRaisesRegex(RecognitionError, 'timeout'):
            local_ocr._run(['tesseract'], deadline=0)

    def test_engine_call_and_empty_text(self):
        calls = []

        def run(args, deadline, output=None):
            calls.append(args)
            Path(args[2] + '.txt').write_text('Document content 12345', encoding='utf-8')

        with patch.object(local_ocr, '_run', side_effect=run):
            result = local_ocr.extract_text(image_bytes(), language='eng')
        self.assertEqual(result['pages'], [{'number': 1, 'text': 'Document content 12345', 'source': 'ocr'}])
        self.assertEqual(result['engine'], 'local')
        self.assertEqual(result['warnings'], [])
        self.assertEqual(calls[0][-4:], ['-l', 'eng', '--psm', '3'])
        with patch.object(local_ocr, '_run'), patch.object(local_ocr, '_text', return_value=''):
            with self.assertRaisesRegex(RecognitionError, 'empty_text'):
                local_ocr.extract_text(image_bytes())

    def test_pdf_rejects_encryption_and_excess_pages(self):
        for info, code in [('Pages: 2\nEncrypted: no', 'page_limit'),
                           ('Pages: 1\nEncrypted: yes', 'encrypted_pdf'),
                           ('not a PDF', 'page_limit')]:
            with patch.object(local_ocr, '_run'), patch.object(local_ocr, '_text', return_value=info):
                with self.assertRaisesRegex(RecognitionError, code):
                    local_ocr.extract_text(b'%PDF-test')

    def test_text_limit_and_service_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'text.txt'
            path.write_bytes(b'x' * (local_ocr.MAX_TEXT + 1))
            with self.assertRaisesRegex(RecognitionError, 'text_limit'):
                local_ocr._text(path)
        service = self.env['qing.ocr.service']
        with self.assertRaises(RecognitionError) as error:
            service._extract_text(b'x', engine='missing')
        self.assertEqual(error.exception.code, 'unsupported_engine')
        expected = {'text': 'Provider text', 'engine': 'test', 'pages': [], 'warnings': []}
        with patch.object(type(service), '_get_ocr_engines', return_value={'test': lambda *args, **kwargs: expected}):
            self.assertEqual(service._extract_text(b'x', engine='test'), expected)
        with self.assertRaises(RecognitionError) as error:
            service._extract_text(b'')
        self.assertIn('10 MB', str(error.exception))

    def test_real_pdf_text_does_not_require_tesseract(self):
        # A usable English PDF must never need OCR, even with Chinese language requested.
        which = local_ocr.shutil.which
        with patch.object(local_ocr.shutil, 'which', side_effect=lambda name: None if name == 'tesseract' else which(name)):
            result = self.env['qing.ocr.service']._extract_text(pdf_bytes())
        self.assertIn('Purchase order number 12345', result['text'])
        self.assertEqual(result['pages'][0]['source'], 'pdf_text')
        self.assertEqual(result['warnings'], [])

    def test_real_images_and_scanned_pdf(self):
        for format in ('PNG', 'JPEG'):
            result = self.env['qing.ocr.service']._extract_text(image_bytes('Invoice Total 113.00', format), language='eng')
            self.assertIn('113.00', result['text'])
            self.assertEqual(result['pages'][0]['source'], 'ocr')
        result = self.env['qing.ocr.service']._extract_text(pdf_bytes('Invoice Total 113.00', scanned=True), language='eng')
        self.assertIn('113.00', result['text'])
        self.assertEqual(result['warnings'], [{'code': 'pdf_text_fallback', 'page': 1}])

    def test_real_explicit_page_limit_and_order(self):
        data = pdf_bytes(pages=2)
        with self.assertRaises(RecognitionError) as error:
            self.env['qing.ocr.service']._extract_text(data)
        self.assertEqual(error.exception.code, 'page_limit')
        result = self.env['qing.ocr.service']._extract_text(data, max_pages=2)
        self.assertEqual([page['number'] for page in result['pages']], [1, 2])
        self.assertIn('Page 1', result['pages'][0]['text'])
        self.assertIn('Page 2', result['pages'][1]['text'])

    def test_real_blank_pdf_page_is_preserved(self):
        data = io.BytesIO()
        document = canvas.Canvas(data)
        document.drawString(40, 750, 'Document with useful text 12345')
        document.showPage()
        document.showPage()
        document.save()
        result = self.env['qing.ocr.service']._extract_text(data.getvalue(), language='eng', max_pages=2)
        self.assertIn('Document with useful text', result['text'])
        self.assertEqual(result['pages'][1], {'number': 2, 'text': '', 'source': 'ocr'})
        self.assertIn({'code': 'empty_page', 'page': 2}, result['warnings'])

    def test_chinese_service_errors(self):
        self.env['res.lang']._activate_lang('zh_CN')
        with self.assertRaises(RecognitionError) as error:
            self.env['qing.ocr.service'].with_context(lang='zh_CN')._extract_text(b'')
        self.assertEqual(str(error.exception), '请上传不超过 10 MB 的 PNG、JPEG 或 PDF 文件。')
