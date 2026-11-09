from odoo import models


DEFAULT_ENDPOINT = 'https://api.openai.com/v1/responses'
DEFAULT_MODEL = 'gpt-4.1-mini'


class QingAiService(models.AbstractModel):
    _inherit = 'qing.ai.service'

    def _provider_codes(self):
        return (*super()._provider_codes(), 'openai')

    def _native_tool_provider_codes(self):
        return (*super()._native_tool_provider_codes(), 'openai')

    def _openai_result(self, data, config, text):
        details = data.get('incomplete_details')
        truncated = data.get('status') == 'incomplete' and isinstance(details, dict) and (
            details.get('reason') == 'max_output_tokens')
        return {'text': text, 'model': data.get('model') or config['model'],
                'usage': data.get('usage') or {}, 'truncated': truncated, 'tool_call': None}

    def _complete_openai(self, prompt, config, max_output_tokens, image=None, system=None,
                         json_mode=False, on_delta=None, tools=None):
        content = prompt
        if image:
            content = [{'role': 'user', 'content': [
                {'type': 'input_text', 'text': prompt},
                {'type': 'input_image', 'image_url': f"data:{image['mimetype']};base64,{image['data']}"},
            ]}]
        payload = {'model': config['model'], 'input': content, 'store': False,
                   'max_output_tokens': max_output_tokens}
        if system:
            payload['instructions'] = system
        if tools:
            payload['tools'] = [dict(tool, type='function', strict=False) for tool in tools]
            payload['parallel_tool_calls'] = False
        elif json_mode:
            payload['text'] = {'format': {'type': 'json_object'}}
        if on_delta and not tools:
            payload['stream'] = True
            return self._stream_openai(payload, config, on_delta)
        data = self._post_json(config['endpoint'], payload, config['api_key'])
        output = data.get('output')
        if not isinstance(output, list):
            output = []
        text_parts = [
            part.get('text')
            for item in output
            if isinstance(item, dict) and item.get('type') == 'message'
            for part in (item.get('content') or [])
            if isinstance(part, dict) and part.get('type') == 'output_text'
            and isinstance(part.get('text'), str)
        ]
        result = self._openai_result(data, config, '\n'.join(text_parts).strip())
        call = next((item for item in output
                     if isinstance(item, dict) and item.get('type') == 'function_call'), None)
        if call:
            result['tool_call'] = {'name': call.get('name') or '',
                                   'arguments': self._parse_tool_arguments(call.get('arguments'))}
        if not result['text'] and not result['tool_call']:
            self._raise_empty_text(result['truncated'])
        return result

    def _stream_openai(self, payload, config, on_delta):
        """Stream a Responses API answer: text deltas, then the final response summary."""
        parts, final = [], {}
        for event in self._post_stream(config['endpoint'], payload, config['api_key']):
            kind = event.get('type')
            if kind == 'response.output_text.delta' and isinstance(event.get('delta'), str):
                parts.append(event['delta'])
                on_delta(event['delta'])
            elif kind in ('response.completed', 'response.incomplete') and isinstance(event.get('response'), dict):
                final = event['response']
            elif kind in ('error', 'response.failed'):
                break
        result = self._openai_result(final, config, ''.join(parts).strip())
        if not result['text']:
            self._raise_empty_text(result['truncated'])
        return result
