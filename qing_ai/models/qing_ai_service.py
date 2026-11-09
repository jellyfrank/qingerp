"""Small, server-side AI text interface used by QingERP feature modules."""

import json
import logging
import re
import time
from urllib.parse import urlsplit

import requests

from odoo import models
from odoo.exceptions import AccessError, UserError


_logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = (5, 60)
TEST_PROMPT = 'Reply with OK.'
DEFAULT_MAX_OUTPUT_TOKENS = 4096
MIN_MAX_OUTPUT_TOKENS = 256
MAX_MAX_OUTPUT_TOKENS = 8192
TEST_MAX_OUTPUT_TOKENS = 256
# Retries only cover failures where the provider did not accept the request
# (connection errors) or explicitly asked the client to come back later.
# Read timeouts are never retried because the provider may still bill the call.
MAX_RETRIES = 2
MAX_RETRY_DELAY_SECONDS = 4
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
PERMANENT_ERROR_CODES = frozenset({
    'insufficient_quota', 'billing_hard_limit_reached', 'billing_not_active',
    'invalid_api_key', 'account_deactivated',
})
SAFE_TOKEN = re.compile(r'^[A-Za-z0-9_.:-]{1,64}$')
REQUEST_ID_HEADERS = ('x-request-id', 'request-id', 'x-goog-request-id', 'x-trace-id')


class ProviderHTTPError(UserError):
    """A provider rejected the request with an HTTP status the caller may react to."""

    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


def safe_token(value):
    """Return a short identifier-like provider value, never free-form text."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ''
    value = str(value)
    return value if SAFE_TOKEN.match(value) else ''


class QingAiService(models.AbstractModel):
    _name = 'qing.ai.service'
    _description = 'Qing AI Text Service'

    def _provider_codes(self):
        """Adapters extend this whitelist without coupling the core to their addons."""
        return ()

    def _usage_summary(self, usage):
        """Normalize exact provider counters without estimating missing values."""
        usage = usage if isinstance(usage, dict) else {}

        def number(*paths):
            for path in paths:
                value = usage
                for key in path:
                    value = value.get(key) if isinstance(value, dict) else None
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                    return value
            return None

        summary = {
            'input_tokens': number(
                ('input_tokens',), ('prompt_tokens',), ('promptTokenCount',),
            ),
            # Prompt-cache hits are part of input_tokens and usually billed at a lower rate.
            'cached_input_tokens': number(
                ('prompt_cache_hit_tokens',), ('cachedContentTokenCount',),
                ('input_tokens_details', 'cached_tokens'), ('prompt_tokens_details', 'cached_tokens'),
            ),
            'output_tokens': number(
                ('output_tokens',), ('completion_tokens',), ('candidatesTokenCount',),
            ),
            'reasoning_tokens': number(
                ('reasoning_tokens',), ('thoughtsTokenCount',),
                ('output_tokens_details', 'reasoning_tokens'),
                ('completion_tokens_details', 'reasoning_tokens'),
            ),
            'total_tokens': number(('total_tokens',), ('totalTokenCount',)),
        }
        return {key: value for key, value in summary.items() if value is not None}

    def _provider_config(self, provider):
        if provider not in self._provider_codes():
            raise UserError(self.env._('The AI provider is not installed.'))
        parameters = self.env['ir.config_parameter'].sudo()
        prefix = f'qing_ai.{provider}.'
        return {
            'enabled': parameters.get_bool(prefix + 'enabled'),
            'api_key': parameters.get_str(prefix + 'api_key'),
            'endpoint': parameters.get_str(prefix + 'endpoint'),
            'model': parameters.get_str(prefix + 'model'),
            'image_enabled': parameters.get_bool(
                prefix + 'image_enabled', provider in ('openai', 'gemini')
            ),
        }

    def _default_max_output_tokens(self):
        """Return the administrator's answer budget, clamped to the supported range."""
        value = self.env['ir.config_parameter'].sudo().get_int(
            'qing_ai.max_output_tokens', DEFAULT_MAX_OUTPUT_TOKENS
        )
        return min(max(value, MIN_MAX_OUTPUT_TOKENS), MAX_MAX_OUTPUT_TOKENS)

    def _complete_text(self, prompt, provider=None, config=None, require_enabled=True,
                       max_output_tokens=None, image=None, system=None, json_mode=False,
                       on_delta=None, tools=None):
        """
        Return {text, model, usage, truncated} for trusted server-side callers.

        :param prompt: user-role content (request, context and other untrusted data)
        :param system: optional system instructions kept separate from untrusted data
        :param json_mode: ask the provider for one JSON object when the model supports it
        :param on_delta: callback receiving answer text fragments while the provider streams
        :param tools: native function definitions ({name, description, parameters}); the
            result then carries ``tool_call`` ({name, arguments}) when the model calls one
        """
        if not isinstance(prompt, str) or not prompt.strip():
            raise UserError(self.env._('Enter a text prompt.'))
        if max_output_tokens is None:
            max_output_tokens = self._default_max_output_tokens()
        if (isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int)
                or not 1 <= max_output_tokens <= MAX_MAX_OUTPUT_TOKENS):
            raise UserError(self.env._('The AI output token limit must be between 1 and 8192.'))
        if provider is None:
            provider = self.env['ir.config_parameter'].sudo().get_str(
                'qing_ai.default_provider', 'none'
            )
        if provider not in self._provider_codes():
            raise UserError(self.env._('Select an installed AI provider in Settings.'))

        config = config if config is not None else self._provider_config(provider)
        if require_enabled and not config.get('enabled'):
            raise UserError(self.env._('Enable the selected AI provider in Settings.'))
        if not config.get('api_key') or not config.get('model') or not config.get('endpoint'):
            raise UserError(self.env._('Set the AI provider key, endpoint and model.'))
        if image and not config.get('image_enabled'):
            raise UserError(self.env._('Image input is not enabled for this provider and model.'))
        completion = getattr(self, f'_complete_{provider}')
        options = {'image': image or None, 'system': system or None}
        if tools:
            options['tools'] = tools
        try:
            result = completion(prompt, config, max_output_tokens, json_mode=json_mode,
                                on_delta=on_delta, **options)
        except ProviderHTTPError as error:
            # Some compatible models reject structured output or streaming; the JSON contract
            # in the instructions still applies, so retry once with the plain request.
            # Native tool rejections are left to the caller, which switches protocols.
            if tools or not (json_mode or on_delta) or error.status not in (400, 422):
                raise
            _logger.info('Qing AI %s rejected JSON mode or streaming (HTTP %s); retrying without them.',
                         provider, error.status)
            result = completion(prompt, config, max_output_tokens, json_mode=False, on_delta=None, **options)
        result.setdefault('truncated', False)
        result.setdefault('tool_call', None)
        if result['truncated']:
            _logger.info('Qing AI %s response reached the output token limit (%s).',
                         provider, max_output_tokens)
        return result

    def _test_provider(self, provider, config):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._('Only system administrators can test AI providers.'))
        result = self._complete_text(
            TEST_PROMPT, provider=provider, config=config, require_enabled=False,
            max_output_tokens=TEST_MAX_OUTPUT_TOKENS,
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': self.env._('AI connection successful'),
                'message': self.env._('The %(provider)s model %(model)s responded.',
                                      provider=provider, model=result['model']),
                'type': 'success',
                'sticky': False,
            },
        }

    def _raise_empty_text(self, truncated=False):
        """Explain an empty answer, distinguishing an exhausted output budget."""
        if truncated:
            raise UserError(self.env._(
                'The AI model used its whole output token limit before writing an answer. '
                'Increase the maximum output tokens in Qing AI settings or choose a model '
                'that reasons less.'
            ))
        raise UserError(self.env._('The AI provider response has no text.'))

    def _provider_error_code(self, response):
        """Extract a short provider error code without reading free-form messages."""
        try:
            data = response.json()
        except ValueError:
            return ''
        error = data.get('error') if isinstance(data, dict) else None
        if not isinstance(error, dict):
            return ''
        code = error.get('code')
        candidates = (
            code if isinstance(code, str) else None,
            error.get('type'),
            error.get('status'),
            code,
        )
        for candidate in candidates:
            token = safe_token(candidate)
            if token:
                return token
        return ''

    def _provider_request_id(self, response):
        headers = getattr(response, 'headers', None)
        if not hasattr(headers, 'get'):
            return ''
        for name in REQUEST_ID_HEADERS:
            token = safe_token(headers.get(name))
            if token:
                return token
        return ''

    def _retry_delay(self, response, attempt):
        """Honor a short numeric Retry-After header, otherwise back off exponentially."""
        delay = 2 ** attempt
        headers = getattr(response, 'headers', None)
        value = headers.get('Retry-After') if hasattr(headers, 'get') else None
        if isinstance(value, str) and value.strip().isdigit():
            delay = int(value.strip())
        return min(max(delay, 1), MAX_RETRY_DELAY_SECONDS)

    def _provider_error_message(self, status, code):
        """Map an HTTP failure to an actionable, secret-free message."""
        _ = self.env._
        if status in (401, 403):
            message = _('The AI provider rejected the API key or the key lacks permission '
                        '(HTTP %(status)s).', status=status)
        elif status == 404:
            message = _('The AI provider endpoint or model was not found (HTTP 404). '
                        'Check the endpoint and model name.')
        elif status == 429:
            message = _('The AI provider rate limit or account quota was exceeded (HTTP 429). '
                        'Try again later or check the provider account balance.')
        elif status in (408, 504):
            message = _('The AI provider timed out (HTTP %(status)s). Please try again.',
                        status=status)
        elif status == 413:
            message = _('The request is too large for the AI provider (HTTP 413). '
                        'Shorten the message or attachments.')
        elif status in (400, 422):
            message = _('The AI provider rejected the request (HTTP %(status)s). '
                        'Check the model name and the features it supports.', status=status)
        elif status >= 500:
            message = _('The AI provider is temporarily unavailable (HTTP %(status)s). '
                        'Please try again later.', status=status)
        else:
            message = _('The AI provider returned HTTP %(status)s.', status=status)
        if code:
            message += ' ' + _('Provider error code: %(code)s.', code=code)
        return message

    def _endpoint_hostname(self, url, allow_query=False):
        """Validate a provider URL and return its host for logging."""
        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname
        except ValueError as exc:
            raise UserError(self.env._('The AI endpoint is not a valid URL.')) from exc
        if parsed.scheme != 'https' or not hostname or parsed.username or parsed.password:
            raise UserError(self.env._('The AI endpoint must be an HTTPS URL without credentials.'))
        if parsed.fragment or (parsed.query and not allow_query):
            raise UserError(self.env._('The AI endpoint cannot contain a query or fragment.'))
        return hostname

    def _send_request(self, url, payload, api_key, key_header, stream=False, allow_query=False):
        """
        POST JSON with bounded timeouts and retries; return a successful (HTTP 200) response.

        Retries only happen before the provider accepted the request, so a streamed
        answer is never requested twice once its first byte arrived.
        """
        hostname = self._endpoint_hostname(url, allow_query=allow_query)
        headers = {'Content-Type': 'application/json'}
        if stream:
            headers['Accept'] = 'text/event-stream'
        headers[key_header] = f'Bearer {api_key}' if key_header == 'Authorization' else api_key
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = requests.post(
                    url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT,
                    allow_redirects=False, stream=stream,
                )
            except requests.ConnectionError as exc:
                # Includes connect timeouts: the provider never accepted the request.
                if attempt < MAX_RETRIES:
                    _logger.warning('Qing AI provider %s unreachable; retrying (%s/%s).',
                                    hostname, attempt + 1, MAX_RETRIES)
                    time.sleep(min(2 ** attempt, MAX_RETRY_DELAY_SECONDS))
                    continue
                raise UserError(self.env._('The AI provider could not be reached.')) from exc
            except requests.Timeout as exc:
                raise UserError(self.env._(
                    'The AI provider did not answer within %(seconds)s seconds. Please try again.',
                    seconds=REQUEST_TIMEOUT[1],
                )) from exc
            except requests.RequestException as exc:
                raise UserError(self.env._('The AI provider could not be reached.')) from exc
            status = response.status_code
            if status == 200:
                return response
            code = self._provider_error_code(response)
            request_id = self._provider_request_id(response)
            if stream and hasattr(response, 'close'):
                response.close()
            if status in RETRY_STATUSES and code not in PERMANENT_ERROR_CODES and attempt < MAX_RETRIES:
                delay = self._retry_delay(response, attempt)
                _logger.warning(
                    'Qing AI provider %s returned HTTP %s (code=%s, request_id=%s); '
                    'retrying in %ss (%s/%s).',
                    hostname, status, code or '-', request_id or '-', delay, attempt + 1, MAX_RETRIES,
                )
                time.sleep(delay)
                continue
            _logger.warning('Qing AI provider %s returned HTTP %s (code=%s, request_id=%s).',
                            hostname, status, code or '-', request_id or '-')
            raise ProviderHTTPError(self._provider_error_message(status, code), status)

    def _post_json(self, url, payload, api_key, *, key_header='Authorization'):
        """Send JSON with bounded timeouts and retries, no redirect or body disclosure."""
        response = self._send_request(url, payload, api_key, key_header)
        try:
            data = response.json()
        except ValueError as exc:
            raise UserError(self.env._('The AI provider returned invalid JSON.')) from exc
        if not isinstance(data, dict):
            raise UserError(self.env._('The AI provider returned an unexpected response.'))
        return data

    def _post_stream(self, url, payload, api_key, *, key_header='Authorization', allow_query=False):
        """Yield the JSON events of a server-sent event stream."""
        response = self._send_request(url, payload, api_key, key_header, stream=True,
                                      allow_query=allow_query)
        try:
            for raw in response.iter_lines():
                # Decode bytes ourselves: SSE responses often omit the charset.
                line = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else (raw or '')
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    break
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    yield event
        except requests.RequestException as exc:
            raise UserError(self.env._('The AI provider stopped responding while streaming the answer.')) from exc
        finally:
            response.close()

    def _resolve_provider(self, provider=None):
        """Return the provider code that a call with ``provider`` would use."""
        if provider:
            return provider
        return self.env['ir.config_parameter'].sudo().get_str('qing_ai.default_provider', 'none')

    def _native_tool_provider_codes(self):
        """Adapters whose API supports native function calling register themselves here."""
        return ()

    def _supports_native_tools(self, provider=None):
        return self._resolve_provider(provider) in self._native_tool_provider_codes()

    def _parse_tool_arguments(self, raw):
        """Decode function arguments; invalid JSON yields None so the planner can retry."""
        if isinstance(raw, dict):
            return raw
        try:
            value = json.loads(raw or '{}')
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def _chat_completion(self, prompt, config, max_output_tokens, image=None, system=None,
                         json_mode=False, on_delta=None, tools=None):
        """OpenAI-compatible chat protocol shared by DeepSeek and GLM."""
        content = prompt
        if image:
            content = [
                {'type': 'text', 'text': prompt},
                {'type': 'image_url', 'image_url': {
                    'url': f"data:{image['mimetype']};base64,{image['data']}",
                }},
            ]
        messages = [{'role': 'user', 'content': content}]
        if system:
            # A stable system prefix also lets providers such as DeepSeek reuse their prompt cache.
            messages.insert(0, {'role': 'system', 'content': system})
        payload = {'model': config['model'], 'messages': messages,
                   'stream': False, 'max_tokens': max_output_tokens}
        if tools:
            payload['tools'] = [{'type': 'function', 'function': tool} for tool in tools]
            payload['tool_choice'] = 'auto'
        elif json_mode:
            payload['response_format'] = {'type': 'json_object'}
        if on_delta and not tools:
            payload['stream'] = True
            return self._stream_chat_completion(payload, config, on_delta)
        data = self._post_json(config['endpoint'], payload, config['api_key'])
        try:
            choice = data['choices'][0]
            message = choice['message']
            text = message.get('content') or ''
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise UserError(self.env._('The AI provider response has no text.')) from exc
        truncated = isinstance(choice, dict) and choice.get('finish_reason') == 'length'
        tool_call = None
        calls = message.get('tool_calls') if isinstance(message, dict) else None
        if isinstance(calls, list) and calls and isinstance(calls[0], dict):
            function = calls[0].get('function') or {}
            tool_call = {'name': function.get('name') or '',
                         'arguments': self._parse_tool_arguments(function.get('arguments'))}
        if not tool_call and (not isinstance(text, str) or not text.strip()):
            self._raise_empty_text(truncated)
        return {'text': text if isinstance(text, str) else '', 'model': data.get('model') or config['model'],
                'usage': data.get('usage') or {}, 'truncated': truncated, 'tool_call': tool_call}

    def _stream_chat_completion(self, payload, config, on_delta):
        """Stream an OpenAI-compatible chat answer, forwarding text deltas as they arrive."""
        parts, usage, model, finish = [], {}, config['model'], None
        for event in self._post_stream(config['endpoint'], payload, config['api_key']):
            model = event.get('model') or model
            if isinstance(event.get('usage'), dict):
                usage = event['usage']
            choices = event.get('choices')
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                continue
            finish = choices[0].get('finish_reason') or finish
            delta = (choices[0].get('delta') or {}).get('content')
            if isinstance(delta, str) and delta:
                parts.append(delta)
                on_delta(delta)
        text = ''.join(parts)
        truncated = finish == 'length'
        if not text.strip():
            self._raise_empty_text(truncated)
        return {'text': text, 'model': model, 'usage': usage, 'truncated': truncated, 'tool_call': None}
