"""Semantic graph validation, metrics and deterministic slide geometry."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from .contracts import DiagramSpec

Direction = Literal['TB','BT','LR']


@dataclass(frozen=True)
class GraphNode:
    id: str
    label: str
    layer: int | None = None
    emphasis: bool = False
    shape: Literal['circle','rounded'] = 'circle'
    weight: float | None = None
    role: str = 'task'
    origin: str = 'source'


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    emphasis: bool = False
    label: str = ''


@dataclass(frozen=True)
class GraphRules:
    direction: Direction = 'TB'
    margin_x: float = .08
    margin_y: float = .08
    node_width: float = .14
    node_height: float = .18
    max_crossings: int = 0
    min_occupancy: float = .42


@dataclass(frozen=True)
class GraphSpec:
    kind: str
    nodes: tuple[GraphNode,...]
    edges: tuple[GraphEdge,...]
    rules: GraphRules
    layer_captions: tuple[str,...] = ()
    footer: str = ''
    educational_example: bool = False
    example_label: str = ''
    metrics: tuple[tuple[str,float],...] = ()


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
    weight: float | None = None
    role: str = 'task'
    origin: str = 'source'

    @property
    def center(self):return self.x+self.w/2,self.y+self.h/2


@dataclass(frozen=True)
class GraphLayout:
    kind: str
    nodes: tuple[PlacedNode,...]
    edges: tuple[GraphEdge,...]
    direction: Direction
    layer_captions: tuple[str,...]
    footer: str
    educational_example: bool
    example_label: str
    metrics: tuple[tuple[str,float],...]
    crossings: int
    occupancy: float


def _layers(spec: GraphSpec) -> dict[str,int]:
    explicit={node.id:node.layer for node in spec.nodes if node.layer is not None}
    if len(explicit)==len(spec.nodes):return {key:int(value) for key,value in explicit.items()}
    incoming={node.id:[] for node in spec.nodes}
    for edge in spec.edges:incoming[edge.target].append(edge.source)
    result={};pending={node.id for node in spec.nodes}
    while pending:
        progressed=False
        for node_id in sorted(pending):
            parents=incoming[node_id]
            if all(parent in result for parent in parents):
                result[node_id]=max((result[parent]+1 for parent in parents),default=0)
                pending.remove(node_id);progressed=True
        if not progressed:raise ValueError('Graph must be acyclic')
    return result


def _validate(spec: GraphSpec):
    ids=[node.id for node in spec.nodes]
    if len(ids)!=len(set(ids)):raise ValueError('Graph node IDs must be unique')
    known=set(ids)
    if any(edge.source not in known or edge.target not in known for edge in spec.edges):raise ValueError('Graph edge references an unknown node')
    indegree={node_id:0 for node_id in ids};outgoing={node_id:[] for node_id in ids}
    for edge in spec.edges:indegree[edge.target]+=1;outgoing[edge.source].append(edge.target)
    queue=[node_id for node_id,value in indegree.items() if value==0];visited=0
    while queue:
        node_id=queue.pop(0);visited+=1
        for target in outgoing[node_id]:
            indegree[target]-=1
            if indegree[target]==0:queue.append(target)
    if visited!=len(ids):raise ValueError('Graph must be acyclic')
    _layers(spec)


def _ordered_layers(spec: GraphSpec,layers: dict[str,int]) -> list[list[str]]:
    count=max(layers.values(),default=0)+1
    result=[[node.id for node in spec.nodes if layers[node.id]==layer] for layer in range(count)]
    incoming={node.id:[] for node in spec.nodes};outgoing={node.id:[] for node in spec.nodes}
    for edge in spec.edges:
        incoming[edge.target].append(edge.source);outgoing[edge.source].append(edge.target)
    for _ in range(6):
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


def _critical_path(nodes: tuple[GraphNode,...],edges: tuple[GraphEdge,...]):
    """Return Work, Span and every node/edge belonging to a longest path."""
    incoming={node.id:[] for node in nodes};outgoing={node.id:[] for node in nodes};indegree={node.id:0 for node in nodes}
    for edge in edges:
        incoming[edge.target].append(edge.source);outgoing[edge.source].append(edge.target);indegree[edge.target]+=1
    queue=sorted(node_id for node_id,value in indegree.items() if value==0);order=[]
    while queue:
        node_id=queue.pop(0);order.append(node_id)
        for target in outgoing[node_id]:
            indegree[target]-=1
            if indegree[target]==0:queue.append(target);queue.sort()
    if len(order)!=len(nodes):raise ValueError('Graph must be acyclic')
    weights={node.id:float(node.weight if node.weight is not None else 1) for node in nodes}
    distance={};best_parents={}
    for node_id in order:
        parents=incoming[node_id];best=max((distance[parent] for parent in parents),default=0)
        distance[node_id]=best+weights[node_id]
        best_parents[node_id]={parent for parent in parents if abs(distance[parent]-best)<1e-9}
    span=max(distance.values(),default=0);frontier=[node_id for node_id,value in distance.items() if abs(value-span)<1e-9]
    critical_nodes=set(frontier);critical_edges=set()
    while frontier:
        target=frontier.pop()
        for source in best_parents[target]:
            critical_edges.add((source,target))
            if source not in critical_nodes:critical_nodes.add(source);frontier.append(source)
    return sum(weights.values()),span,critical_nodes,critical_edges


def _emphasize_critical(spec: GraphSpec) -> GraphSpec:
    work,span,nodes,edges=_critical_path(spec.nodes,spec.edges)
    updated_nodes=tuple(GraphNode(n.id,n.label,n.layer,n.id in nodes,n.shape,n.weight,n.role,n.origin) for n in spec.nodes)
    updated_edges=tuple(GraphEdge(e.source,e.target,(e.source,e.target) in edges,e.label) for e in spec.edges)
    footer=spec.footer or (f'Span S = {span:g}' if spec.kind=='critical_path' else '')
    metrics=(('work',work),('span',span))
    return GraphSpec(spec.kind,updated_nodes,updated_edges,spec.rules,spec.layer_captions,footer,spec.educational_example,spec.example_label,metrics)


def layout_graph(spec: GraphSpec) -> GraphLayout:
    _validate(spec);node_by_id={node.id:node for node in spec.nodes}
    layers=_layers(spec);ordered=_ordered_layers(spec,layers);rules=spec.rules
    layer_count=len(ordered);placed=[];centers_by_id={}
    if rules.direction in {'TB','BT'}:
        usable_start=rules.margin_x+rules.node_width/2;usable_end=1-rules.margin_x-rules.node_width/2
        vertical=_spread(layer_count,rules.margin_y+rules.node_height/2,1-rules.margin_y-rules.node_height/2)
        if rules.direction=='BT':vertical=list(reversed(vertical))
        widest=max(map(len,ordered),default=1);incoming={node.id:[] for node in spec.nodes}
        for edge in spec.edges:incoming[edge.target].append(edge.source)
        for layer,node_ids in enumerate(ordered):
            if spec.kind=='reduction_tree' and layer>0:
                horizontal=[sum(centers_by_id[parent] for parent in incoming[node_id])/len(incoming[node_id]) for node_id in node_ids]
            else:horizontal=_aligned_spread(len(node_ids),widest,usable_start,usable_end)
            for center_x,node_id in zip(horizontal,node_ids):
                source=node_by_id[node_id];center_y=vertical[layer];centers_by_id[node_id]=center_x
                placed.append(PlacedNode(node_id,source.label,center_x-rules.node_width/2,center_y-rules.node_height/2,rules.node_width,rules.node_height,source.emphasis,source.shape,layer,source.weight,source.role,source.origin))
    else:
        horizontal=_spread(layer_count,rules.margin_x+rules.node_width/2,1-rules.margin_x-rules.node_width/2)
        usable_start=rules.margin_y+rules.node_height/2;usable_end=1-rules.margin_y-rules.node_height/2
        widest=max(map(len,ordered),default=1)
        for layer,node_ids in enumerate(ordered):
            vertical=_aligned_spread(len(node_ids),widest,usable_start,usable_end)
            for center_y,node_id in zip(vertical,node_ids):
                source=node_by_id[node_id];center_x=horizontal[layer]
                placed.append(PlacedNode(node_id,source.label,center_x-rules.node_width/2,center_y-rules.node_height/2,rules.node_width,rules.node_height,source.emphasis,source.shape,layer,source.weight,source.role,source.origin))
    lookup={node.id:node for node in placed};crossings=0
    for index,left in enumerate(spec.edges):
        for right in spec.edges[index+1:]:
            if {left.source,left.target}&{right.source,right.target}:continue
            if _segments_cross(lookup[left.source].center,lookup[left.target].center,lookup[right.source].center,lookup[right.target].center):crossings+=1
    min_x=min(node.x for node in placed);max_x=max(node.x+node.w for node in placed)
    min_y=min(node.y for node in placed);max_y=max(node.y+node.h for node in placed)
    occupancy=(max_x-min_x)*(max_y-min_y)
    return GraphLayout(spec.kind,tuple(placed),spec.edges,rules.direction,spec.layer_captions,spec.footer,spec.educational_example,spec.example_label,spec.metrics,crossings,occupancy)


def _rules(kind: str):
    return {
        'fork_join':GraphRules(margin_x=.03,margin_y=.04,node_width=.27,node_height=.19),
        'reduction_tree':GraphRules(direction='BT',margin_x=.03,margin_y=.04,node_width=.20,node_height=.18),
        'work_span':GraphRules(direction='LR',margin_x=.03,margin_y=.04,node_width=.22,node_height=.19),
        'critical_path':GraphRules(direction='LR',margin_x=.03,margin_y=.04,node_width=.22,node_height=.19),
        'level_schedule':GraphRules(margin_x=.03,margin_y=.04,node_width=.23,node_height=.17),
        'level_bound_proof':GraphRules(direction='LR',node_width=.29,node_height=.22,margin_x=.02,margin_y=.04,min_occupancy=.15),
    }[kind]


def _from_description(description: DiagramSpec) -> GraphSpec:
    kind=description.kind;nodes=[]
    for item in description.nodes:
        # A single vertex grammar prevents the accidental mix of stretched
        # ovals, circles and boxes that made older diagrams look improvised.
        shape='rounded'
        nodes.append(GraphNode(item.id,item.label,item.layer,False,shape,item.weight,item.role,item.origin))
    edges=tuple(GraphEdge(edge.source,edge.target,False,edge.label) for edge in description.edges);captions=()
    if kind=='level_schedule':
        count=max((node.layer or 0 for node in description.nodes),default=0)+1;captions=tuple(f'Уровень {index+1}' for index in range(count))
    path_edges=set(zip(description.highlighted_path,description.highlighted_path[1:]));path_nodes=set(description.highlighted_path)
    if path_nodes:
        nodes=[GraphNode(n.id,n.label,n.layer,n.id in path_nodes,n.shape,n.weight,n.role,n.origin) for n in nodes]
        edges=tuple(GraphEdge(edge.source,edge.target,(edge.source,edge.target) in path_edges,edge.label) for edge in edges)
    spec=GraphSpec(kind,tuple(nodes),edges,_rules(kind),captions,description.footer,description.educational_example,description.example_label)
    if kind in {'work_span','critical_path'}:
        computed=_emphasize_critical(spec)
        critical_edges={(edge.source,edge.target) for edge in computed.edges if edge.emphasis}
        if path_edges and not path_edges<=critical_edges:raise ValueError('Highlighted path is not a critical path')
        return computed
    return spec


def _short_concept(value: str,max_words=3) -> str:
    """Turn a source-backed visual item into a compact vertex label."""
    value=re.sub(r'^\s*(?:этап\s*\d+|fork|join|результат|ready[- ]?вершина)\s*:\s*','',value,flags=re.I)
    value=re.sub(r'\([^)]{18,}\)','',value).strip(' .:;—–-')
    replacements=(
        (r'создани\w*\s+независим\w*\s+ветв\w*','Независимая задача'),
        (r'слияни\w*\s+результат\w*','Сведение результата'),
        (r'выполнен\w*\s+после\s+предшественник\w*','Готовая задача'),
        (r'сложени\w*\s+пар\w*','Сложение пары'),
        (r'копировани\w*','Копирование'),
    )
    for pattern,label in replacements:
        if re.search(pattern,value,re.I):return label
    words=value.split()
    result=' '.join(words[:max_words])
    return result[:28].rstrip(' ,:;') or 'Задача'


def _semantic_tasks(items: list[str],fallback: tuple[str,...],count: int) -> list[str]:
    labels=[]
    for item in items:
        label=_short_concept(item)
        if label and label.casefold() not in {x.casefold() for x in labels}:labels.append(label)
    for label in fallback:
        if label.casefold() not in {x.casefold() for x in labels}:labels.append(label)
    return labels[:count]


def default_diagram(layout: str,items: list[str]) -> DiagramSpec | None:
    if layout=='level_bound':layout='level_schedule'
    if layout=='fork_join':
        branch_items=[item for item in items if not re.match(r'\s*(?:fork|join)\s*:',item,re.I)]
        labels=_semantic_tasks(branch_items,('Независимая задача','Параллельная работа','Готовая задача'),3)
        return DiagramSpec(kind=layout,nodes=[{'id':'fork','label':'Создать ветви','layer':0,'role':'state'},*({'id':f'b{i}','label':label,'layer':1} for i,label in enumerate(labels,1)),{'id':'join','label':'Свести результат','layer':2,'role':'state'}],edges=[*({'source':'fork','target':f'b{i}'} for i in range(1,4)),*({'source':f'b{i}','target':'join'} for i in range(1,4))],footer='Продолжение — после завершения всех ветвей')
    if layout=='reduction_tree':
        return DiagramSpec(kind=layout,nodes=[*({'id':f'a{i}','label':f'a{chr(8320+i)}','layer':0,'role':'input','origin':'derived'} for i in range(1,5)),{'id':'p1','label':'a₁ + a₂','layer':1,'role':'operation','origin':'derived'},{'id':'p2','label':'a₃ + a₄','layer':1,'role':'operation','origin':'derived'},{'id':'sum','label':'Σ aᵢ','layer':2,'role':'result','origin':'derived'}],edges=[{'source':'a1','target':'p1'},{'source':'a2','target':'p1'},{'source':'a3','target':'p2'},{'source':'a4','target':'p2'},{'source':'p1','target':'sum'},{'source':'p2','target':'sum'}],footer='На каждом уровне число активных значений уменьшается вдвое')
    if layout in {'work_span','critical_path'}:
        weights=(2,3,2,2,4,1,2);ids=('a','b','c','d','e','f','g');edges=(('a','b'),('a','c'),('b','d'),('b','e'),('c','e'),('d','f'),('e','f'),('e','g'))
        labels=('Старт','Разбор A','Разбор B','Расчёт A','Расчёт B','Сведение','Результат')
        return DiagramSpec(kind=layout,nodes=[{'id':node_id,'label':label,'weight':weight,'origin':'example'} for node_id,label,weight in zip(ids,labels,weights)],edges=[{'source':a,'target':b} for a,b in edges],educational_example=True,example_label='Учебный пример',footer='T_P ≥ max(W/P, S)' if layout=='work_span' else '',highlighted_path=['a','b','e','g'])
    if layout=='level_schedule':
        labels=('Чтение A','Чтение B','Вычисление A','Вычисление B','Вычисление C','Сведение A','Сведение B')
        return DiagramSpec(kind=layout,nodes=[{'id':node_id,'label':label,'layer':layer,'origin':'derived'} for node_id,label,layer in zip(('a','b','c','d','e','f','g'),labels,(0,0,1,1,1,2,2))],edges=[{'source':'a','target':'c'},{'source':'a','target':'d'},{'source':'b','target':'d'},{'source':'b','target':'e'},{'source':'c','target':'f'},{'source':'d','target':'f'},{'source':'d','target':'g'},{'source':'e','target':'g'}],footer=items[0] if items else 'Следующий уровень начинается после завершения предыдущего')
    if layout=='level_bound_proof':
        return DiagramSpec(kind=layout,nodes=[{'id':'actual','label':'T_level','layer':0,'role':'bound'},{'id':'decomposition','label':'W/P + S','layer':1,'role':'bound'},{'id':'optimum','label':'2T*','layer':2,'role':'bound'}],edges=[{'source':'actual','target':'decomposition','label':'≤'},{'source':'decomposition','target':'optimum','label':'≤'}],footer='Количество уровней не превышает длину критического пути')
    return None


def semantic_graph(layout: str,items: list[str],description: DiagramSpec | dict | None=None) -> GraphSpec | None:
    """Build a validated semantic graph, using legacy profiles as fallback."""
    if description is not None:
        if not isinstance(description,DiagramSpec):description=DiagramSpec.model_validate(description)
        return _from_description(description)
    fallback=default_diagram(layout,items)
    return _from_description(fallback) if fallback else None


def graph_quality(layout: str,items: list[str],description: DiagramSpec | dict | None=None) -> tuple[GraphLayout|None,list[str]]:
    """Return deterministic semantic and geometry failures for selection/audit."""
    try:spec=semantic_graph(layout,items,description)
    except (TypeError,ValueError) as error:return None,[str(error)]
    if spec is None:return None,[]
    result=layout_graph(spec);failures=[]
    if result.crossings>spec.rules.max_crossings:failures.append('edge_crossings')
    if result.occupancy<spec.rules.min_occupancy:failures.append('low_occupancy')
    if any(not node.label.strip() or len(node.label)>({'circle':8,'rounded':24}[node.shape]) for node in result.nodes):
        failures.append('unreadable_labels')
    if result.kind in {'work_span','critical_path'}:
        if any(node.weight is None for node in result.nodes):failures.append('missing_weights')
        if not result.metrics:failures.append('missing_metrics')
    if result.educational_example and not result.example_label.strip():failures.append('unlabeled_example')
    if result.kind=='reduction_tree':
        lookup={node.id:node for node in result.nodes};incoming={node.id:[] for node in result.nodes}
        for edge in result.edges:incoming[edge.target].append(edge.source)
        for node_id,parents in incoming.items():
            if len(parents)>1:
                expected=sum(lookup[parent].center[0] for parent in parents)/len(parents)
                if abs(lookup[node_id].center[0]-expected)>.001:failures.append('uncentered_parent');break
    return result,failures
