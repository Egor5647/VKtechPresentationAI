from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: str = "1.0"


class Box(Contract):
    x: float
    y: float
    w: float = Field(ge=0)
    h: float = Field(ge=0)


class Style(Contract):
    font: str = "Arial"
    size: float = Field(default=18, gt=0)
    color: str = "202020"
    fill: str | None = None
    bold: bool = False
    align: str = "left"
    source: dict[str, str] = Field(default_factory=dict)


class Slot(Contract):
    id: str
    shape_id: int
    role: Literal["title", "body", "footer", "decor", "visual"]
    box: Box
    style: Style
    source_text: str = ""
    source_path: str = ""


class Prototype(Contract):
    id: str
    slide_part: str
    layout_part: str
    slots: list[Slot]
    background: str = "FFFFFF"


class DesignIR(Contract):
    id: str
    source_hash: str
    width: int
    height: int
    fonts: list[str]
    font_sizes: list[float]
    palette: list[str]
    prototypes: list[Prototype]
    warnings: list[str] = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)


class Claim(Contract):
    id: str
    text: str = Field(min_length=1)
    source: str
    required: bool = True


class Dataset(Contract):
    id: str
    title: str
    categories: list[str] = Field(min_length=1, max_length=50)
    series: dict[str, list[float]]
    unit: str = Field(min_length=1)
    source: str

    @model_validator(mode="after")
    def lengths(self):
        if not self.series or any(len(v) != len(self.categories) for v in self.series.values()):
            raise ValueError("Each series must match the category count")
        return self


class Asset(Contract):
    id: str
    path: str
    description: str
    source: str
    media_type: Literal["image/png", "image/jpeg", "image/webp"] = "image/png"
    claim_ids: list[str] = Field(default_factory=list)
    purpose: Literal["output", "reference"] = "output"


class ContentIR(Contract):
    id: str
    title: str
    language: str = "ru"
    claims: list[Claim]
    datasets: list[Dataset] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [x.id for x in self.claims + self.datasets + self.assets]
        if len(ids) != len(set(ids)):
            raise ValueError("Content IDs must be unique")
        return self


class PlanSlide(Contract):
    id: str
    title: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=240)
    claim_ids: list[str]
    dataset_id: str | None = None
    asset_id: str | None = None
    visual: Literal["none", "chart", "table", "sequence", "list", "hierarchy", "image"] = "none"
    role: Literal["cover", "content", "divider"] = "content"


class PresentationPlan(Contract):
    status: Literal["ready", "needs_input"] = "ready"
    reason: str = ""
    slides: list[PlanSlide]


class Node(Contract):
    id: str
    kind: Literal["text", "chart", "table", "smartart", "image"]
    role: str
    box: Box
    style: Style
    text: str = ""
    claim_ids: list[str] = Field(default_factory=list)
    data: dict = Field(default_factory=dict)
    binding: str | None = None


class SceneSlide(Contract):
    id: str
    title: str
    role: str
    prototype_id: str
    background: str
    nodes: list[Node]


class SceneIR(Contract):
    id: str
    version: int = 1
    variant: str
    template_id: str
    content_id: str
    width: int
    height: int
    slides: list[SceneSlide]


class Issue(Contract):
    id: str
    rule: str
    category: Literal["deterministic", "contextual", "environment"]
    status: Literal["pass", "fail", "not_applicable", "unknown"]
    severity: Literal["error", "warning", "info"] = "warning"
    slide_id: str | None = None
    element_ids: list[str] = Field(default_factory=list)
    message: str
    evidence: dict = Field(default_factory=dict)
    repair: Literal["clamp", "fit_text", "remove_placeholder", "none"] = "none"


class AuditReport(Contract):
    scene_id: str
    version: int
    issues: list[Issue]


class ContextualFinding(Contract):
    rule: Literal["C01", "C02", "C03", "C04", "C05", "C06", "C07", "C08", "C09", "C10", "C11"]
    status: Literal["pass", "fail", "not_applicable"]
    slide_id: str
    element_ids: list[str] = Field(default_factory=list)
    message: str = Field(min_length=20)
    evidence: str = Field(min_length=10)


class ContextualReport(Contract):
    issues: list[ContextualFinding]


class ContextualFailure(ContextualFinding):
    status: Literal["fail"] = "fail"


class ContextualFailureReport(Contract):
    issues: list[ContextualFailure] = Field(max_length=12)


class GenerateRequest(Contract):
    template_id: str
    content_id: str
    brief: str = Field(min_length=1, max_length=10000)
    purpose: str = "project"
    slide_count: int = Field(default=12, ge=1, le=50)
    generate_images: bool = False


class RepairRequest(Contract):
    variant: Literal["A", "B", "C"]
    expected_version: int = Field(ge=1)
    issue_ids: list[str] = Field(min_length=1)
    idempotency_key: str = Field(min_length=8, max_length=100)
