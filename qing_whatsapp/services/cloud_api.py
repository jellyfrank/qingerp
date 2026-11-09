"""WhatsApp Cloud API transport. No ORM, arbitrary URLs or business writes."""
import hashlib
import hmac
import re
from urllib.parse import urlparse

import requests

MAX_MEDIA_BYTES = 16 * 1024 * 1024


class CloudError(Exception):
    def __init__(self, code='network', retryable=False, uncertain=False):
        super().__init__(str(code))
        self.code = str(code)
        self.retryable = retryable
        self.uncertain = uncertain


def valid_signature(raw, signature, secret):
    if not secret or not re.fullmatch(r'sha256=[0-9a-f]{64}', signature or ''):
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature[7:])


class CloudAPI:
    def __init__(self, token, version, phone_id):
        if not re.fullmatch(r'v\d+\.\d+', version or '') or not str(phone_id).isdigit():
            raise CloudError('configuration')
        self.base = 'https://graph.facebook.com/' + version
        self.phone_id = phone_id
        self.headers = {'Authorization': 'Bearer ' + token}

    def request(self, method, path, *, payload=None, params=None, files=None):
        if not re.fullmatch(r'[A-Za-z0-9_/-]+', path):
            raise CloudError('invalid_path')
        try:
            response = requests.request(method, self.base + '/' + path,
                headers=self.headers, json=payload, params=params, files=files,
                timeout=(10, 30), allow_redirects=False)
        except requests.RequestException as exc:
            raise CloudError('network', uncertain=method == 'POST', retryable=method == 'GET') from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise CloudError('invalid_response', uncertain=method == 'POST') from exc
        if not response.ok or data.get('error'):
            code = data.get('error', {}).get('code', response.status_code)
            # Only a definitive throttling rejection is safe to resend automatically.
            raise CloudError(code, retryable=response.status_code == 429,
                             uncertain=method == 'POST' and response.status_code >= 500)
        return data

    def send(self, payload):
        data = self.request('POST', self.phone_id + '/messages', payload=payload)
        uid = (data.get('messages') or [{}])[0].get('id')
        if not uid:
            raise CloudError('missing_message_id', uncertain=True)
        return uid

    def upload(self, name, content, mime):
        try:
            data = self.request('POST', self.phone_id + '/media',
                params={'messaging_product': 'whatsapp'}, files={'file': (name, content, mime)})
        except CloudError as exc:
            # A duplicate media upload cannot deliver a customer message.
            raise CloudError(exc.code, retryable=exc.retryable or exc.uncertain) from exc
        if not data.get('id'):
            raise CloudError('missing_media_id')
        return data['id']

    def download(self, media_id):
        info = self.request('GET', str(media_id))
        url = info.get('url', '')
        host = (urlparse(url).hostname or '').lower()
        if urlparse(url).scheme != 'https' or not (host.endswith('.facebook.com') or host.endswith('.fbcdn.net')):
            raise CloudError('untrusted_media_host')
        try:
            with requests.get(url, headers=self.headers, timeout=(10, 30),
                              stream=True, allow_redirects=False) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise CloudError('media_redirect_rejected')
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > MAX_MEDIA_BYTES:
                        raise CloudError('media_too_large')
                    chunks.append(chunk)
        except requests.RequestException as exc:
            raise CloudError('media_download', retryable=True) from exc
        content = b''.join(chunks)
        if info.get('sha256') and hashlib.sha256(content).hexdigest() != info['sha256']:
            raise CloudError('media_hash_mismatch')
        return content, info.get('mime_type', 'application/octet-stream')
