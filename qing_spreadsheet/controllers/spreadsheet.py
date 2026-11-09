import json

from werkzeug.exceptions import BadRequest

from odoo import http, _
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request
from odoo.http.stream import content_disposition

from ..services import workbook


class QingSpreadsheetController(http.Controller):
    @http.route('/qing_spreadsheet/import', type='http', auth='user', methods=['POST'])
    def import_file(self, file=None, **kwargs):
        if not request.env.user.has_group('base.group_user'):
            raise AccessError(_('You cannot import workbooks.'))
        try:
            if file is None or not hasattr(file, 'read'):
                raise ValidationError(_('Choose an XLSX or JSON workbook.'))
            content = file.read(workbook.MAX_BYTES + 1)
            if len(content) > workbook.MAX_BYTES:
                raise ValidationError(_('The file exceeds the 20 MB limit.'))
            name = (file.filename or '').lower()
            if name.endswith('.xlsx'):
                data = workbook.unpack_xlsx(content)
                warnings = [_('Excel macros and external workbook links are not supported. Review imported formulas and charts before saving.')]
            elif name.endswith('.json'):
                data = workbook.validate(content.decode('utf-8-sig'))
                warnings = []
            else:
                raise ValidationError(_('Choose an XLSX or JSON workbook.'))
        except (ValidationError, UnicodeDecodeError) as exc:
            return request.make_json_response({'error': str(exc)}, status=400)
        # The native engine converts XLSX in the browser; validated persistence is a separate RPC.
        return request.make_json_response({'data': data, 'warnings': warnings, 'name': file.filename})

    @http.route('/qing_spreadsheet/<int:workbook_id>/export', type='http', auth='user', methods=['POST'])
    def export_file(self, workbook_id, files=None, format='xlsx', **kwargs):
        book = request.env['qing.spreadsheet'].browse(workbook_id).exists()
        book.check_access('read')
        if not book or not request.env.user.has_group('base.group_allow_export'):
            raise AccessError(_('You do not have permission to export this workbook.'))
        if book.binding_model:
            book._bound_record('read')
        if format == 'json':
            content = book.spreadsheet_data.encode()
            mime, extension = 'application/json', 'osheet.json'
        elif format == 'xlsx' and files:
            raw = files.read(workbook.MAX_BYTES + 1) if hasattr(files, 'read') else files.encode()
            if len(raw) > workbook.MAX_BYTES:
                raise BadRequest('The export package is too large.')
            content = workbook.pack_xlsx(json.loads(raw))
            mime, extension = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'xlsx'
        else:
            raise BadRequest('Unsupported export format.')
        return request.make_response(content, headers=[('Content-Type', mime),
            ('Content-Disposition', content_disposition(f'{book.name}.{extension}')),
            ('Content-Length', str(len(content))), ('X-Content-Type-Options', 'nosniff')])
