"""Deterministic semantic graph layout for editable PowerPoint diagrams.

The module deliberately separates meaning (nodes and edges), visual rules and
geometry.  Exporters consume only the calculated boxes, so the final deck keeps
native PowerPoint objects instead of embedding a screenshot of Graphviz output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Direction = Literal['TB','BT','LR']


@dataclass(frozen=True)
class GraphNode:
    id: str
    label: str
    layer: int | None = None
    emphasis: bool = False
    shape: Literal['circle','rounded'] = 'circle'


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    emphasis: bool = False


@dataclass(frozen=True)
class GraphRules:
    direction: Direction = 'TB'
    margin_x: float = .08
    margin_y: float = .08
    node_width: float = .14
    node_height: float = .14
    sibling_gap: float = .055
    max_crossings: int = 0
    min_occupancy: float = .48


@dataclass(frozen=True)
class GraphSpec:
    nodes: tuple[GraphNode,...]
    edges: tuple[GraphEdge,...]
    rules: GraphRules
    layer_captions: tuple[str,...] = ()
    footer: str = ''


@dataclass(frozen=True)
class PlacedNode:
    id: str
    label: str
    x: float
    y: float
    w: float
    h: float
    emphasis: bool
    shape: str
    layer: int

    @property
    def center(self):return self.x+self.w/2,self.y+self.h/2


@dataclass(frozen=True)
class GraphLayout:
    nodes: tuple[PlacedNode,...]
    edges: tuple[GraphEdge,...]
    direction: Direction
    layer_captions: tuple[str,...]
    footer: str
    crossings: int
    occupancy: float


def _layers(spec: GraphSpec) -> dict[str,int]:
    explicit={node.id:node.layer for node in spec.nodes if node.layer is not None}
    if len(explicit)==len(spec.nodes):return {key:int(value) for key,value in explicit.items()}
    incoming={node.id:[] for node in spec.nodes}
    for edge in spec.edges:incoming[edge.target].append(edge.source)
    result={}
    pending={node.id for node in spec.nodes}
    while pending:
        progressed=False
        for node_id in list(pending):
            parents=incoming[node_id]
            if all(parent in result for parent in parents):
                result[node_id]=max((result[parent]+1 for parent in parents),default=0)
                pending.remove(node_id);progressed=True
        if not progressed:raise ValueError('Graph must be acyclic')
    return result


def _ordered_layers(spec: GraphSpec,layers: dict[str,int]) -> list[list[str]]:
    count=max(layers.values(),default=0)+1
    result=[[node.id for node in spec.nodes if layers[node.id]==layer] for layer in range(count)]
    incoming={node.id:[] for node in spec.nodes};outgoing={node.id:[] for node in spec.nodes}
    for edge in spec.edges:
        incoming[edge.target].append(edge.source);outgoing[edge.source].append(edge.target)
    # Alternating barycentric sweeps reduce crossings without introducing a
    # runtime dependency or non-determinism.
    for _ in range(4):
        positions={node_id:index for layer in result for index,node_id in enumerate(layer)}
        for layer in range(1,count):
            result[layer].sort(key=lambda node_id:(sum(positions[parent] for parent in incoming[node_id])/max(1,len(incoming[node_id])),positions[node_id],node_id))
        positions={node_id:index for layer in result for index,node_id in enumerate(layer)}
        for layer in range(count-2,-1,-1):
            result[layer].sort(key=lambda node_id:(sum(positions[child] for child in outgoing[node_id])/max(1,len(outgoing[node_id])),positions[node_id],node_id))
    return result


def _spread(count: int,start: float,end: float) -> list[float]:
    if count<=1:return [(start+end)/2]
    return [start+(end-start)*index/(count-1) for index in range(count)]


def _aligned_spread(count: int,maximum: int,start: float,end: float) -> list[float]:
    if count<=1:return [(start+end)/2]
    if maximum<=1:return _spread(count,start,end)
    step=(end-start)/(maximum-1);center=(start+end)/2
    first=center-step*(count-1)/2
    return [first+step*index for index in range(count)]


def _segments_cross(a,b,c,d):
    def side(p,q,r):return (q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0])
    return side(a,b,c)*side(a,b,d)<0 and side(c,d,a)*side(c,d,b)<0


def layout_graph(spec: GraphSpec) -> GraphLayout:
    node_by_id={node.id:node for node in spec.nodes}
    if len(node_by_id)!=len(spec.nodes):raise ValueError('Graph node IDs must be unique')
    if any(edge.source not in node_by_id or edge.target not in node_by_id for edge in spec.edges):raise ValueError('Graph edge references an unknown node')
    layers=_layers(spec);ordered=_ordered_layers(spec,layers);rules=spec.rules
    layer_count=len(ordered);placed=[]
    if rules.direction in {'TB','BT'}:
        usable_start=rules.margin_x+rules.node_width/2;usable_end=1-rules.margin_x-rules.node_width/2
        vertical=_spread(layer_count,rules.margin_y+rules.node_height/2,1-rules.margin_y-rules.node_height/2)
        if rules.direction=='BT':vertical=list(reversed(vertical))
        widest=max(map(len,ordered),default=1)
        for layer,node_ids in enumerate(ordered):
            horizontal=_aligned_spread(len(node_ids),widest,usable_start,usable_end)
            for center_x,node_id in zip(horizontal,node_ids):
                source=node_by_id[node_id];center_y=vertical[layer]
                placed.append(PlacedNode(node_id,source.label,center_x-rules.node_width/2,center_y-rules.node_height/2,rules.node_width,rules.node_height,source.emphasis,source.shape,layer))
    else:
        horizontal=_spread(layer_count,rules.margin_x+rules.node_width/2,1-rules.margin_x-rules.node_width/2)
        usable_start=rules.margin_y+rules.node_height/2;usable_end=1-rules.margin_y-rules.node_height/2
        widest=max(map(len,ordered),default=1)
        for layer,node_ids in enumerate(ordered):
            vertical=_aligned_spread(len(node_ids),widest,usable_start,usable_end)
            for center_y,node_id in zip(vertical,node_ids):
                source=node_by_id[node_id];center_x=horizontal[layer]
                placed.append(PlacedNode(node_id,source.label,center_x-rules.node_width/2,center_y-rules.node_height/2,rules.node_width,rules.node_height,source.emphasis,source.shape,layer))
    lookup={node.id:node for node in placed};crossings=0
    for index,left in enumerate(spec.edges):
        for right in spec.edges[index+1:]:
            if {left.source,left.target}&{right.source,right.target}:continue
            if _segments_cross(lookup[left.source].center,lookup[left.target].center,lookup[right.source].center,lookup[right.target].center):crossings+=1
    min_x=min(node.x for node in placed);max_x=max(node.x+node.w for node in placed)
    min_y=min(node.y for node in placed);max_y=max(node.y+node.h for node in placed)
    occupancy=(max_x-min_x)*(max_y-min_y)
    return GraphLayout(tuple(placed),spec.edges,rules.direction,spec.layer_captions,spec.footer,crossings,occupancy)


def semantic_graph(layout: str,items: list[str]) -> GraphSpec | None:
    """Return the semantic graph and its visual contract for a known grammar."""
    if layout=='fork_join':
        nodes=(GraphNode('fork','Fork',0,True,'rounded'),*(GraphNode(f'b{i}',str(i),1) for i in range(1,4)),GraphNode('join','Join',2,True,'rounded'))
        edges=tuple(GraphEdge('fork',f'b{i}') for i in range(1,4))+tuple(GraphEdge(f'b{i}','join') for i in range(1,4))
        return GraphSpec(nodes,edges,GraphRules(node_width=.22,node_height=.14),('Fork','Ветки','Join'),'Готовность после всех предшественников')
    if layout=='reduction_tree':
        nodes=tuple(GraphNode(f'a{i}',f'A{i}',0) for i in range(1,5))+(GraphNode('p1','+',1),GraphNode('p2','+',1),GraphNode('sum','Σ',2,True))
        edges=(GraphEdge('a1','p1'),GraphEdge('a2','p1'),GraphEdge('a3','p2'),GraphEdge('a4','p2'),GraphEdge('p1','sum'),GraphEdge('p2','sum'))
        return GraphSpec(nodes,edges,GraphRules(direction='BT',node_width=.14,node_height=.14),('Входы','Пары','Результат'),'Высота дерева — log n')
    if layout=='work_span':
        nodes=(GraphNode('start','',0,True),GraphNode('a','',1,True),GraphNode('b','',1),GraphNode('c','',2),GraphNode('d','',2,True),GraphNode('e','',3,True),GraphNode('f','',3))
        edges=(GraphEdge('start','a',True),GraphEdge('start','b'),GraphEdge('a','c'),GraphEdge('a','d',True),GraphEdge('b','d'),GraphEdge('c','e'),GraphEdge('d','e',True),GraphEdge('d','f'))
        footer=items[2] if len(items)>2 else 'T_P ≥ max(W/P, S)'
        return GraphSpec(nodes,edges,GraphRules(direction='LR',node_width=.11,node_height=.13),(),footer)
    if layout=='level_bound':
        nodes=(GraphNode('a','',0,True),GraphNode('b','',0,True),GraphNode('c','',1),GraphNode('d','',1),GraphNode('e','',1),GraphNode('f','',2),GraphNode('g','',2))
        edges=(GraphEdge('a','c',True),GraphEdge('a','d'),GraphEdge('b','d',True),GraphEdge('b','e'),GraphEdge('c','f',True),GraphEdge('d','f'),GraphEdge('d','g',True),GraphEdge('e','g'))
        footer=items[2] if len(items)>2 else 'T_level ≤ 2T*'
        return GraphSpec(nodes,edges,GraphRules(node_width=.11,node_height=.12),('Уровень 1','Уровень 2','Уровень 3'),footer)
    if layout=='critical_path':
        nodes=(GraphNode('s','',0,True),GraphNode('a','',1,True),GraphNode('b','',1),GraphNode('c','',2),GraphNode('d','',2,True),GraphNode('e','',3,True))
        edges=(GraphEdge('s','a',True),GraphEdge('s','b'),GraphEdge('a','d',True),GraphEdge('b','c'),GraphEdge('c','e'),GraphEdge('d','e',True))
        return GraphSpec(nodes,edges,GraphRules(direction='LR',node_width=.12,node_height=.14),(),'Выделен критический путь')
    return None
