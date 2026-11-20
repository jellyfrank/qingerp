from odoo import api, fields, models
from odoo.tools import BinaryBytes, BinaryValue, file_open
from odoo.tools.image import image_process

QING_FAVICON_SOURCE = 'qing_base/static/img/qing_logo.png'
ODOO_STOCK_FAVICON = 'web/static/img/favicon.ico'


def favicon_bytes(data):
    """Process an image the way website._handle_favicon stores an upload."""
    return image_process(data, size=(256, 256), crop='center', output_format='ICO')


def binary_content(value):
    if isinstance(value, BinaryValue):
        return value.content
    return bytes(value or b'')


class Website(models.Model):
    _inherit = 'website'

    # The stock default reads web/static/img/favicon.ico; new websites start with the Q mark.
    favicon = fields.Binary(default=lambda self: self._qing_default_favicon())

    @api.model
    def _qing_favicon_source(self):
        with file_open(QING_FAVICON_SOURCE, 'rb') as image:
            return image.read()

    @api.model
    def _qing_default_favicon(self):
        return BinaryBytes(favicon_bytes(self._qing_favicon_source()), filename='favicon.ico')

    @api.model
    def _qing_stock_favicons(self):
        """Odoo's stock favicon as a field default stores it and as an upload stores it."""
        with file_open(ODOO_STOCK_FAVICON, 'rb') as image:
            stock = image.read()
        return {stock, favicon_bytes(stock)}

    @api.model
    def _qing_replace_stock_favicons(self):
        """Give the Q mark to websites that still show Odoo's stock favicon."""
        stock = self._qing_stock_favicons()
        websites = self.sudo().search([]).filtered(
            lambda website: not website.favicon or binary_content(website.favicon) in stock)
        if websites:
            websites.write({'favicon': BinaryBytes(self._qing_favicon_source(), filename='favicon.png')})
        return websites
