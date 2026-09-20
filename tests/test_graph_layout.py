import pytest

from vktech.graph_layout import semantic_graph,layout_graph
from vktech.export import _graph_node_bounds


GRAPH_TYPES=('fork_join','reduction_tree','work_span','level_bound','critical_path')


@pytest.mark.parametrize('name',GRAPH_TYPES)
def test_semantic_graph_rules_produce_clean_layout(name):
    spec=semantic_graph(name,['Work','Span','T ≤ 2T*'])
    result=layout_graph(spec)
    assert result.crossings<=spec.rules.max_crossings
    assert result.occupancy>=spec.rules.min_occupancy
    for node in result.nodes:
        assert 0<=node.x<node.x+node.w<=1
        assert 0<=node.y<node.y+node.h<=1
    for index,left in enumerate(result.nodes):
        for right in result.nodes[index+1:]:
            assert left.x+left.w<=right.x or right.x+right.w<=left.x or left.y+left.h<=right.y or right.y+right.h<=left.y


def test_graph_layout_is_deterministic_and_preserves_critical_path():
    spec=semantic_graph('work_span',['Work','Span','T_P ≥ max(W/P, S)'])
    first=layout_graph(spec);second=layout_graph(spec)
    assert first==second
    emphasized={node.id for node in first.nodes if node.emphasis}
    assert emphasized=={'start','a','d','e'}
    assert sum(edge.emphasis for edge in first.edges)==3


def test_reduction_tree_centers_parent_layers():
    result=layout_graph(semantic_graph('reduction_tree',[]))
    nodes={node.id:node for node in result.nodes}
    assert .25<nodes['p1'].center[0]<.5
    assert .5<nodes['p2'].center[0]<.75
    assert nodes['sum'].center[0]==pytest.approx(.5)


def test_circle_projection_stays_circular_in_rectangular_region():
    node=next(node for node in layout_graph(semantic_graph('work_span',[])).nodes if node.shape=='circle')
    x,y,w,h=_graph_node_bounds(node,100,200,900,360)
    assert w==h
    assert x>=100 and y>=200
