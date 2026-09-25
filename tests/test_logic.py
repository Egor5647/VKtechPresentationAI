import pytest

from vktech.contracts import Box,Node,PlanSlide,PresentationPlan,SceneIR,SceneSlide,Style
from vktech.logic import audit_deck_logic,enrich_logic_contracts,semantic_hash,validate_variant_semantics


def slide(number,title,message,concepts=(),priors=()):
    item=PlanSlide(id=f's{number}',title=title,message=message,balanced_message=message+' Дополнение.',takeaway=title+'.',claim_ids=[f'c{number}'])
    item.logic_contract.introduced_concepts=list(concepts)
    item.logic_contract.required_prior_concepts=list(priors)
    item.logic_contract.main_assertion=message
    return item


def test_logic_contract_is_enriched_and_hashed():
    plan=enrich_logic_contracts(PresentationPlan(slides=[slide(1,'Введение','Первый законченный тезис.')]))
    contract=plan.slides[0].logic_contract
    assert contract.teaching_goal and contract.question_answered
    assert contract.source_claim_ids==['c1']
    assert contract.semantic_payload_hash==semantic_hash(plan.slides[0])


def test_logic_audit_finds_unavailable_concept_and_adjacent_duplicate():
    first=slide(1,'Первая идея','Общая память задаёт правила доступа к ячейкам.',['PRAM'])
    second=slide(2,'Повтор идеи','Общая память задаёт правила доступа к ячейкам.',['DAG'],['Span'])
    plan=enrich_logic_contracts(PresentationPlan(slides=[first,second]))
    rules={finding.rule for finding in audit_deck_logic(plan)}
    assert 'L02' in rules
    assert 'L03' in rules


def scene(variant,items):
    visual=Node(id='visual',kind='diagram',role='visual',box=Box(x=0,y=0,w=1,h=1),style=Style(),claim_ids=['c1'],data={'items':items,'graph':None})
    return SceneIR(id=variant,variant=variant,template_id='t',content_id='c',width=100,height=100,slides=[SceneSlide(id='s1',title='Тема',role='content',prototype_id='p',background='FFFFFF',nodes=[visual])])


def test_variants_must_keep_the_same_visual_entities():
    plan=enrich_logic_contracts(PresentationPlan(slides=[slide(1,'Тема','Один и тот же тезис.')]))
    with pytest.raises(ValueError,match='semantic payload'):
        validate_variant_semantics(plan,[scene('A',['A','B']),scene('B',['A']),scene('C',['A','B'])])


def test_variants_accept_identical_semantic_payload():
    plan=enrich_logic_contracts(PresentationPlan(slides=[slide(1,'Тема','Один и тот же тезис.')]))
    validate_variant_semantics(plan,[scene('A',['A','B']),scene('B',['A','B']),scene('C',['A','B'])])
