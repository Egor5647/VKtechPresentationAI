"""OpenAI-compatible gateway for approved open-weight models; no silent fallback."""
from __future__ import annotations
import base64
import json
import os
import time
from pathlib import Path
import httpx
from pydantic import BaseModel
from .settings import ROOT, config


class ModelUnavailable(RuntimeError):
    pass


def validate_manifest(manifest):
    for role, model in manifest.items():
        if role == "version": continue
        limit = 20_000_000_000 if role == "image" else 35_000_000_000
        if not model.get("open_weights") or model.get("license") not in {"Apache-2.0","MIT"} or not 0 < model.get("parameters",0) <= limit:
            raise ValueError(f"Ineligible model configuration: {role}")


class ModelGateway:
    def __init__(self, client=None):
        self.profile = os.environ.get("MODEL_PROFILE", "selection")
        if self.profile not in {"selection", "final"}: raise ValueError("Invalid MODEL_PROFILE")
        self.manifest = config("models.yaml"); validate_manifest(self.manifest)
        final = self.profile == "final"
        self.url = os.environ.get("VK_BASE_URL" if final else "MODEL_BASE_URL", "").rstrip("/")
        self.key = os.environ.get("VK_API_KEY" if final else "MODEL_API_KEY", "")
        self.model = os.environ.get("VK_MODEL_NAME" if final else "MODEL_NAME", "Qwen3.8-27B" if final else self.manifest["text"]["repository"])
        approved = {"Qwen3.8-27B", "Qwen/Qwen3.8-27B"}
        if self.model not in approved: raise ValueError("MODEL_NAME must identify the approved Qwen3.8-27B model")
        self.client = client or httpx.Client(timeout=httpx.Timeout(65, connect=10))
        self.calls = []

    def structured(self, role: str, payload: dict, schema: type[BaseModel], images: list[Path] | None = None):
        if not self.url:
            raise ModelUnavailable("Configure VK_BASE_URL for final or MODEL_BASE_URL for selection. No model was called.")
        prompt = (ROOT / "prompts" / ("plan.txt" if role == "planning" else "audit.txt")).read_text()
        content = [{"type":"text", "text":json.dumps({"data":payload,"output_schema":schema.model_json_schema()},ensure_ascii=False)}]
        for p in images or []:
            content.append({"type":"image_url", "image_url":{"url":"data:image/png;base64,"+base64.b64encode(p.read_bytes()).decode()}})
        headers = {"Authorization":"Bearer "+self.key} if self.key else {}
        error = None
        for attempt in range(2):
            started = time.monotonic()
            response = self.client.post(self.url+"/chat/completions",headers=headers,json={"model":self.model,"messages":[{"role":"system","content":prompt},{"role":"user","content":content}],"temperature":0.2,"max_tokens":10000,"response_format":{"type":"json_object"}})
            self.calls.append({"role":role,"provider":"vk" if self.profile=="final" else "configured_endpoint","model":self.model,"seconds":round(time.monotonic()-started,3),"status_code":response.status_code,"attempt":attempt+1})
            response.raise_for_status()
            raw=response.json()["choices"][0]["message"]["content"]
            try:
                return schema.model_validate_json(raw)
            except (ValueError,TypeError) as exc:
                error = exc
                content[0]["text"] += "\nPrevious response failed schema validation. Return a corrected JSON object."
        raise ValueError("Model returned invalid structured output") from error

    def image(self, prompt, output: Path):
        url=os.environ.get("T2I_BASE_URL","").rstrip("/")
        if not url: raise ModelUnavailable("T2I_BASE_URL is required for image generation")
        key=os.environ.get("T2I_API_KEY","")
        response=self.client.post(url+"/images/generations",headers={"Authorization":"Bearer "+key} if key else {},json={"model":self.manifest['image']['repository'],"prompt":prompt,"size":"1024x1024","response_format":"b64_json"})
        response.raise_for_status()
        item=response.json()["data"][0]
        if not item.get("b64_json"): raise ValueError("T2I endpoint must return base64 image data")
        raw=base64.b64decode(item["b64_json"],validate=True)
        from PIL import Image
        import io
        im=Image.open(io.BytesIO(raw)); im.verify()
        output.parent.mkdir(parents=True,exist_ok=True); output.write_bytes(raw)
        self.calls.append({"role":"text_to_image","model":self.manifest['image']['repository'],"status_code":response.status_code})
