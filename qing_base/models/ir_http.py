import json
import logging
import re

from odoo import models
from odoo.exceptions import AccessError
from odoo.http import request

from ..tools import access_events


_logger = logging.getLogger(__name__)
_MODEL = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*$")
_METHOD = re.compile(r"^[a-z][a-z0-9_]{0,99}$")
_ROUTE = re.compile(r"^/[a-zA-Z0-9_./<>:-]{0,127}$")
_OPERATIONS = {
    'web_read': 'read', 'web_search_read': 'read', 'read': 'read',
    'search_read': 'read', 'search': 'read', 'web_save': 'write',
    'web_save_multi': 'write', 'write': 'write', 'create': 'create',
    'unlink': 'unlink', 'web_unlink': 'unlink',
}

from ..tools import mommy_base_loaded


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        result = super().session_info()
        if self.env.user._is_internal():
            module = self.env['ir.module.module']._get('qing_base')
            result['qing_base_version'] = module.latest_version or ''
            result['qing_legacy_mommy_base'] = mommy_base_loaded(self.env)
            result['qing_web_title'] = self.env['ir.config_parameter'].sudo().get_str(
                'qing_base.title', default='QingERP 20.0',
            )
            result['qing_access_requests'] = True
        return result

    @classmethod
    def _qing_request_snapshot(cls, rule, route_args):
        if not request.env.uid:
            return None
        user = request.env.user
        if not user._is_internal():
            return None
        allowed = {company.id: company.display_name for company in user.company_ids}
        active = request.env.companies.ids
        data = request.get_json_data() if request.httprequest.is_json else {}
        data = data if isinstance(data, dict) else {}
        params = data.get('params') if isinstance(data.get('params'), dict) else {}
        route = rule.rule if rule else ''
        dataset = route.startswith('/web/dataset/call_')
        json2 = route.startswith('/json/2/')
        model = params.get('model') if dataset else route_args.get('__model__') if json2 else ''
        model = model if isinstance(model, str) and len(model) <= 128 and _MODEL.fullmatch(model) else ''
        method = params.get('method') if dataset else route_args.get('__method__') if json2 else ''
        method = method if isinstance(method, str) else ''
        if dataset or json2:
            entry = '/json/2' if json2 else (
                '/web/dataset/call_button' if route.startswith('/web/dataset/call_button') else '/web/dataset/call_kw')
            if model and model in request.registry and _METHOD.fullmatch(method):
                public_method = getattr(request.registry[model], method, None)
                if callable(public_method):
                    entry += '/' + method
        else:
            entry = route if _ROUTE.fullmatch(route) else '/other'
        kwargs = data if json2 else params.get('kwargs') if isinstance(params.get('kwargs'), dict) else {}
        context = kwargs.get('context') if isinstance(kwargs.get('context'), dict) else {}
        active_ids = context.get('allowed_company_ids')
        if isinstance(active_ids, list) and active_ids and all(
            isinstance(item, int) and not isinstance(item, bool) and item in allowed for item in active_ids
        ):
            active = list(dict.fromkeys(active_ids))
        current_company = active[0] if active else request.env.company.id
        args = params.get('args') if isinstance(params.get('args'), list) else []
        ids = data.get('ids', []) if json2 else args[0] if args and isinstance(args[0], list) else []
        ids = ids if isinstance(ids, list) else []
        record_id = ids[0] if len(ids) == 1 and isinstance(ids[0], int) and not isinstance(ids[0], bool) and ids[0] > 0 else 0
        return {
            'db': request.db, 'user_id': request.env.uid, 'company_id': current_company,
            'allowed_companies': json.dumps(sorted(allowed), separators=(',', ':')),
            'active_companies': json.dumps(sorted(active), separators=(',', ':')),
            'allowed_names': allowed, 'model_name': model or 'unknown',
            'operation': _OPERATIONS.get(method, 'other'),
            'entry': entry,
            'record_id': record_id, 'method': method,
            'partial_contract': context.get('qing_partial_read') is True,
        }

    @classmethod
    def _pre_dispatch(cls, rule, args):
        try:
            request.qing_access_snapshot = cls._qing_request_snapshot(rule, args)
        except Exception:
            request.qing_access_snapshot = None
            _logger.warning("Could not prepare permission diagnostic metadata")
        return super()._pre_dispatch(rule, args)

    @classmethod
    def _post_dispatch(cls, response):
        result = super()._post_dispatch(response)
        snapshot = getattr(request, 'qing_access_snapshot', None)
        if not snapshot or not snapshot['partial_contract'] or snapshot['method'] not in ('web_read', 'web_search_read'):
            return result
        try:
            if response.mimetype != 'application/json':
                return result
            body = response.get_data()
            if len(body) > 5_000_000:
                access_events._drop('response_scan_limit')
                return result
            if b'__qing_restricted_fields' not in body:
                return result
            payload = response.get_json(silent=True)
            if snapshot['entry'].startswith('/json/2/'):
                values = payload
            else:
                if not isinstance(payload, dict) or 'error' in payload:
                    return result
                values = payload.get('result')
            if not access_events.contains_restricted(values):
                return result
            event = {key: snapshot[key] for key in (
                'user_id', 'company_id', 'allowed_companies', 'active_companies',
                'model_name', 'operation', 'entry')}
            event.update(event_type='restricted', impact='masked_readonly', cause='unknown')
            dbname = snapshot['db']
            request.env.cr.postcommit.add(lambda: access_events.enqueue(dbname, event))
        except Exception:
            access_events._drop('response_collection_failed')
        return result

    @classmethod
    def _handle_error(cls, exception):
        if not isinstance(exception, AccessError):
            return super()._handle_error(exception)
        snapshot = getattr(request, 'qing_access_snapshot', None)
        suggested = None
        source_context = getattr(exception, 'context', None)
        if snapshot and isinstance(source_context, dict):
            company = source_context.get('suggested_company')
            company_id = company.get('id') if isinstance(company, dict) else None
            if isinstance(company_id, int) and not isinstance(company_id, bool) and company_id in snapshot['allowed_names']:
                suggested = {'id': company_id, 'display_name': snapshot['allowed_names'][company_id]}
        if snapshot:
            try:
                event = {key: snapshot[key] for key in (
                    'user_id', 'company_id', 'allowed_companies', 'active_companies',
                    'model_name', 'operation', 'entry')}
                event.update(event_type='denied', impact='none', cause='company_hint' if suggested else 'unknown')
                dbname = snapshot['db']
                cr = request.env.cr
                if cr.closed:
                    access_events.enqueue(dbname, event)
                else:
                    cr.postrollback.add(lambda: access_events.enqueue(dbname, event))
            except Exception:
                access_events._drop('denial_collection_failed')
        # Do not serialize exception arguments, context or traceback supplied by business code.
        message = 'This operation could not be completed because access is restricted. The exact reason is currently unknown.'
        try:
            if request.env and suggested:
                message = request.env._('This operation may require access in %s. Switch to that authorized company and try again.', suggested['display_name'])
            elif request.env:
                message = request.env._('This operation could not be completed because access is restricted. The exact reason is currently unknown.')
        except Exception:
            pass
        safe = AccessError(message)
        safe_context = {}
        if suggested:
            safe_context['suggested_company'] = suggested
        if snapshot:
            safe_context['qing_access'] = {
                'model': snapshot['model_name'] if snapshot['model_name'] != 'unknown' else '',
                'operation': snapshot['operation'], 'record_id': snapshot['record_id'],
            }
        safe.context = safe_context
        return super()._handle_error(safe)
