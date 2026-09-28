import pytest

from vktech.contracts import Box,Claim,ContentIR,Node,PlanSlide,PresentationPlan,SceneIR,SceneSlide,Style
from vktech.logic import apply_curriculum_guardrails,audit_deck_logic,curriculum_checkpoints,curriculum_findings,enrich_logic_contracts,semantic_hash,validate_variant_semantics


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


def pram_content():
    facts=[
        'EREW, CREW и CRCW задают разные правила совместного доступа в PRAM.',
        'Бинарная редукция имеет Work Θ(n) и Span Θ(log n).',
        'Fork создаёт независимые ветви, а Join добавляет зависимости DAG.',
        'Work суммирует работу, а Span равен длине критического пути.',
        'Нижние границы: T_P ≥ W/P и T_P ≥ S.',
        'Level-by-level ждёт уровень, а greedy запускает любую готовую задачу.',
        'Формула Брента даёт W/P + S для unit-cost DAG.',
        'Work-optimality сохраняет работу, после чего уменьшают Span.',
        'Кэши, NUMA, locality и overhead ограничивают реальную машину.',
        'Broadcast занимает Θ(1) в CREW и Θ(log n) в EREW при P=n.',
        'Set(A, x): EREW — Θ(log n), CREW и CRCW — Θ(1), Work — Θ(n).',
        'Level-by-level scheduler является 2-аппроксимацией: T_level ≤ 2T*.',
    ]
    return ContentIR(id='pram',title='PRAM',claims=[Claim(id=f'claim-{index+1}',text=fact,source=f'page-{index+1}') for index,fact in enumerate(facts)])


def test_pram_guardrails_preserve_model_structure_and_fill_checkpoints():
    content=pram_content()
    slides=[PlanSlide(id=f'slide-{index+1}',title=f'Авторский заголовок {index+1}',message=f'Авторская мысль {index+1}.',balanced_message=f'Сбалансированная мысль {index+1}.',takeaway=f'Вывод {index+1}.',claim_ids=[f'claim-{index+1}']) for index in range(12)]
    authored=PresentationPlan(slides=slides)
    guarded=apply_curriculum_guardrails(authored,content)
    assert len(curriculum_checkpoints(content,12))==12
    assert [item.title for item in guarded.slides]==[item.title for item in authored.slides]
    assert [item.message for item in guarded.slides]==[item.message for item in authored.slides]
    assert all(finding.rule!='L11' for finding in audit_deck_logic(guarded,content))
    assert any(item.support_points for item in guarded.slides)


def test_pram_audit_rejects_reversed_set_complexity():
    content=pram_content();slides=[]
    for index,claim in enumerate(content.claims):
        slides.append(PlanSlide(id=f'slide-{index+1}',title=f'Тема {index+1}',message=claim.text,balanced_message=claim.text,takeaway=claim.text,claim_ids=[claim.id]))
    plan=PresentationPlan(slides=slides)
    plan.slides[-2].support_points=['EREW — Θ(1).']
    assert any(finding.rule=='L12' for finding in audit_deck_logic(plan,content))


def test_curriculum_does_not_treat_logical_as_log_n():
    content=pram_content()
    slides=[]
    for index,claim in enumerate(content.claims):
        text=claim.text
        if index==1:text='Бинарная редукция имеет Work Θ(n), а logical processors выполняют пары.'
        slides.append(PlanSlide(id=f'slide-{index+1}',title=f'Тема {index+1}',message=text,balanced_message=text,takeaway=text,claim_ids=[claim.id]))
    findings=curriculum_findings(PresentationPlan(slides=slides),content)
    assert any(finding.rule=='L11' and finding.evidence.get('checkpoint_id')=='reduction' for finding in findings)
