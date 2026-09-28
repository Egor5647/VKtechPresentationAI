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


class DiagramNodeSpec(Contract):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    label: str = Field(default="", max_length=40)
    layer: int | None = Field(default=None, ge=0, le=12)
    weight: float | None = Field(default=None, ge=0)
    role: Literal["task", "input", "operation", "result", "state", "bound"] = "task"
    origin: Literal["source", "derived", "example"] = "source"
    claim_ids: list[str] = Field(default_factory=list, max_length=6)


class DiagramEdgeSpec(Contract):
    source: str = Field(min_length=1, max_length=40)
    target: str = Field(min_length=1, max_length=40)
    label: str = Field(default="", max_length=32)


class DiagramSpec(Contract):
    kind: Literal[
        "fork_join", "reduction_tree", "work_span", "level_schedule", "level_bound_proof", "critical_path",
        "sequence", "comparison", "hierarchy", "layers", "memory_access", "scheduler", "formula_focus",
        "abstraction", "pram_reality"
    ]
    nodes: list[DiagramNodeSpec] = Field(min_length=2, max_length=16)
    edges: list[DiagramEdgeSpec] = Field(default_factory=list, max_length=32)
    educational_example: bool = False
    example_label: str = Field(default="", max_length=60)
    footer: str = Field(default="", max_length=120)
    highlighted_path: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def valid_graph(self):
        ids=[node.id for node in self.nodes]
        if len(ids)!=len(set(ids)):raise ValueError("Diagram node IDs must be unique")
        known=set(ids)
        if any(edge.source not in known or edge.target not in known for edge in self.edges):
            raise ValueError("Diagram edge references an unknown node")
        if any(node.origin=="example" for node in self.nodes) and not self.educational_example:
            raise ValueError("Example values require educational_example=true")
        if any(node_id not in known for node_id in self.highlighted_path):
            raise ValueError("Highlighted path references an unknown node")
        edge_pairs={(edge.source,edge.target) for edge in self.edges}
        if any(pair not in edge_pairs for pair in zip(self.highlighted_path,self.highlighted_path[1:])):
            raise ValueError("Highlighted path must follow graph edges")
        incoming={node_id:0 for node_id in ids};outgoing={node_id:[] for node_id in ids}
        for edge in self.edges:
            incoming[edge.target]+=1;outgoing[edge.source].append(edge.target)
        queue=[node_id for node_id,value in incoming.items() if value==0];visited=0
        while queue:
            node_id=queue.pop(0);visited+=1
            for target in outgoing[node_id]:
                incoming[target]-=1
                if incoming[target]==0:queue.append(target)
        if visited!=len(ids):raise ValueError("Diagram graph must be acyclic")
        return self


class VisualContract(Contract):
    goal: str = Field(default="", max_length=300)
    entities: list[str] = Field(default_factory=list, max_length=6)
    relations: list[str] = Field(default_factory=list, max_length=6)
    forbidden: list[str] = Field(default_factory=list, max_length=6)
    diagram: DiagramSpec | None = None


class SlideLogicContract(Contract):
    """Meaning that must survive layout, density and renderer changes."""
    teaching_goal: str = Field(default="", max_length=300)
    question_answered: str = Field(default="", max_length=240)
    introduced_concepts: list[str] = Field(default_factory=list, max_length=12)
    required_prior_concepts: list[str] = Field(default_factory=list, max_length=12)
    source_claim_ids: list[str] = Field(default_factory=list, max_length=32)
    main_assertion: str = Field(default="", max_length=360)
    supporting_assertions: list[str] = Field(default_factory=list, max_length=6)
    transition_from_previous: str = Field(default="", max_length=240)
    transition_to_next: str = Field(default="", max_length=240)
    visual_assertion: str = Field(default="", max_length=300)
    required_visual_entities: list[str] = Field(default_factory=list, max_length=12)
    required_visual_relations: list[str] = Field(default_factory=list, max_length=12)
    semantic_payload_hash: str = Field(default="", max_length=64)


class LogicFinding(Contract):
    rule: Literal[
        "L01", "L02", "L03", "L04", "L05", "L06", "L07", "L08", "L09", "L10",
        "L11", "L12"
    ]
    severity: Literal["error", "warning", "info"] = "warning"
    slide_id: str | None = None
    message: str
    repair_level: Literal["local", "slide", "section", "deck"] = "slide"
    evidence: dict = Field(default_factory=dict)


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
    # The API schema advertises much tighter authoring limits.  These wider
    # transport limits let us accept a provider response that ignored those
    # hints and then reduce it through fit_semantic_variants, which composes
    # whole sentences instead of clipping characters.
    message: str = Field(min_length=1, max_length=1000)
    claim_ids: list[str]
    dataset_id: str | None = None
    asset_id: str | None = None
    visual: Literal["none", "chart", "table", "sequence", "list", "hierarchy", "image"] = "none"
    role: Literal["cover", "content", "divider"] = "content"
    archetype: Literal["cover", "divider", "explanation", "comparison", "process", "example", "formula", "exercise", "summary", "illustration"] = "explanation"
    support_points: list[str] = Field(default_factory=list, max_length=3)
    takeaway: str = Field(default="", max_length=400)
    balanced_message: str = Field(default="", max_length=600)
    visual_items: list[str] = Field(default_factory=list, max_length=3)
    visual_brief: str = Field(default="", max_length=300)
    visual_strategy: Literal["none", "generated_image", "source_image", "diagram", "chart", "table"] = "none"
    visual_score: float = Field(default=0, ge=0, le=1)
    visual_reason: str = Field(default="", max_length=240)
    visual_contract: VisualContract = Field(default_factory=VisualContract)
    logic_contract: SlideLogicContract = Field(default_factory=SlideLogicContract)


class PresentationPlan(Contract):
    status: Literal["ready", "needs_input"] = "ready"
    reason: str = ""
    slides: list[PlanSlide]


class Node(Contract):
    id: str
    kind: Literal["text", "chart", "table", "diagram", "smartart", "image"]
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


class PaletteSpec(Contract):
    background: str = Field(default="FFFFFF", pattern=r"^[0-9A-Fa-f]{6}$")
    surface: str = Field(default="E8EEF6", pattern=r"^[0-9A-Fa-f]{6}$")
    accent: str = Field(default="0077FF", pattern=r"^[0-9A-Fa-f]{6}$")
    accent_secondary: str = Field(default="31C48D", pattern=r"^[0-9A-Fa-f]{6}$")
    text_primary: str = Field(default="172438", pattern=r"^[0-9A-Fa-f]{6}$")
    text_on_accent: str = Field(default="FFFFFF", pattern=r"^[0-9A-Fa-f]{6}$")

    @model_validator(mode="after")
    def uppercase(self):
        for name in ("background", "surface", "accent", "accent_secondary", "text_primary", "text_on_accent"):
            setattr(self,name,getattr(self,name).upper())
        return self


class PaletteRequest(Contract):
    palette: PaletteSpec
    selection: dict[str, Literal["A", "B", "C"]] | None = Field(default=None, min_length=1, max_length=50)


class RepairRequest(Contract):
    variant: Literal["A", "B", "C"]
    expected_version: int = Field(ge=1)
    issue_ids: list[str] = Field(min_length=1)
    idempotency_key: str = Field(min_length=8, max_length=100)


class SelectionRequest(Contract):
    slide_id: str = Field(min_length=1, max_length=100)
    variant: Literal["A", "B", "C"]


class ExportRequest(Contract):
    selection: dict[str, Literal["A", "B", "C"]] = Field(min_length=1, max_length=50)


class RegenerateSlideRequest(Contract):
    slide_id: str = Field(min_length=1, max_length=100)
    instruction: str = Field(default="Сделай слайд выразительнее и плотнее, сохрани все факты.", min_length=1, max_length=500)
    selection: dict[str, Literal["A", "B", "C"]] | None = Field(default=None, min_length=1, max_length=50)


class VisualUpdateRequest(Contract):
    slide_id: str = Field(min_length=1, max_length=100)
    mode: Literal["auto", "none", "diagram", "image"] = "auto"
    instruction: str = Field(default="", max_length=500)
    candidate_asset_id: str | None = Field(default=None, max_length=160)
    selection: dict[str, Literal["A", "B", "C"]] | None = Field(default=None, min_length=1, max_length=50)


class ImageCandidateScore(Contract):
    index: int = Field(ge=0, le=7)
    score: float = Field(ge=0, le=100)
    semantic_fit: float = Field(ge=0, le=100)
    naturalness: float = Field(ge=0, le=100)
    composition: float = Field(ge=0, le=100)
    accepted: bool
    reason: str = Field(min_length=10, max_length=300)


class ImageSelection(Contract):
    candidates: list[ImageCandidateScore] = Field(min_length=1, max_length=8)
    selected_index: int = Field(ge=0, le=7)
    fallback_to_diagram: bool = False
    reason: str = Field(min_length=10, max_length=300)

    @model_validator(mode="after")
    def selected_exists(self):
        indices=[candidate.index for candidate in self.candidates]
        if len(indices)!=len(set(indices)):
            raise ValueError("candidate indices must be unique")
        if self.selected_index not in set(indices):
            raise ValueError("selected_index must identify a scored candidate")
        return self


class SlideRevision(Contract):
    title: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1000)
    support_points: list[str] = Field(default_factory=list, max_length=3)
    takeaway: str = Field(default="", max_length=400)
    balanced_message: str = Field(default="", max_length=600)
    visual_items: list[str] = Field(default_factory=list, max_length=3)
    visual: Literal["none", "sequence", "list", "hierarchy"] = "none"
    archetype: Literal["divider", "explanation", "comparison", "process", "example", "formula", "exercise", "summary", "illustration"] = "explanation"
    visual_brief: str = Field(default="", max_length=300)
