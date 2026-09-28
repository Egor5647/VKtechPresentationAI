import math

import pytest
from pydantic import ValidationError

from vktech.contracts import DiagramSpec
from vktech.export import _graph_boundary_point, _graph_node_bounds, _math_display
from vktech.graph_layout import PlacedNode, default_diagram, graph_quality, layout_graph, semantic_graph


GRAPH_TYPES=(
    'fork_join','reduction_tree','work_span','level_schedule',
    'level_bound_proof','critical_path','sequence','comparison','hierarchy',
    'layers','memory_access','scheduler','formula_focus','abstraction','pram_reality',
)


@pytest.mark.parametrize('name',GRAPH_TYPES)
def test_semantic_graph_rules_produce_clean_layout(name):
    generic={'sequence','comparison','hierarchy','layers','memory_access','scheduler','formula_focus','abstraction','pram_reality'}
    description=default_diagram(name,['Work','Span','T ≤ 2T*']) if name in generic else None
    result,failures=graph_quality(name,['Work','Span','T ≤ 2T*'],description)
    assert not failures
    assert result.crossings==0
    for node in result.nodes:
        assert 0<=node.x<node.x+node.w<=1
        assert 0<=node.y<node.y+node.h<=1
    for index,left in enumerate(result.nodes):
        for right in result.nodes[index+1:]:
            assert left.x+left.w<=right.x or right.x+right.w<=left.x or left.y+left.h<=right.y or right.y+right.h<=left.y


def test_graph_layout_is_deterministic_and_computes_work_span():
    spec=semantic_graph('work_span',['Work','Span','T_P ≥ max(W/P, S)'])
    first=layout_graph(spec);second=layout_graph(spec)
    assert first==second
    assert dict(first.metrics)=={'work':16.0,'span':11.0}
    assert {node.id for node in first.nodes if node.emphasis}=={'a','b','e','g'}
    assert {(edge.source,edge.target) for edge in first.edges if edge.emphasis}=={('a','b'),('b','e'),('e','g')}
    assert first.educational_example and first.example_label=='Учебный пример'


def test_equal_critical_paths_are_all_highlighted():
    diagram=DiagramSpec(
        kind='critical_path',
        nodes=[
            {'id':'a','label':'A','weight':2,'origin':'example'},
            {'id':'b','label':'B','weight':2,'origin':'example'},
            {'id':'end','label':'Итог','weight':1,'origin':'example'},
        ],
        edges=[{'source':'a','target':'end'},{'source':'b','target':'end'}],
        educational_example=True,example_label='Учебный пример',
    )
    result=layout_graph(semantic_graph('critical_path',[],diagram))
    assert dict(result.metrics)=={'work':5.0,'span':3.0}
    assert {node.id for node in result.nodes if node.emphasis}=={'a','b','end'}
    assert sum(edge.emphasis for edge in result.edges)==2


@pytest.mark.parametrize('payload,message',[
    ({'kind':'fork_join','nodes':[{'id':'a'},{'id':'a'}]},'unique'),
    ({'kind':'fork_join','nodes':[{'id':'a'},{'id':'b'}],'edges':[{'source':'a','target':'missing'}]},'unknown'),
    ({'kind':'fork_join','nodes':[{'id':'a'},{'id':'b'}],'edges':[{'source':'a','target':'b'},{'source':'b','target':'a'}]},'acyclic'),
    ({'kind':'work_span','nodes':[{'id':'a','weight':2,'origin':'example'},{'id':'b'}]},'educational_example'),
])
def test_invalid_semantic_graphs_are_rejected(payload,message):
    with pytest.raises(ValidationError,match=message):DiagramSpec.model_validate(payload)


def test_reduction_tree_centers_each_parent_between_children():
    result=layout_graph(semantic_graph('reduction_tree',[]));nodes={node.id:node for node in result.nodes}
    assert nodes['p1'].center[0]==pytest.approx((nodes['a1'].center[0]+nodes['a2'].center[0])/2)
    assert nodes['p2'].center[0]==pytest.approx((nodes['a3'].center[0]+nodes['a4'].center[0])/2)
    assert nodes['sum'].center[0]==pytest.approx((nodes['p1'].center[0]+nodes['p2'].center[0])/2)


def test_circle_projection_and_diagonal_boundary_are_exact():
    node=PlacedNode('circle','A',.1,.1,.2,.4,False,'circle',0)
    x,y,w,h=_graph_node_bounds(node,100,200,900,360);bounds={node.id:(x,y,w,h)}
    assert w==h and x>=100 and y>=200
    px,py=_graph_boundary_point(bounds,node,x+w*2,y+h*2)
    assert math.hypot(px-(x+w/2),py-(y+h/2))==pytest.approx(w/2)


@pytest.mark.parametrize('name',('fork_join','work_span','level_schedule'))
def test_fallback_graphs_use_semantic_labels_and_one_shape(name):
    result=layout_graph(semantic_graph(name,['Создание независимых ветвей','Сведение результатов','Готовая задача']))
    assert len({node.shape for node in result.nodes})==1
    assert not any(node.label.isdigit() or (len(node.label)==1 and node.label.isalpha()) for node in result.nodes)
    assert result.occupancy>=.55


def test_sequence_fallback_never_leaves_an_unclosed_formula_label():
    spec=default_diagram('sequence',[
        'Broadcast: CREW Θ(1), EREW Θ(log n)',
        'Nested loops: Θ(log n + log m)',
        'Ошибка: перемножение глубин',
    ])
    _,failures=graph_quality('sequence',[],spec)
    assert 'incomplete_labels' not in failures
    assert all(node.label.count('(')==node.label.count(')') for node in spec.nodes)


def test_rendered_math_uses_typographic_subscripts():
    assert _math_display('T_P ≥ W/P; T_level ≤ 2T*')=='Tₚ ≥ W/P; Tₗₑᵥₑₗ ≤ 2T*'


def test_rectangle_diagonal_boundary_hits_real_box_edge():
    node=next(node for node in layout_graph(semantic_graph('critical_path',[])).nodes if node.shape=='rounded')
    bounds={node.id:(100,200,160,80)}
    px,py=_graph_boundary_point(bounds,node,400,400)
    assert px==pytest.approx(260) or py==pytest.approx(280)
    assert 100<=px<=260 and 200<=py<=280


def test_unreadable_graph_labels_disqualify_candidate():
    diagram=DiagramSpec(kind='fork_join',nodes=[
        {'id':'a','label':'Слишком длинная подпись для круга'},
        {'id':'b','label':'B'},
    ],edges=[{'source':'a','target':'b'}])
    _,failures=graph_quality('fork_join',[],diagram)
    assert 'unreadable_labels' in failures


def test_short_formula_label_is_never_cut_inside_parentheses():
    diagram=default_diagram('comparison',['CREW: X → все','EREW: дерево копий','O(1) против Θ(log n)'])
    result,failures=graph_quality('comparison',[],diagram)
    assert 'incomplete_labels' not in failures
    assert result.nodes[-1].label=='O(1) против Θ(log n)'
