"""Deck-level narrative contracts, semantic invariants and editorial checks."""
from __future__ import annotations

import hashlib
import json
import re

from .contracts import ContentIR, LogicFinding, PlanSlide, PresentationPlan, SlideLogicContract


_STOP={
    'этот','эта','это','эти','для','как','или','при','после','перед','через','который','которая',
    'слайд','материал','лекция','лекции','пример','задача','алгоритм','алгоритмы','также','может',
    'позволяет','показывает','определяет','измеряет','выполняется','нужно','число','время',
}


def _tokens(value: str) -> set[str]:
    return {word.casefold()[:8] for word in re.findall(r'[A-Za-zА-Яа-яЁё][\w/-]{2,}',value) if word.casefold() not in _STOP}


def _concepts(slide: PlanSlide) -> list[str]:
    text=' '.join((slide.title,slide.message,*slide.visual_items))
    known=(
        'PRAM','EREW','CREW','CRCW','Work','Span','DAG','Fork–Join','parallel for','scheduler',
        'level-by-level','greedy','work stealing','критический путь','общая память','редукция',
        'Work-optimality','Set(A, x)','unit-cost','параллелизм','конкурентность',
    )
    return [item for item in known if re.search(re.escape(item),text,re.I)][:12]


def _canonical_concept(value: str) -> str | None:
    aliases=(
        (r'\bpram\b|модел\w*\s+pram','PRAM'),
        (r'\berew\b','EREW'),(r'\bcrew\b','CREW'),(r'\bcrcw\b','CRCW'),
        (r'\bwork\b','Work'),(r'\bspan\b','Span'),(r'\bdag\b','DAG'),
        (r'fork.?join','Fork–Join'),
        (r'scheduler|scheduling|планиров','scheduler'),
        (r'критическ\w*\s+пут','критический путь'),
        (r'нижн\w*\s+границ|lower\s+bounds?','нижние границы'),
        (r'общ\w*\s+памят','общая память'),(r'редукц','редукция'),
    )
    return next((canonical for pattern,canonical in aliases if re.search(pattern,value,re.I)),None)


def semantic_payload(slide: PlanSlide) -> dict:
    contract=slide.logic_contract
    diagram=slide.visual_contract.diagram
    return {
        'claims':sorted(slide.claim_ids),
        'assertion':contract.main_assertion or slide.message,
        'support':contract.supporting_assertions or slide.support_points,
        'entities':contract.required_visual_entities or slide.visual_contract.entities,
        'relations':contract.required_visual_relations or slide.visual_contract.relations,
        'diagram':diagram.model_dump() if diagram else None,
    }


def semantic_hash(slide: PlanSlide) -> str:
    raw=json.dumps(semantic_payload(slide),ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def enrich_logic_contracts(plan: PresentationPlan) -> PresentationPlan:
    result=plan.model_copy(deep=True)
    introduced=[]
    for index,slide in enumerate(result.slides):
        contract=slide.logic_contract.model_copy(deep=True)
        # Narrative prerequisites are computed from the final visible slide.
        # Free-form model labels such as "DAG-зависимости" and "DAG" are not
        # stable identifiers and previously produced false missing-prerequisite
        # errors even when the concept was already explained.
        explicit=[_canonical_concept(value) for value in contract.introduced_concepts]
        concepts=list(dict.fromkeys([value for value in [*explicit,*_concepts(slide)] if value]))
        current=[concept for concept in concepts if concept not in introduced]
        if not contract.teaching_goal:
            contract.teaching_goal=('Сформировать карту темы и ожидаемый результат.' if slide.role=='cover'
                                    else 'Объяснить, '+slide.title[:1].lower()+slide.title[1:].rstrip('.')+'.')
        if not contract.question_answered:
            contract.question_answered=('Что читатель сможет объяснить после презентации?' if slide.role=='cover'
                                        else 'Как устроено: '+slide.title.rstrip('?')+'?')
        contract.introduced_concepts=current
        explicit_priors=[_canonical_concept(value) for value in contract.required_prior_concepts]
        contract.required_prior_concepts=list(dict.fromkeys([
            value for value in [*explicit_priors,*concepts]
            if value and value not in current
        ]))[-4:]
        contract.source_claim_ids=list(slide.claim_ids)
        contract.main_assertion=contract.main_assertion or slide.takeaway or slide.message
        contract.supporting_assertions=list(contract.supporting_assertions or slide.support_points)
        if index and not contract.transition_from_previous:
            contract.transition_from_previous=f'После «{result.slides[index-1].title}» раскрывается следующий вопрос.'
        if index+1<len(result.slides) and not contract.transition_to_next:
            contract.transition_to_next=f'Далее: {result.slides[index+1].title}.'
        contract.visual_assertion=contract.visual_assertion or slide.visual_contract.goal
        diagram=slide.visual_contract.diagram
        if slide.visual_strategy=='diagram' and diagram:
            # The deterministic graph builder may replace an unsafe draft from
            # the model.  Audit the geometry that will actually be rendered,
            # rather than stale entity names from the draft contract.
            contract.required_visual_entities=[node.label for node in diagram.nodes][:12]
            contract.required_visual_relations=[
                f'{edge.source} → {edge.target}' for edge in diagram.edges
            ][:12]
        else:
            contract.required_visual_entities=list(slide.visual_items or slide.visual_contract.entities)
            contract.required_visual_relations=[]
        slide.logic_contract=contract
        slide.logic_contract.semantic_payload_hash=semantic_hash(slide)
        introduced.extend(current)
    return result


def _near_duplicate(left: str,right: str) -> float:
    a,b=_tokens(left),_tokens(right)
    return len(a&b)/max(1,min(len(a),len(b)))


def audit_deck_logic(plan: PresentationPlan,content: ContentIR|None=None) -> list[LogicFinding]:
    findings=[];introduced=set()
    for index,slide in enumerate(plan.slides):
        logic=slide.logic_contract
        missing=[name for name,value in (
            ('цель',logic.teaching_goal),('вопрос',logic.question_answered),('главный тезис',logic.main_assertion)
        ) if not value.strip()]
        if missing:findings.append(LogicFinding(rule='L01',severity='error',slide_id=slide.id,message='Не заполнены: '+', '.join(missing)+'.',repair_level='slide'))
        unavailable=[concept for concept in logic.required_prior_concepts if concept not in introduced]
        if unavailable:findings.append(LogicFinding(rule='L02',severity='error',slide_id=slide.id,message='Используются ещё не введённые понятия: '+', '.join(unavailable)+'.',repair_level='section'))
        introduced.update(logic.introduced_concepts)
        if index:
            previous=plan.slides[index-1]
            score=_near_duplicate(previous.logic_contract.main_assertion or previous.message,logic.main_assertion or slide.message)
            if score>=.72:findings.append(LogicFinding(rule='L03',severity='warning',slide_id=slide.id,message=f'Главный тезис почти повторяет предыдущий слайд ({score:.0%}).',repair_level='section',evidence={'previous_slide_id':previous.id,'similarity':round(score,3)}))
        prose=' '.join((slide.title,slide.message,slide.balanced_message,slide.takeaway,*slide.support_points))
        if re.search(r'…|\.\.\.|\bоглашения\b|\bглуби́\b',prose,re.I):findings.append(LogicFinding(rule='L04',severity='error',slide_id=slide.id,message='Обнаружен оборванный текст или редакционная ошибка.',repair_level='local'))
        if len(logic.source_claim_ids)==0:findings.append(LogicFinding(rule='L05',severity='error',slide_id=slide.id,message='Тезис не связан с источником.',repair_level='slide'))
        if slide.visual_strategy=='diagram':
            labels=[];relations=[]
            diagram=slide.visual_contract.diagram
            if diagram:
                labels=[node.label.casefold() for node in diagram.nodes]
                relations=[f'{edge.source} {edge.label} {edge.target}'.casefold() for edge in diagram.edges]
            visible=labels+slide.visual_items
            def shown(entity):
                compact=re.sub(r'\W+','',entity.casefold())
                return any((_tokens(entity)&_tokens(label)) or compact==re.sub(r'\W+','',label.casefold()) for label in visible)
            missing_entities=[e for e in logic.required_visual_entities if not shown(e)]
            if missing_entities:findings.append(LogicFinding(rule='L06',severity='error',slide_id=slide.id,message='Схема не показывает обязательные сущности: '+', '.join(missing_entities)+'.',repair_level='slide',evidence={'missing_entities':missing_entities}))
            if logic.required_visual_relations and diagram and not diagram.edges:
                findings.append(LogicFinding(rule='L07',severity='error',slide_id=slide.id,message='Схема перечисляет сущности, но не показывает связи между ними.',repair_level='slide'))
        if logic.semantic_payload_hash and logic.semantic_payload_hash!=semantic_hash(slide):
            findings.append(LogicFinding(rule='L08',severity='error',slide_id=slide.id,message='Смысловой пакет изменился после построения контракта.',repair_level='slide'))
    required={claim_id for slide in plan.slides for claim_id in slide.claim_ids}
    contracted={claim_id for slide in plan.slides for claim_id in slide.logic_contract.source_claim_ids}
    if required-contracted:findings.append(LogicFinding(rule='L09',severity='error',message='Не все исходные факты включены в смысловые контракты.',repair_level='deck',evidence={'claim_ids':sorted(required-contracted)}))
    if plan.slides:
        conclusion=plan.slides[-1]
        covered=set().union(*(_tokens(slide.logic_contract.main_assertion) for slide in plan.slides[:-1]))
        if len(_tokens(conclusion.message)&covered)<2:
            findings.append(LogicFinding(rule='L10',severity='warning',slide_id=conclusion.id,message='Итог не связывает ключевые идеи презентации.',repair_level='slide'))
    if content is not None:findings.extend(curriculum_findings(plan,content))
    return findings


def validate_variant_semantics(plan: PresentationPlan,scenes) -> None:
    """A/B/C may rearrange a slide, but cannot remove facts or graph structure."""
    by_variant={scene.variant:scene for scene in scenes}
    if not {'A','B','C'}<=set(by_variant):return
    for index,slide in enumerate(plan.slides):
        signatures=[]
        for variant in ('A','B','C'):
            scene_slide=by_variant[variant].slides[index]
            visual=next((node for node in scene_slide.nodes if node.role=='visual'),None)
            signatures.append({
                'claims':sorted({claim for node in scene_slide.nodes for claim in node.claim_ids}),
                'graph':visual.data.get('graph') if visual and visual.kind=='diagram' else None,
                'items':visual.data.get('items',[]) if visual and visual.kind=='diagram' else [],
            })
        if any(item!=signatures[0] for item in signatures[1:]):
            raise ValueError(f'Variants change semantic payload on slide {slide.id}')



_PRAM_MARKERS=(r'\bPRAM\b',r'\bEREW\b',r'\bWork\b',r'\bSpan\b',r'level-by-level',r'Set\s*\(\s*A\s*,\s*x\s*\)')
_PRAM_CHECKPOINTS=(
    {
        'id':'access-models','label':'Режимы доступа PRAM: EREW, CREW и CRCW.',
        'fact':'EREW, CREW и CRCW задают разные правила совместного доступа в PRAM.',
        'groups':((r'\bpram\b',),(r'\berew\b',),(r'\bcrew\b',),(r'\bcrcw\b',)),
        'source':(r'режимы доступа',r'\berew\b.*\bcrew\b.*\bcrcw\b'),
    },
    {
        'id':'reduction','label':'Бинарная редукция: Work Θ(n), Span Θ(log n).',
        'fact':'Бинарная редукция имеет Work Θ(n) и Span Θ(log n).',
        'groups':((r'редукц',),(r'work.{0,35}(?:θ|theta)\s*\(?n',),(r'span.{0,45}(?:log\s*n|логариф)',)),
        'source':(r'редукц',r'число активных значений'),
    },
    {
        'id':'fork-join-dag','label':'Fork–Join как DAG задач и зависимостей.',
        'fact':'Fork создаёт независимые ветви, а join добавляет зависимости DAG.',
        'groups':((r'fork',),(r'join',),(r'\bdag\b|ациклич',)),
        'source':(r'fork.?join',r'\bdag\b'),
    },
    {
        'id':'work-span','label':'Work как общая работа, Span как критический путь.',
        'fact':'Work суммирует работу, а Span равен длине критического пути.',
        'groups':((r'\bwork\b',),(r'\bspan\b',),(r'критическ\w*\s+пут',)),
        'source':(r'work.{0,80}span',r'критическ\w*\s+пут'),
    },
    {
        'id':'lower-bounds','label':'Нижние границы T_P ≥ W/P и T_P ≥ S.',
        'fact':'Для любого допустимого планировщика T_P ≥ W/P и T_P ≥ S.',
        'groups':((r'(?:t[_ ]?p|tₚ).{0,25}(?:≥|>=).{0,20}w\s*/\s*p',),(r'(?:t[_ ]?p|tₚ).{0,25}(?:≥|>=).{0,12}s\b',)),
        'source':(r'нижн\w* границ',r't[_ ]?p.*w\s*/\s*p'),
    },
    {
        'id':'schedulers','label':'Различие level-by-level и greedy scheduler.',
        'fact':'Level-by-level ждёт уровень, а greedy запускает любую готовую задачу.',
        'groups':((r'level-by-level',),(r'\bgreedy\b',)),
        'source':(r'level-by-level',r'greedy'),
    },
    {
        'id':'level-bound','label':'Верхняя граница level-by-level: W/P + S.',
        'fact':'Level-by-level завершается не позже W/P + S в unit-cost DAG.',
        'groups':((r'level-by-level',),(r'w\s*/\s*p\s*\+\s*s',),(r'unit-cost',)),
        'source':(r'формул\w* брента',r'w\s*/\s*p\s*\+\s*s'),
    },
    {
        'id':'work-optimality','label':'Сначала Work-optimality, затем уменьшение Span.',
        'fact':'Сначала сохраняют Work-optimality, затем уменьшают Span.',
        'groups':((r'work-optimal|оптимальн\w*\s+по\s+(?:работ|work)',),(r'\bspan\b',)),
        'source':(r'work-optimal',r'порядок полезного параллелизма'),
    },
    {
        'id':'real-machine','label':'Ограничения реальной машины: память, locality и overhead.',
        'fact':'Реальная машина добавляет задержки памяти, locality и overhead задач.',
        'groups':((r'кэш|cache|numa|памят',),(r'overhead|накладн|contention|locality',)),
        'source':(r'cache|кэш|numa',r'overhead|contention|locality'),
    },
    {
        'id':'broadcast','label':'Broadcast: CREW Θ(1), EREW Θ(log n).',
        'fact':'Broadcast занимает Θ(1) в CREW и Θ(log n) в EREW при P=n.',
        'groups':((r'broadcast',),(r'\bcrew\b',),(r'\berew\b',),(r'(?:θ|o)\s*\(?1\)?',),(r'(?:θ|o)\s*\(?log\s*n',)),
        'source':(r'broadcast',r'дерев\w* распростран'),
    },
    {
        'id':'set-complexity','label':'Set(A,x): EREW Θ(log n), CREW/CRCW Θ(1), Work Θ(n).',
        'fact':'Set(A,x): EREW — Θ(log n), CREW и CRCW — Θ(1), Work — Θ(n).',
        'groups':((r'set\s*\(\s*a\s*,\s*x\s*\)',),(r'\berew\b',),(r'\bcrew\b',),(r'\bcrcw\b',),(r'(?:θ|o)\s*\(?log\s*n',),(r'(?:θ|o)\s*\(?1\)?',)),
        'source':(r'set\s*\(\s*a\s*,\s*x\s*\)',),
    },
    {
        'id':'two-approximation','label':'Level-by-level не хуже 2× оптимального расписания.',
        'fact':'Для unit-cost DAG выполняется T_level ≤ 2T*.',
        'groups':((r'level-by-level|t[_ ]?level',),(r'2\s*[×x*]?\s*(?:t\*|оптим)',)),
        'source':(r'2\s*раз|2\s*[×x*]?\s*t\*|2-аппрокс',),
    },
)


def _pram_profile(content: ContentIR) -> bool:
    corpus=' '.join(claim.text for claim in content.claims)
    return all(re.search(marker,corpus,re.I) for marker in _PRAM_MARKERS)


def _checkpoint_covered(checkpoint: dict, text: str) -> bool:
    return all(any(re.search(pattern,text,re.I|re.S) for pattern in alternatives) for alternatives in checkpoint['groups'])


def _checkpoint_claim_ids(checkpoint: dict,content: ContentIR) -> list[str]:
    ranked=[]
    for claim in content.claims:
        score=sum(bool(re.search(pattern,claim.text,re.I|re.S)) for pattern in checkpoint['source'])
        if score:ranked.append((-score,claim.source,claim.id))
    return [claim_id for _,_,claim_id in sorted(ranked)[:3]]


def curriculum_checkpoints(content: ContentIR,slide_count: int=20) -> list[dict]:
    """Return source-linked requirements without prescribing slide order or wording."""
    if slide_count<12 or not _pram_profile(content):return []
    result=[]
    for checkpoint in _PRAM_CHECKPOINTS:
        item={key:value for key,value in checkpoint.items() if key not in {'groups','source'}}
        item['claim_ids']=_checkpoint_claim_ids(checkpoint,content)
        result.append(item)
    return result


def _plan_text(plan: PresentationPlan) -> str:
    return '\n'.join(' '.join((slide.title,slide.message,slide.balanced_message,slide.takeaway,*slide.support_points,*slide.visual_items)) for slide in plan.slides)


def apply_curriculum_guardrails(plan: PresentationPlan,content: ContentIR) -> PresentationPlan:
    """Keep the model's story and add only missing source-backed checkpoint facts.

    The previous implementation replaced every generated PRAM deck with one
    hard-coded 20-slide plan.  Guardrails preserve authored order, titles,
    messages and visual choices.  A missing mandatory fact is attached to the
    most relevant source-linked slide as a short support point.
    """
    checkpoints=curriculum_checkpoints(content,len(plan.slides))
    if not checkpoints:return plan
    result=plan.model_copy(deep=True);text=_plan_text(result)
    for checkpoint,definition in zip(checkpoints,_PRAM_CHECKPOINTS):
        if _checkpoint_covered(definition,text):continue
        claim_ids=set(checkpoint['claim_ids'])
        candidates=[slide for slide in result.slides if claim_ids.intersection(slide.claim_ids)]
        if not candidates:
            candidates=[slide for slide in result.slides[1:-1] if slide.role=='content'] or result.slides
        source_rank={claim_id:index for index,claim_id in enumerate(checkpoint['claim_ids'])}
        target=min(candidates,key=lambda slide:(
            min((source_rank[cid] for cid in slide.claim_ids if cid in source_rank),default=len(source_rank)),
            len(slide.support_points)>=3,len(slide.support_points),len(slide.message),slide.id,
        ))
        if checkpoint['fact'] not in target.support_points:
            if len(target.support_points)<3:target.support_points.append(checkpoint['fact'])
            elif len(target.message)+len(checkpoint['fact'])+1<=220:target.message+=' '+checkpoint['fact']
            else:target.support_points[-1]=checkpoint['fact']
        for claim_id in checkpoint['claim_ids']:
            if claim_id not in target.claim_ids:target.claim_ids.append(claim_id)
        text=_plan_text(result)
    return result


def curriculum_findings(plan: PresentationPlan,content: ContentIR) -> list[LogicFinding]:
    checkpoints=curriculum_checkpoints(content,len(plan.slides))
    if not checkpoints:return []
    text=_plan_text(plan);findings=[]
    definitions={item['id']:item for item in _PRAM_CHECKPOINTS}
    for checkpoint in checkpoints:
        if not _checkpoint_covered(definitions[checkpoint['id']],text):
            findings.append(LogicFinding(rule='L11',severity='error',message='Не раскрыта обязательная тема: '+checkpoint['label'],repair_level='deck',evidence={'checkpoint_id':checkpoint['id'],'claim_ids':checkpoint['claim_ids']}))
    def directly_assigns(model: str,complexity: str) -> bool:
        # EREW/CREW also occur in the broadcast example with different valid
        # bounds.  A reversal is an error here only inside a slide that
        # explicitly explains Set(A, x).
        for slide in plan.slides:
            slide_text=' '.join((slide.title,slide.message,slide.balanced_message,slide.takeaway,*slide.support_points,*slide.visual_items))
            if not re.search(r'set\s*\(\s*a\s*,\s*x\s*\)',slide_text,re.I):continue
            for sentence in re.split(r'(?<=[.!?;])\s+|\n+',slide_text):
                for match in re.finditer(rf'\b{model}\b',sentence,re.I):
                    tail=sentence[match.end():]
                    value=re.search(complexity,tail,re.I)
                    if value and not re.search(r'\b(?:EREW|CREW|CRCW)\b',tail[:value.start()],re.I):return True
        return False
    # These reversals are unequivocally inconsistent with the supplied source.
    if directly_assigns('EREW',r'(?:Θ|theta|O)\s*\(?1\)?'):
        findings.append(LogicFinding(rule='L12',severity='error',message='Для Set(A,x) нельзя указывать Θ(1) в EREW.',repair_level='slide',evidence={'checkpoint_id':'set-complexity'}))
    if directly_assigns('CREW',r'(?:Θ|theta|O)\s*\(?log\s*n') or directly_assigns('CRCW',r'(?:Θ|theta|O)\s*\(?log\s*n'):
        findings.append(LogicFinding(rule='L12',severity='error',message='Для Set(A,x) CREW и CRCW имеют Θ(1), а не Θ(log n).',repair_level='slide',evidence={'checkpoint_id':'set-complexity'}))
    return findings


# Backward-compatible name used by older jobs and imports.  It now preserves
# model authorship instead of replacing the complete plan.
def restructure_known_curriculum(plan: PresentationPlan,content: ContentIR) -> PresentationPlan:
    return apply_curriculum_guardrails(plan,content)
