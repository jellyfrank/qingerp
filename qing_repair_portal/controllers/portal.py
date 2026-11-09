from odoo import http
from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.http import request


PHOTO_FIELDS = {'qing_photo_before', 'qing_photo_after'}


class RepairPortal(CustomerPortal):
    @http.route('/my/repairs/<int:repair_id>', type='http', auth='public', website=True, sitemap=False)
    def repair_page(self, repair_id, access_token=None, report_type=None, download=False, **kwargs):
        try:
            repair = self._document_check_access('repair.order', repair_id, access_token)
        except (AccessError, MissingError):
            raise request.not_found()
        if request.env.user._is_public() and repair.partner_id.lang:
            request.update_context(lang=repair.partner_id.lang)
            repair = repair.with_context(lang=repair.partner_id.lang)
        if report_type in ('html', 'pdf'):
            response = self._show_report(repair, report_type, 'qing_repair_portal.action_report_repair', download=download)
        else:
            response = request.render('qing_repair_portal.repair_page', {
                'repair': repair, 'lines': repair._qing_customer_lines(),
                'price_hash': repair._qing_price_hash(), 'page_name': 'repair',
                'no_breadcrumbs': True, 'access_token': access_token,
            })
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @http.route('/my/repairs/<int:repair_id>/photo/<field_name>', type='http', auth='public', sitemap=False)
    def repair_photo(self, repair_id, field_name, access_token=None, **kwargs):
        if field_name not in PHOTO_FIELDS:
            raise request.not_found()
        try:
            repair = self._document_check_access('repair.order', repair_id, access_token)
        except (AccessError, MissingError):
            raise request.not_found()
        if not repair[field_name]:
            raise request.not_found()
        stream = request.env['ir.binary'].sudo()._get_image_stream_from(repair, field_name=field_name)
        response = stream.get_response()
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    @http.route('/my/repairs/<int:repair_id>/sign/<kind>', type='jsonrpc', auth='public', website=True)
    def repair_sign(self, repair_id, kind, name=None, signature=None, access_token=None, **kwargs):
        access_token = access_token or request.httprequest.args.get('access_token')
        price_hash = request.httprequest.args.get('price_hash')
        try:
            repair = self._document_check_access('repair.order', repair_id, access_token)
        except (AccessError, MissingError):
            return {'error': request.env._('Invalid repair link.')}
        try:
            with request.env.cr.savepoint():
                repair._qing_sign(kind, name, signature, price_hash)
        except (UserError, ValidationError) as exc:
            return {'error': str(exc)}
        return {'force_refresh': True, 'redirect_url': repair.get_portal_url()}
