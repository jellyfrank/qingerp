import json
from datetime import datetime, timezone
from urllib.parse import quote
from uuid import uuid4

import requests
from aliyun_sms.sms import Sms


class AliyunSmsClient(Sms):
    """SDK signing helpers, fixed HTTPS endpoint, bounded transport, no automatic retry."""

    def get(self, data):
        data = {key: str(value) for key, value in data.items() if value is not None}
        signature = self.sign(data)
        query = self.get_to_sign(data)
        response = requests.get(
            'https://dysmsapi.aliyuncs.com/?Signature=%s&%s' % (quote(signature, safe=''), query),
            timeout=(5, 20), allow_redirects=False,
        )
        if response.status_code >= 500 or 300 <= response.status_code < 400:
            response.raise_for_status()
            raise ValueError('Unexpected redirect')
        result = response.json()
        if not isinstance(result, dict) or not result.get('Code'):
            raise ValueError('Invalid provider response')
        if response.status_code >= 400 and result.get('Code') == 'OK':
            response.raise_for_status()
        return result

    def call(self, action, **parameters):
        data = self._get_data(signature=uuid4().hex,
                              timestamp=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
        data.update(parameters, Action=action, RegionId='cn-hangzhou')
        return self.get(data)

    def query_sign(self, sign_name):
        return self.call('GetSmsSign', SignName=sign_name)

    def sendSms(self, phone, sign, code, params, outid=None, extendcode=None, signature=None, timestamp=None):
        # Override the SDK's send transport as published versions differ in their use of get().
        return self.call('SendSms', PhoneNumbers=phone, SignName=sign, TemplateCode=code,
                         TemplateParam=json.dumps(params, ensure_ascii=False, separators=(',', ':')),
                         OutId=outid, SmsUpExtendCode=extendcode)
