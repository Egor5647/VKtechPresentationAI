"""Inference gateway for Polza.ai with an explicit local compatibility mode."""
from __future__ import annotations
import base64
import copy
import hashlib
import io
import json
import os
import time
from pathlib import Path
import httpx
from pydantic import BaseModel
from .settings import ROOT, artifact_path, config


class ModelUnavailable(RuntimeError):
    pass


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
    candidates = []
    text = manifest.get("text", {})
    modes = text.get("modes")
    if modes:
        if text.get("default_mode") not in modes:
            raise ValueError("text.default_mode must identify a configured mode")
        candidates.extend((f"text.{name}", model, 35_000_000_000) for name, model in modes.items())
    elif text:
        candidates.append(("text", text, 35_000_000_000))
    if manifest.get("image"):
        candidates.append(("image", manifest["image"], 20_000_000_000))
    for role, model, limit in candidates:
        if not model.get("open_weights") or model.get("license") not in {"Apache-2.0","MIT"} or not 0 < model.get("parameters",0) <= limit:
            raise ValueError(f"Ineligible model configuration: {role}")
        if model.get('api_model') and not 0 < model.get('api_parameters',model['parameters']) <= limit:
            raise ValueError(f"Ineligible API model configuration: {role}")


def configured_text_model(manifest, mode: str):
    text = manifest["text"]
    modes = text.get("modes") or {"default": text}
    if mode not in modes:
        raise ValueError(f"Invalid MODEL_MODE: {mode}")
    return modes[mode]


def inference_status() -> dict:
    """Return public configuration state without exposing any credential."""
    manifest=config('models.yaml');provider=os.environ.get('AI_PROVIDER','polza').lower()
    mode=os.environ.get('MODEL_MODE',manifest['text']['default_mode']);selected=configured_text_model(manifest,mode)
    if provider=='polza':
        model=os.environ.get('POLZA_TEXT_MODEL_'+mode.upper()) or os.environ.get('POLZA_TEXT_MODEL') or selected['api_model']
        configured=bool(os.environ.get('POLZA_API_KEY'))
        image_model=os.environ.get('POLZA_IMAGE_MODEL') or manifest['image']['api_model']
        image_configured=configured
    elif provider=='local':
        final=os.environ.get('MODEL_PROFILE')=='final'
        model=os.environ.get('VK_MODEL_NAME' if final else 'MODEL_NAME') or selected['repository']
        configured=bool(os.environ.get('VK_BASE_URL' if final else 'MODEL_BASE_URL'))
        image_model=os.environ.get('T2I_MODEL') or manifest['image']['repository']
        image_configured=bool(os.environ.get('T2I_BASE_URL'))
    else:raise ValueError('AI_PROVIDER must be polza or local')
    return {'provider':provider,'mode':mode,'model':model,'model_configured':configured,'image_model':image_model,'image_model_configured':image_configured}


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
        self.provider = os.environ.get("AI_PROVIDER", "polza").lower()
        if self.provider not in {"polza", "local"}:
            raise ValueError("AI_PROVIDER must be polza or local")
        self.profile = os.environ.get("MODEL_PROFILE", "selection")
        if self.profile not in {"selection", "final"}: raise ValueError("Invalid MODEL_PROFILE")
        self.manifest = config("models.yaml"); validate_manifest(self.manifest)
        text = self.manifest["text"]
        self.mode = os.environ.get("MODEL_MODE", text.get("default_mode", "default"))
        self.model_config = configured_text_model(self.manifest, self.mode)
        if self.provider == "polza":
            self.url = os.environ.get("POLZA_BASE_URL", "https://polza.ai/api/v1").rstrip("/")
            self.key = os.environ.get("POLZA_API_KEY", "")
            mode_key = "POLZA_TEXT_MODEL_" + self.mode.upper()
            self.model = os.environ.get(mode_key) or os.environ.get("POLZA_TEXT_MODEL") or self.model_config["api_model"]
        else:
            final = self.profile == "final"
            self.url = os.environ.get("VK_BASE_URL" if final else "MODEL_BASE_URL", "").rstrip("/")
            self.key = os.environ.get("VK_API_KEY" if final else "MODEL_API_KEY", "")
            self.model = os.environ.get("VK_MODEL_NAME" if final else "MODEL_NAME") or self.model_config["repository"]
        approved = {self.model_config["repository"], self.model_config.get("api_model"), *self.model_config.get("aliases", [])}
        if self.model not in approved: raise ValueError("Configured text model is not approved in config/models.yaml")
        timeout = float(os.environ.get("MODEL_TIMEOUT_SECONDS", "300"))
        self.client = client or httpx.Client(timeout=httpx.Timeout(timeout, connect=10))
        self.cache_enabled=os.environ.get('MODEL_CACHE','1' if client is None else '0')=='1'
        self.calls = []

    def _cache_path(self,kind: str,parts: list[bytes],suffix='json'):
        digest=hashlib.sha256(b'\0'.join(parts)).hexdigest()
        return artifact_path(f'cache/{kind}/{digest}.{suffix}')

    def _headers(self):
        if not self.key:
            name = "POLZA_API_KEY" if self.provider == "polza" else "model API key"
            raise ModelUnavailable(f"{name} is not configured")
        return {"Authorization":"Bearer "+self.key,"Content-Type":"application/json"}

    @staticmethod
    def _request_error(response: httpx.Response, purpose: str):
        messages={401:'API key was rejected',402:'Insufficient API balance',403:'API access is forbidden',429:'API rate limit was exceeded'}
        detail=messages.get(response.status_code,f'HTTP {response.status_code}')
        return ModelUnavailable(f'{purpose} failed: {detail}')

    def _post(self,url: str,payload: dict,purpose: str,timeout=None):
        attempts=max(1,int(os.environ.get('POLZA_RETRY_ATTEMPTS','3')))
        delay=float(os.environ.get('POLZA_RETRY_DELAY_SECONDS','.5'))
        last=None
        for attempt in range(attempts):
            try:
                kwargs={'headers':self._headers(),'json':payload}
                if timeout is not None:kwargs['timeout']=timeout
                response=self.client.post(url,**kwargs)
            except httpx.HTTPError as exc:
                last=exc
                if attempt+1==attempts:break
                time.sleep(delay*(2**attempt));continue
            if response.status_code not in {429,500,502,503,504}:
                return response
            last=self._request_error(response,purpose)
            if attempt+1<attempts:time.sleep(delay*(2**attempt))
        if isinstance(last,ModelUnavailable):raise last
        raise ModelUnavailable(f'{purpose} failed: network error') from last

    @staticmethod
    def _message_text(message: dict) -> str:
        raw=message.get('content','')
        if isinstance(raw,list):
            raw=''.join(str(item.get('text') or item.get('content') or '') for item in raw if isinstance(item,dict))
        if not isinstance(raw,str):raw=json.dumps(raw,ensure_ascii=False)
        raw=raw.strip()
        if raw.startswith('```'):
            raw=raw.split('\n',1)[1] if '\n' in raw else raw[3:]
            raw=raw.rsplit('```',1)[0].strip()
        if not raw.startswith('{') and '{' in raw and '}' in raw:
            raw=raw[raw.find('{'):raw.rfind('}')+1]
        return raw

    def structured(self, role: str, payload: dict, schema: type[BaseModel], images: list[Path] | None = None):
        if not self.url:
            raise ModelUnavailable("Text model endpoint is not configured")
        prompt_name={'planning':'plan.txt','vision_audit':'audit.txt','regenerate_slide':'regenerate_slide.txt','image_selection':'image_selection.txt'}.get(role)
        if not prompt_name:raise ValueError(f'Unsupported structured role: {role}')
        prompt = (ROOT / "prompts" / prompt_name).read_text()
        output_schema = response_schema(role, payload, schema)
        cache=self._cache_path('structured',[role.encode(),self.model.encode(),prompt.encode(),json.dumps(payload,ensure_ascii=False,sort_keys=True).encode(),json.dumps(output_schema,sort_keys=True).encode(),*[Path(p).read_bytes() for p in images or []]])
        if self.cache_enabled and cache.exists():
            result=schema.model_validate_json(cache.read_bytes())
            self.calls.append({'role':role,'provider':'cache','mode':self.mode,'model':self.model,'seconds':0,'status_code':200,'attempt':0})
            return result
        # The schema is already supplied through response_format. Repeating it in
        # the user message wastes thousands of local-model prefill tokens.
        content = [{"type":"text", "text":json.dumps({"data":payload},ensure_ascii=False)}]
        for p in images or []:
            content.append({"type":"image_url", "image_url":{"url":encoded_image(p)}})
        error = None
        schema_format={"type":"json_schema","json_schema":{"name":role,"strict":True,"schema":output_schema}}
        for attempt in range(2):
            started = time.monotonic()
            max_tokens = int(os.environ.get("MODEL_MAX_TOKENS_" + role.upper(), "3000" if role == "planning" else "1800"))
            request={"model":self.model,"messages":[{"role":"system","content":prompt},{"role":"user","content":content}],"temperature":0.2,"max_tokens":max_tokens,"response_format":schema_format}
            response=self._post(self.url+"/chat/completions",request,'Text generation')
            format_name='json_schema'
            if response.status_code in {400,422} and self.provider=='polza':
                request['response_format']={"type":"json_object"}
                content[0]["text"] += "\nReturn only one JSON object matching the requested fields."
                response=self._post(self.url+"/chat/completions",request,'Text generation')
                format_name='json_object'
            self.calls.append({"role":role,"provider":self.provider,"mode":self.mode,"model":self.model,"seconds":round(time.monotonic()-started,3),"status_code":response.status_code,"attempt":attempt+1,"response_format":format_name})
            if response.status_code>=400:raise self._request_error(response,'Text generation')
            try:raw=self._message_text(response.json()["choices"][0]["message"])
            except (KeyError,IndexError,TypeError,ValueError) as exc:raise ModelUnavailable('Text generation returned an invalid response envelope') from exc
            try:
                result=schema.model_validate_json(raw)
                if self.cache_enabled:
                    cache.parent.mkdir(parents=True,exist_ok=True);cache.write_text(result.model_dump_json(),encoding='utf-8')
                return result
            except (ValueError,TypeError) as exc:
                error = exc
                content[0]["text"] += "\nPrevious response failed schema validation. Return a corrected JSON object."
        raise ValueError("Model returned invalid structured output") from error

    def image(self, prompt, output: Path):
        if self.provider=='polza':
            url=os.environ.get('POLZA_IMAGE_BASE_URL','https://polza.ai/api/v2').rstrip('/')
            model=os.environ.get('POLZA_IMAGE_MODEL') or self.manifest['image']['api_model']
        else:
            url=os.environ.get("T2I_BASE_URL","").rstrip("/")
            if not url: raise ModelUnavailable("T2I_BASE_URL is required for image generation")
            self.key=os.environ.get("T2I_API_KEY","")
            model=os.environ.get('T2I_MODEL') or self.manifest['image']['repository']
        size=f"{os.environ.get('T2I_WIDTH','1344')}x{os.environ.get('T2I_HEIGHT','768')}"
        cache=self._cache_path('images',[model.encode(),size.encode(),prompt.encode()],'png')
        if self.cache_enabled and cache.exists():
            output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(cache.read_bytes())
            self.calls.append({'role':'text_to_image','provider':'cache','model':model,'status_code':200});return
        started=time.monotonic()
        response=self._post(url+"/images/generations",{"model":model,"prompt":prompt,"n":1,"size":size,"response_format":"b64_json"},'Image generation',timeout=float(os.environ.get('T2I_TIMEOUT_SECONDS','600')))
        if response.status_code>=400:raise self._request_error(response,'Image generation')
        payload=response.json();item=self._image_item(payload)
        if item is None and payload.get('id'):
            item=self._poll_image(str(payload['id']))
        if item is None:raise ModelUnavailable('Image generation returned neither image data nor a task id')
        if item.get('b64_json'):
            try:raw=base64.b64decode(item['b64_json'],validate=True)
            except (ValueError,TypeError) as exc:raise ModelUnavailable('Image generation returned invalid base64 data') from exc
        elif item.get('url'):
            download=self.client.get(item['url'],headers={"Authorization":"Bearer "+self.key},timeout=float(os.environ.get('T2I_TIMEOUT_SECONDS','600')),follow_redirects=True)
            if download.status_code>=400:raise self._request_error(download,'Image download')
            raw=download.content
        else:raise ModelUnavailable('Image generation completed without image content')
        from PIL import Image
        im=Image.open(io.BytesIO(raw)); im.verify()
        output.parent.mkdir(parents=True,exist_ok=True); output.write_bytes(raw)
        if self.cache_enabled:
            cache.parent.mkdir(parents=True,exist_ok=True);cache.write_bytes(raw)
        self.calls.append({"role":"text_to_image","provider":self.provider,"model":model,"seconds":round(time.monotonic()-started,3),"status_code":response.status_code})

    @classmethod
    def _image_item(cls,payload):
        if not isinstance(payload,dict):return None
        data=payload.get('data')
        if isinstance(data,list) and data and isinstance(data[0],dict):return data[0]
        if payload.get('b64_json') or payload.get('url'):return payload
        for key in ('result','output','response'):
            nested=payload.get(key)
            if isinstance(nested,list) and nested and isinstance(nested[0],dict):return nested[0]
            item=cls._image_item(nested)
            if item:return item
        return None

    def _poll_image(self,task_id: str):
        timeout=float(os.environ.get('T2I_POLL_TIMEOUT_SECONDS','900'));interval=float(os.environ.get('T2I_POLL_INTERVAL_SECONDS','3'))
        endpoint=os.environ.get('POLZA_MEDIA_BASE_URL',self.url).rstrip('/')+'/media/'+task_id
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            try:response=self.client.get(endpoint,headers=self._headers(),timeout=30)
            except httpx.HTTPError:
                time.sleep(interval);continue
            if response.status_code>=400:raise self._request_error(response,'Image status check')
            payload=response.json();item=self._image_item(payload)
            if item:return item
            if str(payload.get('status','')).lower() in {'failed','error','cancelled'}:
                raise ModelUnavailable('Image generation task failed')
            time.sleep(interval)
        raise ModelUnavailable('Image generation did not finish before the timeout')
