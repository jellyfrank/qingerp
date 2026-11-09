import json
import re
from datetime import timedelta, timezone

from odoo import _
from odoo.exceptions import UserError
from odoo.addons.qing_sms.tools import plain_number
from .aliyun_client import AliyunSmsClient

STATUS = {'0': 'pending', '1': 'approved', '2': 'rejected', '10': 'canceled'}
CATEGORY = {'0': 'otp', '1': 'notification', '2': 'marketing', '3': 'international'}
VARIABLE = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')


def safe_code(value):
    value = str(value or '')
    return value if re.fullmatch(r'[A-Za-z0-9_.-]{1,150}', value) else 'PROVIDER_ERROR'


class AliyunAdapter:
    def __init__(self, account):
        account.ensure_one()
        credentials = account.sudo()
        if not credentials.key or not credentials.secret:
            raise UserError(_('请配置阿里云 Access Key 和 Secret。'))
        self.client = AliyunSmsClient(credentials.key, credentials.secret)

    def _call(self, action, **params):
        result = self.client.call(action, **params)
        if result.get('Code') != 'OK':
            raise UserError(_('阿里云查询失败：%s', safe_code(result.get('Code'))))
        return result

    def _list(self, action, key):
        rows, total = [], None
        for page in range(1, 101):
            result = self._call(action, PageIndex=page, PageSize=50)
            batch = result.get(key)
            count = result.get('TotalCount')
            if not isinstance(batch, list) or not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValueError('Incomplete page')
            if total is not None and count != total:
                raise ValueError('List changed during sync')
            total = count
            rows.extend(batch)
            if len(rows) == total:
                return rows
            if not batch or len(rows) > total:
                raise ValueError('Truncated or inconsistent list')
        raise ValueError('Sync exceeds 5000 records; narrow account scope')

    def test_configuration(self):
        self._call('QuerySmsTemplateList', PageIndex=1, PageSize=1)

    def snapshot(self):
        signs, templates = [], []
        for row in self._list('QuerySmsSignList', 'SmsSignList'):
            detail = self._call('GetSmsSign', SignName=row['SignName'])
            raw = str(detail.get('SignStatus', ''))
            signs.append({'remote_id': row['SignName'], 'name': row['SignName'],
                          'audit_state': STATUS.get(raw, 'unknown'), 'raw_status': raw,
                          'reject_reason': (detail.get('AuditInfo') or {}).get('RejectInfo', '')})
        for row in self._list('QuerySmsTemplateList', 'SmsTemplateList'):
            detail = self._call('GetSmsTemplate', TemplateCode=row['TemplateCode'])
            raw = str(detail.get('TemplateStatus', ''))
            content = detail.get('TemplateContent')
            if not isinstance(content, str) or not content:
                raise ValueError('Missing template content')
            variables = list(dict.fromkeys(VARIABLE.findall(content)))
            # Escape literal dollars; keep only parsed, explicit placeholders active.
            normalized = content.replace('$', '$$')
            for key in variables:
                normalized = normalized.replace('$${' + key + '}', '${' + key + '}')
            rules = detail.get('VariableAttribute') or '{}'
            rules = json.loads(rules) if isinstance(rules, str) else rules
            if not isinstance(rules, dict):
                raise ValueError('Invalid variable rules')
            sign_names = (detail.get('SignList') or {}).get('SignList', [])
            sign_names = list(sign_names) if isinstance(sign_names, list) else []
            if detail.get('RelatedSignName'):
                sign_names.append(detail['RelatedSignName'])
            for item in (detail.get('SignInfoList') or {}).get('SignInfoList', []):
                if item.get('SignName'):
                    sign_names.append(item['SignName'])
            templates.append({'remote_id': row['TemplateCode'], 'name': detail.get('TemplateName') or row['TemplateCode'],
                              'content': content, 'normalized_content': normalized, 'variables': variables,
                              'variable_rules': rules, 'sign_names': sorted(set(filter(None, sign_names))),
                              'audit_state': STATUS.get(raw, 'unknown'), 'raw_status': raw,
                              'category': CATEGORY.get(str(detail.get('TemplateType')), 'international'),
                              'reject_reason': (detail.get('AuditInfo') or {}).get('RejectInfo', '')})
        return signs, templates

    def validate_parameters(self, template, values):
        if set(values) != set(template.variables or []):
            raise UserError(_('阿里云模板变量不匹配。'))
        for key, value in values.items():
            # Common validation only. Versioned provider-specific attributes are preserved for review.
            if len(value) > 100 or re.search(r'[\r\n\x00-\x1f]', value):
                raise UserError(_('阿里云变量过长或含控制字符。'))
            if template.category == 'otp' and key.lower() == 'code' and not re.fullmatch(r'[A-Za-z0-9]{4,8}', value):
                raise UserError(_('验证码须为 4 至 8 位字母或数字。'))

    def create_template(self, application):
        """Create once; the application worker has already committed unknown intent."""
        payload = application.submitted_payload
        rules = payload.get('provider_variable_rules') or {}
        keys = {item['key'] for item in payload.get('variable_schema') or []}
        if set(rules) != keys:
            raise UserError(_('Aliyun requires an explicit rule for every template variable.'))
        params = {'TemplateName': payload['name'], 'TemplateContent': payload['content'],
                  'TemplateType': 0 if payload['category'] == 'otp' else 1,
                  'RelatedSignName': payload['sign'], 'Remark': payload['application_remark']}
        if rules:
            params['TemplateRule'] = json.dumps(rules, ensure_ascii=False, separators=(',', ':'))
        if payload.get('apply_scene_content'):
            params['ApplySceneContent'] = payload['apply_scene_content']
        result = self.client.call('CreateSmsTemplate', **params)
        code = safe_code(result.get('Code'))
        if code == 'OK' and result.get('TemplateCode'):
            return {'state': 'pending', 'code': code, 'remote_code': result['TemplateCode'],
                    'request_id': result.get('RequestId'), 'order_id': result.get('OrderId')}
        if code == 'OK' or code.startswith(('isp.', 'Internal', 'ServiceUnavailable')):
            return {'state': 'unknown', 'code': code, 'request_id': result.get('RequestId')}
        return {'state': 'failed', 'code': code, 'request_id': result.get('RequestId')}

    def validate_template_application(self, application):
        rules = application.provider_variable_rules or {}
        keys = {item['key'] for item in application.variable_schema or []}
        if set(rules) != keys:
            raise UserError(_('Aliyun requires an explicit rule for every template variable.'))
        return True

    def query_template(self, application):
        if not application.remote_code:
            raise UserError(_('An unknown request without a template code needs manual reconciliation.'))
        result = self._call('GetSmsTemplate', TemplateCode=application.remote_code)
        if result.get('TemplateCode') != application.remote_code:
            raise ValueError('Template code mismatch')
        state = STATUS.get(str(result.get('TemplateStatus')), 'pending')
        return {'state': state if state in ('approved', 'rejected') else 'pending', 'code': safe_code(result.get('Code')),
                'request_id': result.get('RequestId'), 'order_id': result.get('OrderId'),
                'reject_reason': (result.get('AuditInfo') or {}).get('RejectInfo', '')[:250]}

    def send(self, message):
        result = self.client.sendSms(plain_number(message.number), message.sign_name,
                                     message.remote_code, message.parameters, outid=message.sms_uuid)
        code = safe_code(result.get('Code'))
        accepted = code == 'OK' and bool(result.get('BizId'))
        # Missing identifiers or system errors do not prove non-delivery.
        unknown = code == 'OK' or code.startswith(('isp.', 'Internal', 'ServiceUnavailable'))
        return {'state': 'accepted' if accepted else 'unknown' if unknown else 'failed',
                'code': code, 'provider_id': result.get('BizId'), 'request_id': result.get('RequestId')}

    def query(self, message):
        if not message.submitted_at:
            return {'state': 'unknown', 'code': 'MISSING_SUBMIT_TIME'}
        # QuerySendDetails dates are provider-local dates (China), never the server timezone.
        date = (message.submitted_at.replace(tzinfo=timezone.utc) + timedelta(hours=8)).strftime('%Y%m%d')
        matches, seen, expected = [], 0, None
        for page in range(1, 101):
            result = self._call('QuerySendDetails', PhoneNumber=plain_number(message.number), SendDate=date,
                                PageSize=50, CurrentPage=page, BizId=message.provider_id or None)
            rows = (result.get('SmsSendDetailDTOs') or {}).get('SmsSendDetailDTO', [])
            total = int(result.get('TotalCount', 0))
            if not isinstance(rows, list) or total < 0:
                raise ValueError('Invalid receipt response')
            if expected is not None and total != expected:
                raise ValueError('Receipt list changed during query')
            expected = total
            seen += len(rows)
            if seen > total:
                raise ValueError('Inconsistent receipt count')
            for row in rows:
                if str(row.get('PhoneNum', '')) != plain_number(message.number):
                    continue
                # BizId-scoped API + matching recipient OR exact OutId; never infer from time/content alone.
                if row.get('OutId') == message.sms_uuid or message.provider_id and not row.get('OutId'):
                    matches.append(row)
            if seen == total:
                break
            if not rows:
                raise ValueError('Incomplete receipt list')
        else:
            raise ValueError('Receipt query exceeds 5000 records')
        if len(matches) != 1:
            return {'state': message.state, 'code': 'RECEIPT_NOT_CONFIRMED'}
        row = matches[0]
        state = {'1': 'accepted', '2': 'failed', '3': 'delivered'}.get(str(row.get('SendStatus')), message.state)
        return {'state': state, 'code': safe_code(row.get('ErrCode') or 'OK'), 'request_id': result.get('RequestId')}
