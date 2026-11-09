from odoo import http
from odoo.http import request


class KnowledgeAssets(http.Controller):
    @http.route('/qing/knowledge/draft_asset/<string:draft_uuid>/<path:logical_path>', type='http', auth='user', methods=['GET'])
    def get_draft_asset(self, draft_uuid, logical_path, download=None):
        Draft = request.env['qing.knowledge.draft']
        draft = Draft.search([('uuid', '=', draft_uuid)], limit=1)
        if not draft or (draft.author_id.id != request.env.uid and draft.state != 'submitted'):
            return request.not_found()
        asset = request.env['qing.knowledge.asset'].search([
            ('draft_id', '=', draft.id), ('logical_path', '=', logical_path),
        ], limit=1)
        if not asset or not asset.attachment_id:
            return request.not_found()
        stream = request.env['ir.binary']._get_stream_from(asset.attachment_id, field_name='raw')
        response = stream.get_response(as_attachment=download == '1' or not (asset.mime.startswith('image/') or asset.mime == 'application/pdf'))
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
        return response

    @http.route('/qing/knowledge/asset/<string:revision_uuid>/<path:logical_path>', type='http', auth='user', methods=['GET'])
    def get_asset(self, revision_uuid, logical_path, download=None):
        Revision = request.env['qing.knowledge.revision']
        revision = Revision.search([('uuid', '=', revision_uuid)], limit=1)
        if not revision:
            return request.not_found()
        Asset = request.env['qing.knowledge.asset']
        asset = Asset.search([('revision_id', '=', revision.id), ('logical_path', '=', logical_path)], limit=1)
        if not asset or not asset.attachment_id:
            return request.not_found()
        attachment = asset.attachment_id
        stream = request.env['ir.binary']._get_stream_from(attachment, field_name='raw')
        response = stream.get_response(as_attachment=download == '1' or not (asset.mime.startswith('image/') or asset.mime == 'application/pdf'))
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
        return response
