from urllib.parse import quote

from odoo import models
from odoo.exceptions import UserError


DEFAULT_ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta'
DEFAULT_MODEL = 'gemini-2.5-flash'


class QingAiService(models.AbstractModel):
    _inherit = 'qing.ai.service'

    def _provider_codes(self):
        return (*super()._provider_codes(), 'gemini')

    def _gemini_candidate(self, data):
        """Return (text, truncated) of the first candidate, ignoring thought summaries."""
        candidates = data.get('candidates')
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            return '', False
        candidate = candidates[0]
        # Thinking models spend part of maxOutputTokens before writing; when the budget is
        # exhausted the candidate ends with MAX_TOKENS and may carry no text parts at all.
        truncated = candidate.get('finishReason') == 'MAX_TOKENS'
        content = candidate.get('content')
        parts = content.get('parts') if isinstance(content, dict) else None
        if not isinstance(parts, list):
            parts = []
        text = ''.join(
            part['text'] for part in parts
            if isinstance(part, dict) and isinstance(part.get('text'), str) and not part.get('thought')
        )
        return text, truncated

    def _stream_gemini(self, url, payload, config, on_delta):
        parts, usage, model, truncated = [], {}, config['model'], False
        for event in self._post_stream(url, payload, config['api_key'], key_header='x-goog-api-key',
                                       allow_query=True):
            text, chunk_truncated = self._gemini_candidate(event)
            truncated = truncated or chunk_truncated
            usage = event.get('usageMetadata') or usage
            model = event.get('modelVersion') or model
            if text:
                parts.append(text)
                on_delta(text)
        text = ''.join(parts).strip()
        if not text:
            self._raise_empty_text(truncated)
        return {'text': text, 'model': model, 'usage': usage, 'truncated': truncated, 'tool_call': None}

    def _complete_gemini(self, prompt, config, max_output_tokens, image=None, system=None,
                         json_mode=False, on_delta=None, tools=None):
        model = quote(config['model'], safe='')
        endpoint = config['endpoint'].rstrip('/')
        parts = [{'text': prompt}]
        if image:
            parts.append({'inline_data': {
                'mime_type': image['mimetype'], 'data': image['data'],
            }})
        generation = {'maxOutputTokens': max_output_tokens}
        if json_mode:
            generation['responseMimeType'] = 'application/json'
        payload = {'contents': [{'role': 'user', 'parts': parts}], 'generationConfig': generation}
        if system:
            payload['systemInstruction'] = {'parts': [{'text': system}]}
        if on_delta:
            # The query string is built here, after the configured endpoint was validated.
            self._endpoint_hostname(endpoint)
            return self._stream_gemini(
                f'{endpoint}/models/{model}:streamGenerateContent?alt=sse', payload, config, on_delta,
            )
        data = self._post_json(
            f'{endpoint}/models/{model}:generateContent', payload,
            config['api_key'], key_header='x-goog-api-key',
        )
        try:
            candidate = data['candidates'][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise UserError(self.env._('The AI provider response has no text.')) from exc
        if not isinstance(candidate, dict):
            raise UserError(self.env._('The AI provider response has no text.'))
        # Thinking models spend part of maxOutputTokens before writing; when the budget is
        # exhausted the candidate ends with MAX_TOKENS and may carry no text parts at all.
        truncated = candidate.get('finishReason') == 'MAX_TOKENS'
        content = candidate.get('content')
        parts = content.get('parts') if isinstance(content, dict) else None
        if not isinstance(parts, list):
            parts = []
        text = '\n'.join(
            part['text'] for part in parts
            if isinstance(part, dict) and isinstance(part.get('text'), str) and not part.get('thought')
        ).strip()
        if not text:
            self._raise_empty_text(truncated)
        return {'text': text, 'model': data.get('modelVersion') or config['model'],
                'usage': data.get('usageMetadata') or {}, 'truncated': truncated, 'tool_call': None}
