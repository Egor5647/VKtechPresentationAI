"""OpenAI-compatible gateway for approved open-weight models; no silent fallback."""
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


def configured_text_model(manifest, mode: str):
    text = manifest["text"]
    modes = text.get("modes") or {"default": text}
    if mode not in modes:
        raise ValueError(f"Invalid MODEL_MODE: {mode}")
    return modes[mode]


def response_schema(role: str, payload: dict, schema: type[BaseModel]) -> dict:
    """Add request-specific constraints that Pydantic cannot express statically."""
    result = copy.deepcopy(schema.model_json_schema())
    if role == "planning":
        count = payload.get("slide_count")
        slides = result.get("properties", {}).get("slides", {})
        if isinstance(count, int) and count > 0 and slides.get("type") == "array":
            slides["minItems"] = count
            slides["maxItems"] = count
    return result


class ModelGateway:
    def __init__(self, client=None):
        self.profile = os.environ.get("MODEL_PROFILE", "selection")
        if self.profile not in {"selection", "final"}: raise ValueError("Invalid MODEL_PROFILE")
        self.manifest = config("models.yaml"); validate_manifest(self.manifest)
        text = self.manifest["text"]
        self.mode = os.environ.get("MODEL_MODE", text.get("default_mode", "default"))
        self.model_config = configured_text_model(self.manifest, self.mode)
        final = self.profile == "final"
        self.url = os.environ.get("VK_BASE_URL" if final else "MODEL_BASE_URL", "").rstrip("/")
        self.key = os.environ.get("VK_API_KEY" if final else "MODEL_API_KEY", "")
        self.model = os.environ.get("VK_MODEL_NAME" if final else "MODEL_NAME") or self.model_config["repository"]
        approved = {self.model_config["repository"], *self.model_config.get("aliases", [])}
        if self.model not in approved: raise ValueError("MODEL_NAME must identify a model approved in config/models.yaml")
        timeout = float(os.environ.get("MODEL_TIMEOUT_SECONDS", "300"))
        self.client = client or httpx.Client(timeout=httpx.Timeout(timeout, connect=10))
        self.cache_enabled=os.environ.get('MODEL_CACHE','1' if client is None else '0')=='1'
        self.calls = []

    def _cache_path(self,kind: str,parts: list[bytes],suffix='json'):
        digest=hashlib.sha256(b'\0'.join(parts)).hexdigest()
        return artifact_path(f'cache/{kind}/{digest}.{suffix}')

    def structured(self, role: str, payload: dict, schema: type[BaseModel], images: list[Path] | None = None):
        if not self.url:
            raise ModelUnavailable("Configure VK_BASE_URL for final or MODEL_BASE_URL for selection. No model was called.")
        prompt_name={'planning':'plan.txt','vision_audit':'audit.txt','regenerate_slide':'regenerate_slide.txt'}.get(role)
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
        headers = {"Authorization":"Bearer "+self.key} if self.key else {}
        error = None
        for attempt in range(2):
            started = time.monotonic()
            max_tokens = int(os.environ.get("MODEL_MAX_TOKENS_" + role.upper(), "3000" if role == "planning" else "1800"))
            response = self.client.post(self.url+"/chat/completions",headers=headers,json={"model":self.model,"messages":[{"role":"system","content":prompt},{"role":"user","content":content}],"temperature":0.2,"max_tokens":max_tokens,"response_format":{"type":"json_schema","json_schema":{"name":role,"strict":True,"schema":output_schema}}})
            self.calls.append({"role":role,"provider":"vk" if self.profile=="final" else "configured_endpoint","mode":self.mode,"model":self.model,"seconds":round(time.monotonic()-started,3),"status_code":response.status_code,"attempt":attempt+1})
            response.raise_for_status()
            raw=response.json()["choices"][0]["message"]["content"]
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
        url=os.environ.get("T2I_BASE_URL","").rstrip("/")
        if not url: raise ModelUnavailable("T2I_BASE_URL is required for image generation")
        key=os.environ.get("T2I_API_KEY","")
        size=f"{os.environ.get('T2I_WIDTH','1024')}x{os.environ.get('T2I_HEIGHT','1024')}"
        model=os.environ.get('T2I_MODEL') or self.manifest['image']['repository']
        cache=self._cache_path('images',[model.encode(),size.encode(),prompt.encode()],'png')
        if self.cache_enabled and cache.exists():
            output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(cache.read_bytes())
            self.calls.append({'role':'text_to_image','provider':'cache','model':model,'status_code':200});return
        response=self.client.post(url+"/images/generations",headers={"Authorization":"Bearer "+key} if key else {},json={"model":model,"prompt":prompt,"size":size,"response_format":"b64_json"},timeout=float(os.environ.get('T2I_TIMEOUT_SECONDS','1800')))
        response.raise_for_status()
        item=response.json()["data"][0]
        if not item.get("b64_json"): raise ValueError("T2I endpoint must return base64 image data")
        raw=base64.b64decode(item["b64_json"],validate=True)
        from PIL import Image
        import io
        im=Image.open(io.BytesIO(raw)); im.verify()
        output.parent.mkdir(parents=True,exist_ok=True); output.write_bytes(raw)
        if self.cache_enabled:
            cache.parent.mkdir(parents=True,exist_ok=True);cache.write_bytes(raw)
        self.calls.append({"role":"text_to_image","model":model,"status_code":response.status_code})
