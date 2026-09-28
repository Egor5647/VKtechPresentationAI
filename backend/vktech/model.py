"""Remote model APIs: structured text/vision through Polza, no local inference."""
from __future__ import annotations
import base64
import copy
import hashlib
import io
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from pydantic import BaseModel, ValidationError
from .settings import ROOT, artifact_path, config


class ModelUnavailable(RuntimeError):
    pass


log = logging.getLogger(__name__)


def validation_issues(error: ValidationError, output_schema: dict) -> list[dict]:
    """Describe schema failures without logging source text or arbitrary JSON keys."""
    fields = set()
    def collect(value):
        if isinstance(value, dict):
            fields.update(value.get('properties', {}))
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
    collect(output_schema)
    issues = []
    for item in error.errors(include_input=False, include_context=False, include_url=False)[:12]:
        path = ''
        for part in item['loc']:
            if isinstance(part, int):
                path += f'[{part}]'
            else:
                name = part if part in fields else '<unknown_field>'
                path += ('.' if path else '') + name
        issues.append({'path': path or '$', 'type': item['type'], 'message': item['msg'][:400]})
    return issues


def encoded_image(path: Path) -> str:
    """Bound vision tokens while retaining enough slide detail for layout review."""
    from PIL import Image
    limit = int(os.environ.get("MODEL_IMAGE_MAX_EDGE", "896"))
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((limit, limit), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def validate_manifest(manifest):
    for role in ('text', 'image'):
        model = manifest.get(role, {})
        if not model.get('model') or not model.get('base_url') or not model.get('provider'):
            raise ValueError(f'Missing API model configuration: {role}')


@dataclass(frozen=True)
class Endpoint:
    url: str
    model: str
    provider: str
    key: str = field(repr=False)

    @property
    def configured(self):
        return bool(self.url and self.key)


def model_endpoint(kind='text', manifest=None):
    manifest = manifest or config('models.yaml')
    validate_manifest(manifest)
    selected = manifest[kind]
    prefix = 'MODEL' if kind == 'text' else 'T2I'
    url = os.environ.get(prefix + '_BASE_URL', selected['base_url']).strip().rstrip('/')
    parsed = urlsplit(url)
    if url and (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError(f'{prefix}_BASE_URL must be an API base URL without credentials or query parameters')
    is_polza = parsed.hostname == 'polza.ai'
    if is_polza and parsed.scheme != 'https':
        raise ValueError('Polza API requires HTTPS')
    name_var = 'MODEL_NAME' if kind == 'text' else 'T2I_MODEL'
    model = os.environ.get(name_var, '').strip() or selected['model']
    if model in selected.get('aliases', []):
        model = selected['model']
    if kind == 'text' and model != selected['model']:
        raise ValueError('MODEL_NAME must identify the model in config/models.yaml')
    # A Polza key is never implicitly sent to a custom endpoint.
    key = os.environ.get(prefix + '_API_KEY', '').strip()
    if not key and is_polza:
        key = os.environ.get('POLZA_API_KEY', '').strip()
    return Endpoint(url, model, 'polza' if is_polza else 'configured_endpoint', key)


def response_schema(role: str, payload: dict, schema: type[BaseModel]) -> dict:
    """Add request-specific constraints that Pydantic cannot express statically."""
    result = copy.deepcopy(schema.model_json_schema())
    if role == "planning":
        count = payload.get("slide_count")
        slides = result.get("properties", {}).get("slides", {})
        if isinstance(count, int) and count > 0 and slides.get("type") == "array":
            slides["minItems"] = count
            slides["maxItems"] = count
        plan_slide=result.get('$defs',{}).get('PlanSlide',{})
        properties=plan_slide.get('properties',{})
        for name,grammar_limit in {'message':260,'balanced_message':200,'takeaway':150}.items():
            if name in properties:
                # The grammar boundary intentionally exceeds the layout-safe
                # validator boundary. If prose reaches this outer limit, the
                # planner must rewrite it shorter instead of accepting a cut at
                # the exact length rendered on the slide.
                properties[name]['maxLength']=grammar_limit
                properties[name]['pattern']=r'.*[.!?)]$'
        if 'support_points' in properties:
            properties['support_points'].setdefault('items',{})['maxLength']=130
            properties['support_points']['items']['pattern']=r'.*[.!?)]$'
        if 'visual_items' in properties:properties['visual_items'].setdefault('items',{})['maxLength']=100
        required=plan_slide.setdefault('required',[])
        for name in ('takeaway','balanced_message','support_points','visual_items'):
            if name not in required:required.append(name)
    elif role == 'regenerate_slide':
        properties=result.get('properties',{})
        for name,grammar_limit in {'message':260,'balanced_message':200,'takeaway':150}.items():
            if name in properties:
                properties[name]['maxLength']=grammar_limit
                properties[name]['pattern']=r'.*[.!?)]$'
        if 'support_points' in properties:
            properties['support_points'].setdefault('items',{})['maxLength']=130
            properties['support_points']['items']['pattern']=r'.*[.!?)]$'
        if 'visual_items' in properties:properties['visual_items'].setdefault('items',{})['maxLength']=100
        required=result.setdefault('required',[])
        for name in ('takeaway','balanced_message','support_points','visual_items'):
            if name not in required:required.append(name)
    return result


class ModelGateway:
    def __init__(self, client=None):
        self.manifest = config("models.yaml"); validate_manifest(self.manifest)
        self.endpoint = model_endpoint('text', self.manifest)
        self.image_endpoint = model_endpoint('image', self.manifest)
        self.profile = self.endpoint.provider
        self.mode = 'api'
        self.model = self.endpoint.model
        self.model_config = self.manifest['text']
        self.response_format = os.environ.get('MODEL_RESPONSE_FORMAT', self.model_config.get('response_format', 'json_object'))
        if self.response_format not in {'json_object', 'json_schema'}:
            raise ValueError('MODEL_RESPONSE_FORMAT must be json_object or json_schema')
        self.reasoning_enabled = os.environ.get('MODEL_REASONING_ENABLED', '0') == '1'
        timeout = float(os.environ.get("MODEL_TIMEOUT_SECONDS", "300"))
        self.client = client or httpx.Client(timeout=httpx.Timeout(timeout, connect=10),
                                            trust_env=os.environ.get('MODEL_TRUST_ENV', '0') == '1')
        self.cache_enabled=os.environ.get('MODEL_CACHE','1' if client is None else '0')=='1'
        self.calls = []

    def _request(self, method, path, endpoint, role, *, body=None, timeout=None, attempts=3):
        if not endpoint.configured:
            raise ModelUnavailable('Настройте POLZA_API_KEY в .env; для другого API укажите его BASE_URL и API_KEY.')
        for attempt in range(attempts):
            started = time.monotonic()
            options = {'headers': {'Authorization': 'Bearer ' + endpoint.key}}
            if body is not None:
                options['json'] = body
            if timeout is not None:
                options['timeout'] = timeout
            try:
                response = self.client.request(method, endpoint.url + path, **options)
            except httpx.RequestError:
                # Retrying an ambiguous timeout can duplicate a paid generation.
                raise ModelUnavailable('API модели недоступен или превышено время ожидания; повторите задание позже.') from None
            call = {'role': role, 'provider': endpoint.provider, 'mode': 'api', 'model': endpoint.model,
                    'seconds': round(time.monotonic() - started, 3), 'status_code': response.status_code, 'attempt': attempt + 1}
            self.calls.append(call)
            if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < attempts:
                try:
                    delay = min(10, max(0, float(response.headers.get('Retry-After', 2 ** attempt))))
                except ValueError:
                    delay = 2 ** attempt
                time.sleep(delay)
                continue
            if response.is_error:
                message = {401: 'Проверьте API-ключ.', 403: 'Нет доступа к модели.',
                           402: 'Недостаточно средств на балансе API.', 404: 'Модель или endpoint не найдены.',
                           429: 'Превышен лимит запросов; повторите позже.'}.get(response.status_code, 'Проверьте настройки API или повторите позже.')
                # Do not expose provider bodies, headers or credentials to jobs/logs.
                raise ModelUnavailable(f'API модели: HTTP {response.status_code}. {message}')
            try:
                document = response.json()
                if not isinstance(document, dict):
                    raise ValueError
            except ValueError:
                raise ModelUnavailable('API модели вернул некорректный JSON.') from None
            usage = document.get('usage')
            if isinstance(usage, dict):
                call['usage'] = {k: v for k, v in usage.items() if k in {'prompt_tokens', 'completion_tokens', 'total_tokens', 'cost', 'cost_rub'} and isinstance(v, (int, float))}
            return document

    def _cache_path(self,kind: str,parts: list[bytes],suffix='json'):
        digest=hashlib.sha256(b'\0'.join(parts)).hexdigest()
        return artifact_path(f'cache/{kind}/{digest}.{suffix}')

    def structured(self, role: str, payload: dict, schema: type[BaseModel], images: list[Path] | None = None):
        if not self.endpoint.configured:
            raise ModelUnavailable('Настройте POLZA_API_KEY в .env. Модель не вызывалась.')
        prompt_name={'planning':'plan.txt','vision_audit':'audit.txt','regenerate_slide':'regenerate_slide.txt','image_selection':'image_selection.txt'}.get(role)
        if not prompt_name:raise ValueError(f'Unsupported structured role: {role}')
        prompt = (ROOT / "prompts" / prompt_name).read_text()
        output_schema = response_schema(role, payload, schema)
        max_tokens = int(os.environ.get('MODEL_MAX_TOKENS_' + role.upper(), '6500' if role == 'planning' else '3000'))
        inference = {'reasoning': {'enabled': self.reasoning_enabled}, 'max_tokens': max_tokens, 'temperature': 0.2}
        cache=self._cache_path('structured',[self.endpoint.url.encode(),role.encode(),self.model.encode(),self.response_format.encode(),json.dumps(inference,sort_keys=True).encode(),os.environ.get('MODEL_IMAGE_MAX_EDGE','896').encode(),prompt.encode(),json.dumps(payload,ensure_ascii=False,sort_keys=True).encode(),json.dumps(output_schema,sort_keys=True).encode(),*[Path(p).read_bytes() for p in images or []]])
        if self.cache_enabled and cache.exists():
            result=schema.model_validate_json(cache.read_bytes())
            self.calls.append({'role':role,'provider':'cache','mode':self.mode,'model':self.model,'seconds':0,'status_code':200,'attempt':0})
            return result
        # Keep a stable JSON protocol even when the upstream strict decoder does
        # not handle this nested schema. Validation remains mandatory locally.
        if self.response_format == 'json_schema':
            response_format = {'type': 'json_schema', 'json_schema': {'name': role, 'strict': True, 'schema': output_schema}}
        else:
            response_format = {'type': 'json_object'}
            prompt += '\nReturn JSON matching this schema: ' + json.dumps(output_schema, ensure_ascii=False)
        content = [{"type":"text", "text":json.dumps({"data":payload},ensure_ascii=False)}]
        for p in images or []:
            content.append({"type":"image_url", "image_url":{"url":encoded_image(p)}})
        messages = [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': content}]
        for attempt in range(2):
            document = self._request('POST', '/chat/completions', self.endpoint, role, body={
                'model': self.model, 'messages': messages, **inference,
                'response_format': response_format})
            try:
                choice = document['choices'][0]
                raw = choice['message']['content']
            except (KeyError, IndexError, TypeError):
                raise ModelUnavailable('API модели не вернул сообщение в choices[0].') from None
            if choice.get('finish_reason') == 'length':
                raise ModelUnavailable(f'Ответ модели обрезан: увеличьте MODEL_MAX_TOKENS_{role.upper()}.')
            if not isinstance(raw, str) or not raw.strip():
                raise ModelUnavailable('API модели вернул пустой ответ или отказ.')
            try:
                result = schema.model_validate_json(raw)
            except ValidationError as exc:
                issues = validation_issues(exc, output_schema)
                # Persist paths/types only, never the response, input values or
                # validator messages (custom validators can embed source text).
                diagnostic = [{'path': item['path'], 'type': item['type']} for item in issues]
                self.calls[-1]['validation_errors'] = diagnostic
                log.warning('Model output validation failed: role=%s attempt=%s errors=%s',
                            role, attempt + 1, json.dumps(diagnostic, ensure_ascii=False))
                if attempt == 0:
                    feedback = {'validation_errors': issues,
                                'instruction': 'Return the complete corrected JSON object, not a patch. '
                                'Fix the listed fields using the exact allowed values from the schema. '
                                'Preserve source facts and claim references; do not invent or drop content. '
                                'Do not include Markdown fences or explanations outside JSON.'}
                    messages.extend([{'role': 'assistant', 'content': raw},
                                     {'role': 'user', 'content': json.dumps(feedback, ensure_ascii=False)}])
                continue
            if self.cache_enabled:
                cache.parent.mkdir(parents=True,exist_ok=True);cache.write_text(result.model_dump_json(),encoding='utf-8')
            return result
        details = '; '.join(item['path'] + ': ' + item['type'] for item in issues[:3])
        raise ModelUnavailable(f'Модель дважды вернула JSON, не соответствующий схеме {role}: {details}. '
                               'Повторите генерацию; исходные материалы сохранены.')

    def image(self, prompt, output: Path):
        endpoint = self.image_endpoint
        if not endpoint.configured:
            raise ModelUnavailable('Настройте POLZA_API_KEY или отдельный T2I API.')
        size = f"{os.environ.get('T2I_WIDTH','1024')}x{os.environ.get('T2I_HEIGHT','576')}"
        aspect = os.environ.get('T2I_ASPECT_RATIO', '16:9')
        if endpoint.provider == 'polza' and endpoint.model == 'tongyi-mai/z-image':
            if len(prompt) > 1000:
                raise ValueError('Z-Image принимает промпт до 1000 символов; сократите инструкцию к иллюстрации.')
            if aspect not in {'1:1', '4:3', '3:4', '16:9', '9:16'}:
                raise ValueError('Unsupported T2I_ASPECT_RATIO for Z-Image')
        cache=self._cache_path('images',[endpoint.url.encode(),endpoint.model.encode(),size.encode(),aspect.encode(),prompt.encode()],'png')
        if self.cache_enabled and cache.exists():
            output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(cache.read_bytes())
            self.calls.append({'role':'text_to_image','provider':'cache','model':endpoint.model,'status_code':200});return
        timeout = float(os.environ.get('T2I_TIMEOUT_SECONDS', '180'))
        if endpoint.provider == 'polza':
            deadline = time.monotonic() + timeout
            document = self._request('POST', '/media', endpoint, 'text_to_image',
                body={'model': endpoint.model, 'input': {'prompt': prompt, 'aspect_ratio': aspect}, 'async': True},
                timeout=min(timeout, 30), attempts=1)
            media_id = document.get('id', '')
            if not isinstance(media_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', media_id):
                raise ModelUnavailable('Polza не вернул ID генерации изображения.')
            while document.get('status') in {'pending', 'processing'}:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ModelUnavailable('Превышено время ожидания генерации изображения в Polza.')
                time.sleep(min(3, remaining))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ModelUnavailable('Превышено время ожидания генерации изображения в Polza.')
                document = self._request('GET', '/media/' + media_id, endpoint, 'image_status', timeout=min(30, remaining), attempts=1)
            if document.get('status') != 'completed':
                raise ModelUnavailable('Polza не завершил генерацию изображения; проверьте историю в кабинете провайдера.')
            item = document.get('data')
        else:
            document = self._request('POST', '/images/generations', endpoint, 'text_to_image',
                body={'model': endpoint.model, 'prompt': prompt, 'size': size, 'response_format': 'b64_json'},
                timeout=timeout, attempts=1)
            item = document.get('data')
        if isinstance(item, list):
            item = item[0] if item else None
        if not isinstance(item, dict):
            raise ModelUnavailable('API не вернул данные изображения.')
        if item.get('b64_json'):
            raw = base64.b64decode(item['b64_json'], validate=True)
        elif endpoint.provider == 'polza' and item.get('url'):
            raw = self._download_polza_image(item['url'])
        else:
            raise ModelUnavailable('API не вернул изображение в ожидаемом формате.')
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            im.verify()
        # Keep the extension, MIME type and cache format consistent.
        with Image.open(io.BytesIO(raw)) as im:
            buffer = io.BytesIO(); im.convert('RGB').save(buffer, 'PNG'); raw = buffer.getvalue()
        output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(raw)
        if self.cache_enabled:
            cache.parent.mkdir(parents=True,exist_ok=True);cache.write_bytes(raw)

    def _download_polza_image(self, url):
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        if parsed.scheme != 'https' or not (host == 'polza.ai' or host.endswith('.polza.ai')) or parsed.username or parsed.password:
            raise ModelUnavailable('Неожиданный адрес изображения от Polza.')
        try:
            # CDN requests never carry the API Authorization header.
            with self.client.stream('GET', url, timeout=60, follow_redirects=False) as response:
                if response.status_code != 200:
                    raise ModelUnavailable('Не удалось скачать изображение из Polza.')
                chunks=[];size=0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 30 * 1024**2:
                        raise ModelUnavailable('Изображение превышает лимит 30 MiB.')
                    chunks.append(chunk)
                return b''.join(chunks)
        except httpx.RequestError:
            raise ModelUnavailable('Не удалось скачать изображение из Polza.') from None
