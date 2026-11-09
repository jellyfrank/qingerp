from odoo import api, models

from ..services.local_ocr import RecognitionError, extract_text


class OcrService(models.AbstractModel):
    _name = 'qing.ocr.service'
    _description = 'Document Text Recognition'

    @api.model
    def _extract_text(self, data, *, language='chi_sim+eng', max_pages=1, engine='local'):
        """Private service seam; callers must authorize document access beforehand.

        No attachment reads, sudo, persistence, or automatic remote fallback.
        Engine addons may extend the private engine map explicitly.
        """
        engines = self._get_ocr_engines()
        if engine not in engines:
            raise RecognitionError('unsupported_engine', self.env._('The requested OCR engine is not available.'))
        try:
            return engines[engine](data, language=language, max_pages=max_pages)
        except RecognitionError as error:
            raise RecognitionError(error.code, self._recognition_error_message(error.code)) from error

    @api.model
    def _get_ocr_engines(self):
        return {'local': self._extract_local_text}

    @api.model
    def _extract_local_text(self, data, *, language, max_pages):
        return extract_text(data, language=language, max_pages=max_pages)

    @api.model
    def _recognition_error_message(self, code):
        messages = {
            'file_limit': self.env._('Upload a PNG, JPEG or PDF file of at most 10 MB.'),
            'invalid_image': self.env._('The image is invalid. Use a single PNG or JPEG image.'),
            'pixel_limit': self.env._('The image exceeds 20 million pixels. Resize it before uploading.'),
            'text_limit': self.env._('The document contains too much text. Split it into smaller files.'),
            'missing_engine': self.env._('A local recognition dependency is missing. Install Tesseract, its required language packs and Poppler.'),
            'timeout': self.env._('Local recognition timed out. Use a smaller or clearer document.'),
            'engine_failed': self.env._('Local recognition failed. Check the file and installed language packs.'),
            'invalid_language': self.env._('The OCR language setting is invalid.'),
            'invalid_page_limit': self.env._('The page limit must be an integer between 1 and 10.'),
            'page_limit': self.env._('The PDF exceeds the allowed page count or its page count cannot be read.'),
            'encrypted_pdf': self.env._('Encrypted PDF files are not supported.'),
            'empty_text': self.env._('No text was recognized. Use a clearer document or enter the data manually.'),
        }
        return messages.get(code, self.env._('Document recognition failed.'))
