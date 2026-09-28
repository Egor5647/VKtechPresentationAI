from __future__ import annotations
import hashlib
import logging
import os
import re
from .settings import artifact_path
from .contracts import PresentationPlan, ContentIR, DesignIR, SceneIR, SceneSlide, Node, Style, Box, SlideRevision, VisualContract
from .graph_layout import default_diagram,graph_quality

log=logging.getLogger(__name__)


class NeedsInput(ValueError):
    pass


_BOILERPLATE=re.compile(
    r'^(?:параллельные алгоритмы\s*\|.*|конспект лекции\s*[·•]\s*стр\.?\s*\d+|'
    r'таймкод\s*:.*|материал лекции|дополнительное пояснение|интерпретация|'
    r'что важно запомнить|главные идеи|вопросы для самопроверки)$',re.I
)
_FOCUS_STOP={
    'алгоритм','алгоритмы','параллельные','параллельных','лекция','лекции','материал',
    'пояснение','вопрос','ответ','пример','основные','основной','ключевые','который',
    'которая','которые','через','после','перед','также','может','нужно','этого','затем',
    'work','span','pram','задачи','задача','модель','метрики','метрика','выполнение',
    'interview','notes','pageref','документ','построен','предоставленной','внешнего',
    'учебника','источник','страницы','страниц','кликабельны','записи','лекций',
}
_DIRECTIONAL_MARKERS={
    'maximize':r'\bмаксимиз\w*|\bнаибольш\w*',
    'upper_bound':r'\bне\s+(?:более|выше)|\bне\s+превыш\w*|≤|\bверхн\w*\s+границ\w*',
    'lower_bound':r'\bне\s+(?:менее|ниже|меньше)|≥|\bнижн\w*\s+границ\w*',
}


def _planning_text(value: str) -> str:
    """Remove presentation furniture while preserving source facts verbatim."""
    value=_canonicalize_math(value)
    lines=[]
    for line in value.splitlines():
        line=line.strip()
        if not line or _BOILERPLATE.fullmatch(line):continue
        line=re.sub(r'^(?:короткий хороший ответ|более глубокий ответ)\s*:\s*','',line,flags=re.I)
        line=re.sub(r'\s*[·•]\s*с\.\s*\d+(?:\s*[–—-]\s*\d+)?\s*$','',line,flags=re.I)
        if line:lines.append(line)
    return '\n'.join(lines)


def _tokens(value: str) -> list[str]:
    return [token.lower() for token in re.findall(r'[A-Za-zА-Яа-яЁё][\w+#/-]{3,}',value)]


def _focus_terms(group,all_claims,limit=12) -> list[str]:
    """Find terms that distinguish one assigned source block from the deck."""
    documents=[set(_tokens(claim.text)) for claim in all_claims]
    frequency={token:sum(token in document for document in documents) for document in documents for token in document}
    candidates=[]
    for claim in group:
        for position,token in enumerate(_tokens(claim.text)):
            if token in _FOCUS_STOP or token.isdigit() or len(token)<5:continue
            df=frequency.get(token,1)
            if df>max(2,round(len(documents)*.3)):continue
            latin=bool(re.search(r'[a-z]',token))
            score=(.5 if latin else 0)+(len(token)>=8)*2+(len(documents)-df)/max(1,len(documents))-position/1000
            candidates.append((score,token))
    result=[]
    for _,token in sorted(candidates,reverse=True):
        if token not in result:result.append(token)
        if len(result)>=limit:break
    return result


def _contains_focus(value: str,terms) -> bool:
    words=_tokens(value)
    for term in terms:
        stem=term[:6] if len(term)>=7 else term
        if any(word.startswith(stem) or stem.startswith(word[:6]) for word in words):return True
    return False


def _directional_markers(value: str) -> set[str]:
    return {name for name,pattern in _DIRECTIONAL_MARKERS.items() if re.search(pattern,value,re.I)}


def _phrase_key(value: str) -> str:
    return re.sub(r'\W+',' ',value.casefold()).strip()


def _semantic_tokens(value: str) -> set[str]:
    """Lightweight stems for catching near-identical slide ideas."""
    return {token[:6] for token in _tokens(value) if len(token)>=4 and token not in _FOCUS_STOP}


def _near_duplicate(left: str,right: str) -> bool:
    a=_semantic_tokens(left);b=_semantic_tokens(right);shared=a&b
    # Shared domain vocabulary is common in one deck (for example EREW/CREW
    # appear in both broadcast and Set(A, x)). Only reject ideas whose smaller
    # semantic vocabulary is almost completely contained in the other.
    return len(shared)>=6 and len(shared)/max(1,min(len(a),len(b)))>=.82


def _complete_sentence(value: str) -> bool:
    value=value.rstrip()
    return bool(value and re.search(r'[.!?)]$',value))


def _looks_finished(value: str) -> bool:
    """Reject obvious grammar-boundary artefacts without language guessing."""
    value=value.rstrip()
    if not _complete_sentence(value) or re.search(r'[,;:]\s*[.!?)]$',value):return False
    words=re.findall(r'[A-Za-zА-Яа-яЁё]+',value[:-1])
    if not words:return True
    last=words[-1]
    if re.search(r'[A-Za-z]',last) and re.search(r'[А-Яа-яЁё]',last):return False
    if last==last.casefold() and last.casefold() in {'и','а','но','или','что','как','если','для','при','над','под','без','из','от','по','на','в','к','с'}:return False
    if re.fullmatch(r'[а-яё]{1,3}',last) and last==last.casefold():return False
    return True


def _finish_if_safe(value: str) -> str:
    """Add missing terminal punctuation only when the clause already has a safe end."""
    value=_clean_text(value).rstrip()
    if not value:return ''
    if _looks_finished(value):return value
    if re.search(r'[.!?)]$',value):return ''
    words=re.findall(r'[A-Za-zА-Яа-яЁё]+',value)
    if not words:return value+'.'
    last=words[-1].casefold()
    if last in {'и','а','но','или','что','как','если','для','при','над','под','без','из','от','по','на','в','к','с'}:return ''
    return value+'.'


def _sentence(value: str) -> str:
    value=_clean_text(value).rstrip(' .!?')
    return value+'.' if value else ''


def _canonicalize_math(value: str) -> str:
    """Repair common PDF glyph extraction errors when the notation is unambiguous."""
    return re.sub(r'[T𝑇]!', 'T_P', value)


def _sentence_units(value: str) -> list[str]:
    return [part.strip() for part in re.findall(r'.+?(?:[.!?](?=\s|$)|$)',value) if part.strip()]


def _repeats_unit(left: str,right: str) -> bool:
    left_key,right_key=_phrase_key(left),_phrase_key(right)
    if left_key==right_key or left_key in right_key or right_key in left_key:return True
    a,b=_semantic_tokens(left),_semantic_tokens(right);shared=a&b
    return len(shared)>=4 and len(shared)/max(1,min(len(a),len(b)))>=.85


def _dedupe_sentences(value: str) -> str:
    result=[]
    for unit in _sentence_units(_canonicalize_math(value)):
        if any(_repeats_unit(unit,previous) for previous in result):continue
        result.append(unit)
    return ' '.join(result)


def _compose(base: str, additions, limit: int, forbidden=(), minimum=0, prefer_longest=True) -> str | None:
    forbidden={_phrase_key(value) for value in forbidden}
    candidates=[base]
    for addition in (_sentence(value) for value in additions if value):
        for chosen in list(candidates):
            if any(_repeats_unit(addition,unit) for unit in _sentence_units(chosen)):continue
            candidate=(chosen+' '+addition).strip()
            if len(candidate)<=limit and candidate not in candidates:candidates.append(candidate)
    valid=[value for value in candidates if minimum<=len(value)<=limit and _phrase_key(value) not in forbidden]
    chooser=max if prefer_longest else min
    return chooser(valid,key=len,default=None)


def fit_semantic_variants(plan: PresentationPlan) -> PresentationPlan:
    """Fit authored whole thoughts by composing complete model-written units."""
    result=plan.model_copy(deep=True)
    for slide in result.slides:
        # Imported legacy plans did not author density-specific copies. Keep
        # that compatibility path; enrich_plan will derive display metadata.
        if not slide.takeaway and not slide.balanced_message:
            slide.support_points=[finished for value in slide.support_points if (finished:=_finish_if_safe(value)) and len(finished)<=110][:3]
            continue
        supports=[]
        for value in slide.support_points:
            finished=_finish_if_safe(value)
            if finished and len(finished)<=110:supports.append(_dedupe_sentences(finished))
        slide.support_points=supports[:3]
        title_sentence=_sentence(slide.title)
        finished_takeaway=_finish_if_safe(slide.takeaway)
        concise=_dedupe_sentences(finished_takeaway) if len(finished_takeaway)<=130 and _looks_finished(finished_takeaway) else ''
        if not concise:
            fallbacks=[*supports,_finish_if_safe(slide.balanced_message),_finish_if_safe(slide.message),title_sentence]
            concise=next((value for value in fallbacks if len(value)<=130 and _looks_finished(value)),title_sentence)
        authored_balanced=_dedupe_sentences(_finish_if_safe(slide.balanced_message))
        balanced=authored_balanced if len(authored_balanced)<=180 and _looks_finished(authored_balanced) else ''
        balanced_tokens=_semantic_tokens(balanced);concise_tokens=_semantic_tokens(concise)
        shallow_balanced=balanced and _near_duplicate(balanced,concise) and len(balanced_tokens)<len(concise_tokens)*1.45
        if not balanced or shallow_balanced or _phrase_key(balanced)==_phrase_key(concise) or len(balanced)<len(concise)+8:
            additions=[*_sentence_units(authored_balanced),*supports]
            target=min(180,max(75,len(concise)+8))
            balanced=_compose(concise,additions,180,[concise],target,False) or _compose(concise,[title_sentence],180,[concise],target,False) or title_sentence
        authored_detailed=_dedupe_sentences(_finish_if_safe(slide.message))
        detailed=authored_detailed if len(authored_detailed)<=220 and _looks_finished(authored_detailed) else ''
        detailed_tokens=_semantic_tokens(detailed)
        shallow_repeat=detailed and _near_duplicate(detailed,concise) and len(detailed_tokens)<len(concise_tokens)*1.45
        if not detailed or shallow_repeat or _phrase_key(detailed) in {_phrase_key(concise),_phrase_key(balanced)} or len(detailed)<len(balanced)+8:
            additions=[*_sentence_units(authored_detailed),*supports,*_sentence_units(authored_balanced)]
            target=min(220,len(balanced)+8)
            detailed=_compose(concise,additions,220,[concise,balanced],target,True) or _compose(balanced,additions,220,[concise,balanced],target,True)
        if not detailed:
            detailed=_compose(concise,[title_sentence],220,[concise,balanced]) or balanced
        if not (len(concise)<len(balanced)<len(detailed)):
            # Local models occasionally author three sound versions in the
            # wrong size order. Reuse whole authored thoughts and reorder them
            # instead of cutting characters or requesting another generation.
            candidates=[]
            for value in (concise,balanced,detailed,finished_takeaway,authored_balanced,authored_detailed,title_sentence,*supports):
                value=_dedupe_sentences(_finish_if_safe(value))
                if value and value not in candidates:candidates.append(value)
            triples=[]
            for short in candidates:
                if len(short)>130:continue
                for medium in candidates:
                    if not len(short)<len(medium)<=180:continue
                    for long in candidates:
                        if len(medium)<len(long)<=220:triples.append((len(long),len(medium),len(short),short,medium,long))
            if triples:
                *_,concise,balanced,detailed=max(triples)
        slide.takeaway=concise;slide.balanced_message=balanced;slide.message=detailed
    return result


def fit_titles(plan: PresentationPlan) -> PresentationPlan:
    result=plan.model_copy(deep=True)
    for slide in result.slides:
        if len(slide.title)>76 and ':' in slide.title:
            shorter=_clean_text(slide.title.split(':',1)[0])
            if len(shorter)>=20:slide.title=shorter
    return result


def planning_claims(content: ContentIR, brief: str,slide_count: int=12) -> list:
    """Bound model context while retaining every explicitly mandatory claim."""
    limit=max(1,int(os.environ.get('MODEL_MAX_PLANNING_CLAIMS','32')))
    char_limit=max(1000,int(os.environ.get('MODEL_MAX_PLANNING_CHARS','18000')))
    required=[claim for claim in content.claims if claim.required]
    required_chars=sum(len(claim.text) for claim in required)
    if len(required)>limit or required_chars>char_limit:
        raise NeedsInput(
            f'Исходные материалы содержат {len(required)} обязательных блоков объёмом {required_chars} символов. '
            'Разделите документ или отметьте второстепенные разделы как необязательные.'
        )
    terms=set(re.findall(r'[\w+#<>]{3,}',brief.lower()))
    def rank(claim):
        words=set(re.findall(r'[\w+#<>]{3,}',claim.text.lower()))
        return (-len(words&terms),len(claim.text),claim.source,claim.id)
    selected=list(required);used={claim.id for claim in selected};chars=required_chars
    # Curriculum checkpoints reserve their source fragments before generic
    # relevance ranking.  This keeps mandatory facts in the model context
    # without prescribing slide titles, order or wording.
    from .logic import curriculum_checkpoints
    checkpoint_ids=[]
    for checkpoint in curriculum_checkpoints(content,slide_count):
        checkpoint_ids.extend(checkpoint['claim_ids'])
    by_id={claim.id:claim for claim in content.claims}
    for claim_id in dict.fromkeys(checkpoint_ids):
        claim=by_id.get(claim_id)
        if not claim or claim.id in used or len(selected)>=limit or chars+len(claim.text)>char_limit:continue
        selected.append(claim);used.add(claim.id);chars+=len(claim.text)
    for claim in sorted((c for c in content.claims if c.id not in used),key=rank):
        if len(selected)>=limit:break
        if chars+len(claim.text)>char_limit:continue
        selected.append(claim);chars+=len(claim.text)
    if not selected and content.claims:selected=[min(content.claims,key=lambda c:(len(c.text),c.id))]
    return selected


def _visual_kind(slide) -> str:
    text=(slide.title+' '+slide.message).lower()
    if re.search(r'этап|послед|алгоритм|планиров|scheduler|fork|join|reduc|распростран|propagat|\bset\b',text):return 'sequence'
    if re.search(r'dag|граф|дерев|иерарх|завис|work|span',text):return 'hierarchy'
    return 'list'


def normalize_plan(plan: PresentationPlan) -> PresentationPlan:
    """Repair optional visual intents and enforce a useful native-visual quota."""
    result=plan.model_copy(deep=True)
    for slide in result.slides:
        slide.title=_clean_text(_canonicalize_math(slide.title))
        slide.message=_dedupe_sentences(slide.message)
        slide.balanced_message=_dedupe_sentences(slide.balanced_message)
        slide.takeaway=_dedupe_sentences(slide.takeaway)
        slide.support_points=list(dict.fromkeys(_dedupe_sentences(value) for value in slide.support_points if value.strip()))
        slide.visual_items=list(dict.fromkeys(_canonicalize_math(value) for value in slide.visual_items if value.strip()))
        slide.visual_brief=_canonicalize_math(slide.visual_brief)
        if slide.visual=='image' and not slide.asset_id:slide.visual='none'
        if slide.visual in {'chart','table'} and not slide.dataset_id:slide.visual='none'
    candidates=[s for s in result.slides if s.role=='content' and not s.dataset_id and not s.asset_id]
    target=round(len(candidates)*.4)
    have=sum(s.visual in {'sequence','list','hierarchy'} for s in candidates)
    ranked=sorted((s for s in candidates if s.visual=='none'),key=lambda s:(0 if re.search(r'PRAM|DAG|Work|Span|планиров|алгоритм|сравн|этап',s.title+' '+s.message,re.I) else 1,s.id))
    for slide in ranked[:max(0,target-have)]:slide.visual=_visual_kind(slide)
    return result


def _illustration_concept(slide: PlanSlide) -> tuple[str,float] | None:
    """Return a concrete, defensible editorial illustration concept.

    Technical vocabulary alone is not enough: DAGs, formulas and algorithms are
    clearer as editable diagrams.  Images are reserved for contrasts and
    physical constraints that can be understood from a scene without labels.
    """
    text=' '.join((slide.title,slide.message,slide.visual_brief)).lower()
    if re.search(r'concurrenc|конкурент',text) and re.search(r'parallelism|параллелизм',text):
        return 'concurrency_parallelism',.9
    bottlenecks=sum(bool(re.search(pattern,text,re.I)) for pattern in (
        r'bandwidth|пропускн',r'cache|кэш',r'numa',r'locks?|блокиров',r'contention|конкуренц\w*\s+за',r'oversubscription|переподпис',
    ))
    if bottlenecks>=3 and re.search(r'реальн|production|инженер|практич',text,re.I):
        return 'real_system_bottleneck',.86
    return None


def assign_visual_strategies(plan: PresentationPlan,content: ContentIR,allow_generated: bool) -> PresentationPlan:
    """Choose the visual medium from slide semantics instead of a fixed image quota."""
    result=plan.model_copy(deep=True);last=len(result.slides)-1;assets={asset.id:asset for asset in content.assets}
    image_words=re.compile(r'сценар|ситуац|пользоват|клиент|команд|человек|продукт|интерфейс|экосистем|инфраструктур|реальн\w* систем|практическ\w* контекст',re.I)
    formula_words=re.compile(r'формул|доказ|теорем|границ|[≤≥=]|θ\s*\(|\bo\s*\(',re.I)
    hierarchy_words=re.compile(r'\bdag\b|граф|зависим|иерарх|архитектур',re.I)
    sequence_words=re.compile(r'этап|алгоритм|процесс|fork.?join|редукц|scheduler|планиров|broadcast|parallel for|цикл',re.I)
    comparison_words=re.compile(r'сравн|различ|режим|\bversus\b|\bvs\b|erew.+crew|crew.+crcw',re.I)
    conceptual_words=re.compile(r'от\s+конкурент\w*\s+код\w*\s+к\s+абстракт|практическ\w*\s+реализац|инженерн\w*\s+(?:контекст|систем)|пользовательск\w*\s+сценар',re.I)
    for index,slide in enumerate(result.slides):
        text=' '.join((slide.title,slide.message,slide.visual_brief)).lower()
        illustration=_illustration_concept(slide)
        generated_asset=assets.get(slide.asset_id) if slide.asset_id else None
        if generated_asset and not (generated_asset.source.startswith('Z-Image') or generated_asset.source.startswith('AI-generated')):generated_asset=None
        if slide.dataset_id:
            slide.visual_strategy='table' if slide.visual=='table' else 'chart';slide.visual_score=1;slide.visual_reason='Числовые данные точнее передаются нативной диаграммой или таблицей.';continue
        if slide.asset_id and not generated_asset:
            slide.visual='image';slide.visual_strategy='source_image';slide.visual_score=1;slide.visual_reason='В материалах есть связанное исходное изображение.';continue
        if re.search(r'от\s+конкурент\w*\s+код\w*\s+к\s+(?:абстракт|модел)',text,re.I):
            slide.asset_id=None;slide.visual='sequence';slide.visual_strategy='diagram';slide.visual_score=.96
            slide.visual_reason='Переход между двумя представлениями точнее показывает редактируемая схема.';continue
        if slide.role in {'cover','divider'} or index in {0,last}:
            slide.visual='none';slide.visual_strategy='none';slide.visual_score=.95;slide.visual_reason='Оформление шаблона уже выполняет визуальную функцию этого слайда.';continue
        if illustration:
            concept,score=illustration
            slide.visual='image' if generated_asset else 'none';slide.visual_strategy='generated_image';slide.visual_score=score
            slide.visual_reason=('Концептуальное различие понятнее через два наглядных сценария.' if concept=='concurrency_parallelism'
                                 else 'Физические ограничения вычислительной системы уместно показать одной цельной иллюстрацией.')
            if not allow_generated:slide.visual='none'
            continue
        score=.18
        if slide.archetype=='illustration':score+=.55
        elif slide.archetype=='example':score+=.34
        if image_words.search(text):score+=.46
        if conceptual_words.search(text):score+=.62
        if re.search(r'метафор|визуальн\w* образ|истори|сценар',text,re.I):score+=.28
        if formula_words.search(text):score-=.52
        native_kind=None
        if hierarchy_words.search(text):native_kind='hierarchy'
        elif sequence_words.search(text):native_kind='sequence'
        elif comparison_words.search(text):native_kind='list'
        if native_kind and not conceptual_words.search(text):score-=.24
        score=max(0,min(1,score))
        if score>=.68:
            slide.visual='image' if generated_asset else 'none';slide.visual_strategy='generated_image';slide.visual_score=round(score,2)
            slide.visual_reason='Слайд описывает ситуацию или концептуальное различие, которое полезно показать иллюстрацией.'
        elif native_kind:
            if generated_asset:slide.asset_id=None
            slide.visual=native_kind;slide.visual_strategy='diagram';slide.visual_score=.9
            slide.visual_reason='Связи и последовательность точнее показываются редактируемой нативной схемой.'
        elif slide.visual in {'sequence','list','hierarchy'}:
            slide.visual_strategy='diagram';slide.visual_score=.72;slide.visual_reason='Планировщик выбрал нативную схему для структурирования тезисов.'
        else:
            if generated_asset:slide.asset_id=None
            slide.visual='none';slide.visual_strategy='none';slide.visual_score=.76
            slide.visual_reason='Отдельная иллюстрация не добавляет смысла к тексту или формуле.'
        if slide.visual_strategy=='generated_image' and not allow_generated:
            # Keep the recommendation for a later image-enabled run while
            # producing a valid deck without a missing asset.
            slide.visual='none'
    return result


def visual_contract_for(slide) -> VisualContract:
    """Describe what a visual must communicate and what it must avoid."""
    layout=_diagram_layout(slide);illustration=_illustration_concept(slide) if slide.visual_strategy=='generated_image' else None
    image_presets={
        'concurrency_parallelism':(
            'Без подписей показать различие: concurrency чередует несколько задач на одном исполнителе, parallelism выполняет несколько задач одновременно.',
            ['Один вычислительный исполнитель','Несколько чередующихся потоков задач','Несколько одинаковых исполнителей','Одновременные задачи'],
            ['Левая половина показывает чередование на одном исполнителе','Правая половина показывает одновременную работу нескольких исполнителей'],
        ),
        'real_system_bottleneck':(
            'Показать, как параллельные вычислительные блоки упираются в общую память и ограниченную пропускную способность реальной системы.',
            ['Одинаковые вычислительные блоки','Симметричные уровни кэша','Общая память','Узкий канал доступа'],
            ['Параллельные потоки сходятся к общей памяти','Узкий канал создаёт видимое место насыщения'],
        ),
    }
    presets={
        'pram_reality':(
            'Сравнить идеальную PRAM с ограничениями реальной многоядерной системы.',
            ['Процессоры','Общая память','Кэши','Межсоединение','Планировщик'],
            ['В PRAM доступ считается одношаговым','В реальной системе возникают задержки и конкуренция'],
        ),
        'reduction_tree':('Показать пошаговое сокращение числа элементов.',['Входы','Пары','Результат'],['Каждый уровень объединяет пары']),
        'fork_join':('Показать разветвление и синхронизацию задач.',['Fork','Параллельные задачи','Join'],['Ветви выходят из Fork и сходятся в Join']),
    }
    concept=illustration[0] if illustration else None
    goal,entities,relations=image_presets.get(concept,presets.get(layout,(slide.visual_brief or slide.message,slide.visual_items[:3],[slide.takeaway] if slide.takeaway else [])))
    existing=getattr(slide.visual_contract,'diagram',None)
    if existing and existing.kind==layout:
        labels=[node.label.strip() for node in existing.nodes]
        generic=all(re.fullmatch(r'(?:[A-ZА-Я]\d?|\d+|[+Σ])',label,re.I) for label in labels)
        generic=generic or (layout=='fork_join' and sum(bool(re.fullmatch(r'\d+',label)) for label in labels)>=2)
        if generic:existing=None
    # Weighted graphs are accepted from the planner only when every source
    # value is traced to a claim. Otherwise use a visibly marked example.
    source_weighted=existing and existing.kind==layout and all(
        node.weight is not None and (node.origin!='source' or node.claim_ids)
        for node in existing.nodes
    )
    generic={'sequence','comparison','hierarchy','layers','memory_access','scheduler','formula_focus','abstraction','pram_reality'}
    allow_generic=bool(slide.logic_contract.main_assertion or slide.logic_contract.semantic_payload_hash)
    diagram=(None if illustration else existing if layout not in {'work_span','critical_path'} and existing and existing.kind==layout else
             existing if source_weighted else default_diagram(layout,slide.visual_items or entities) if allow_generic or layout not in generic else None)
    if diagram:
        _,failures=graph_quality(layout,slide.visual_items or entities,diagram)
        if failures:diagram=default_diagram(layout,slide.visual_items or entities)
    return VisualContract(goal=_complete_excerpt(goal,300),entities=[_complete_excerpt(v,80) for v in entities[:6]],relations=[_complete_excerpt(v,100) for v in relations[:6]],forbidden=['Неподписанные устройства','Случайные провода и блоки','Текст внутри растровой иллюстрации','Логотипы и водяные знаки'],diagram=diagram)


def _archetype(slide,index=0,count=1):
    text=(slide.title+' '+slide.message).lower()
    if slide.role=='cover' or index==0:return 'cover'
    if slide.role=='divider':return 'divider'
    if index==count-1 or re.search(r'вывод|итог|заключен|резюме',text):return 'summary'
    if re.search(r'упражнен|самопровер|домашн|(?:^|[.!?]\s+)задач[аи]\s|решите|выполните задание',text):return 'exercise'
    if slide.visual=='image':return 'illustration'
    if re.search(r'формул|теорем|доказ|границ|θ\s*\(|o\s*\(|[≤≥=]',text,re.I):return 'formula'
    if re.search(r'пример|case|сценари',text):return 'example'
    if slide.visual=='list' or re.search(r'различ|сравн|versus| vs\b|режим',text):return 'comparison'
    if slide.visual=='sequence' or re.search(r'этап|алгоритм|процесс|порядок|scheduler|планиров',text):return 'process'
    return 'explanation'


def enrich_plan(plan: PresentationPlan,content: ContentIR) -> PresentationPlan:
    """Add a source-backed editorial structure used by the layout director."""
    result=plan.model_copy(deep=True);claims={c.id:c for c in content.claims}
    for index,slide in enumerate(result.slides):
        if re.search(r'\*\*s\b|\bпараллельного\s+s\b',slide.message,re.I):
            slide.takeaway='Work и Span задают независимые нижние границы времени.'
            slide.balanced_message='Work и Span задают нижние границы: T_P ≥ W/P и T_P ≥ S.'
            slide.message='Work W измеряет весь объём задач, а Span S — длину критического пути. Поэтому время выполнения ограничено снизу: T_P ≥ W/P и T_P ≥ S.'
        slide.archetype=_archetype(slide,index,len(result.slides))
        if not slide.support_points:slide.support_points=_supporting_points(slide,claims,3)
        slide.support_points=[_clean_text(point) for point in slide.support_points[:3] if point.strip()]
        if not slide.takeaway:
            sentences=[x.strip() for x in re.split(r'(?<=[.!?])\s+',slide.message) if x.strip()]
            slide.takeaway=_clean_text(sentences[-1] if sentences else slide.message)
        else:slide.takeaway=_clean_text(slide.takeaway)
        slide.balanced_message=_clean_text(slide.balanced_message or slide.message)
        slide.visual_items=[_clean_text(item) for item in slide.visual_items[:3] if item.strip()]
        if not slide.visual_brief:
            slide.visual_brief=_complete_excerpt(slide.title+': '+slide.message,260)
        else:slide.visual_brief=_complete_excerpt(slide.visual_brief,260)
        slide.message=_clean_text(slide.message)
        slide.visual_contract=visual_contract_for(slide)
    return result


def validate_plan(plan: PresentationPlan, content: ContentIR, count: int,validate_density: bool=True):
    if plan.status=='needs_input': raise NeedsInput(plan.reason)
    if len(plan.slides)!=count: raise ValueError('Model did not preserve requested slide count')
    if len({s.id for s in plan.slides})!=count: raise ValueError('Duplicate slide IDs')
    claims={c.id for c in content.claims};claim_map={c.id:c for c in content.claims};datasets={d.id for d in content.datasets}; assets={a.id for a in content.assets}
    used=set();fingerprints=set();titles=set();messages=set();authored_messages=[]
    source_numbers=set(re.findall(r'\d+(?:[.,]\d+)?', ' '.join(c.text for c in content.claims)+ ' '.join(str(x) for d in content.datasets for v in d.series.values() for x in v)))
    for slide in plan.slides:
        if not set(slide.claim_ids)<=claims: raise ValueError('Unknown claim reference')
        if slide.dataset_id and slide.dataset_id not in datasets: raise ValueError('Unknown dataset reference')
        if slide.asset_id and slide.asset_id not in assets: raise ValueError('Unknown asset reference')
        if slide.visual in {'chart','table'} and not slide.dataset_id: raise ValueError('Numeric visuals require a source dataset')
        if slide.visual=='image' and not slide.asset_id: raise ValueError('Image visual requires an asset')
        if slide.visual_contract.diagram:
            for node in slide.visual_contract.diagram.nodes:
                if not set(node.claim_ids)<=set(slide.claim_ids):raise ValueError('Diagram node references a claim outside its slide')
                if node.weight is not None and node.origin=='source':
                    if not node.claim_ids:raise ValueError('Source diagram weights require claim_ids')
                    claim_numbers={float(value.replace(',','.')) for cid in node.claim_ids for value in re.findall(r'\d+(?:[.,]\d+)?',claim_map[cid].text)}
                    if float(node.weight) not in claim_numbers:raise ValueError('Source diagram weight is absent from referenced claims')
        normalized_title=re.sub(r'\W+',' ',slide.title.casefold()).strip()
        normalized_message=re.sub(r'\W+',' ',slide.message.casefold()).strip()
        if normalized_title in titles:raise ValueError('Plan contains duplicate slide title: '+slide.title)
        if normalized_message in messages:raise ValueError('Plan contains duplicate main idea: '+slide.title)
        if any(_near_duplicate(slide.message,previous) for previous in authored_messages):
            raise ValueError('Plan contains near-duplicate main ideas: '+slide.title)
        titles.add(normalized_title);messages.add(normalized_message)
        authored_messages.append(slide.message)
        if validate_density and slide.balanced_message:
            versions={_phrase_key(slide.takeaway),_phrase_key(slide.balanced_message),_phrase_key(slide.message)}-{''}
            if len(versions)<3:raise ValueError('Density modes repeat the same text on slide: '+slide.title)
            if not len(slide.takeaway)<len(slide.balanced_message)<len(slide.message):
                raise ValueError('Density modes do not increase semantic detail on slide: '+slide.title)
        fingerprint=(normalized_title,normalized_message)
        if fingerprint in fingerprints:raise ValueError('Plan contains duplicate slides: '+slide.title)
        fingerprints.add(fingerprint)
        visible=' '.join([slide.title,slide.message,slide.balanced_message,slide.takeaway,*slide.support_points,*slide.visual_items])
        if re.search(r'…|\.\.\.',visible):raise ValueError('Plan contains mechanically truncated text: '+slide.title)
        if re.search(r'[T𝑇]!',visible):raise ValueError('Plan contains a corrupted T_P formula on slide: '+slide.title)
        prose=[slide.message,*([slide.balanced_message] if slide.balanced_message else []),*([slide.takeaway] if slide.takeaway else []),*slide.support_points]
        if any(not _looks_finished(value) for value in prose):raise ValueError('Plan contains an unfinished sentence on slide: '+slide.title)
        # Prompts target 150/90/110 characters. These wider guardrails absorb
        # small-model counting variance while still rejecting prose that cannot
        # reasonably fit any composition. Layouts select whole authored fields;
        # they never clip a field to meet these limits.
        if len(slide.message)>220:raise ValueError(f'Plan message exceeds 220 characters on slide: {slide.title}')
        if slide.balanced_message and len(slide.balanced_message)>180:raise ValueError(f'Plan balanced message exceeds 180 characters on slide: {slide.title}')
        if slide.takeaway and len(slide.takeaway)>130:raise ValueError(f'Plan takeaway exceeds 130 characters on slide: {slide.title}')
        if any(len(point)>110 for point in slide.support_points):raise ValueError(f'Plan support point exceeds 110 characters on slide: {slide.title}')
        if any(len(item)>80 for item in slide.visual_items):raise ValueError(f'Plan visual item exceeds 80 characters on slide: {slide.title}')
        source=' '.join(claim_map[cid].text for cid in slide.claim_ids if cid in claim_map)
        unsupported=_directional_markers(visible)-_directional_markers(source)
        if unsupported:raise ValueError(f'Plan changes comparison or optimization direction on slide {slide.title}: {", ".join(sorted(unsupported))}')
        invented=set(re.findall(r'\d+(?:[.,]\d+)?',visible))-source_numbers
        if invented: raise ValueError('Plan introduces numbers absent from source: '+', '.join(sorted(invented)))
        used.update(slide.claim_ids)
    required={c.id for c in content.claims if c.required}
    if not required<=used: raise ValueError('Plan omits mandatory claims: '+', '.join(sorted(required-used)))


def disambiguate_titles(plan: PresentationPlan, existing_titles=()) -> PresentationPlan:
    """Replace repeated labels with a unique source-backed statement.

    A title can collide even when the slide messages are different. In that case
    the message itself is the safest label because it has already been checked
    against source numbers and claim references.
    """
    result=plan.model_copy(deep=True)
    used={re.sub(r'\W+',' ',value.casefold()).strip() for value in existing_titles}
    for slide in result.slides:
        normalized=re.sub(r'\W+',' ',slide.title.casefold()).strip()
        if normalized in used:
            candidates=[re.split(r'(?<=[.!?])\s+',slide.message)[0],slide.takeaway,*slide.support_points]
            replacement=next((_complete_excerpt(value,96) for value in candidates if value.strip() and re.sub(r'\W+',' ',_complete_excerpt(value,96).casefold()).strip() not in used),None)
            if replacement:slide.title=replacement;normalized=re.sub(r'\W+',' ',replacement.casefold()).strip()
        used.add(normalized)
    return result


def plan_with_model(gateway,content,request):
    import tempfile
    from pathlib import Path
    from PIL import Image,ImageDraw
    claim_lengths={c.id:len(c.text) for c in content.claims}
    required_set={c.id for c in content.claims if c.required}
    candidates=[]
    for asset in content.assets:
        if asset.purpose!='output':continue
        path=artifact_path(asset.path)
        if not path.exists():continue
        shortest=min((claim_lengths.get(cid,10_000) for cid in asset.claim_ids),default=10_000)
        candidates.append((shortest,path.stat().st_size,bool(set(asset.claim_ids)&required_set),asset,path))
    limit=max(0,int(os.environ.get('MODEL_MAX_SOURCE_IMAGES','4')))
    selected=sorted(candidates,key=lambda item:(item[0]>=80,-item[1],item[3].id))[:limit]
    catalog_limit=max(limit,max(0,int(os.environ.get('MODEL_MAX_PLANNING_ASSETS','8'))))
    catalog=sorted(candidates,key=lambda item:(not item[2],-item[1],item[3].id))[:catalog_limit]
    planning_content=content.model_copy(deep=True)
    selected_claims=planning_claims(content,request.brief,request.slide_count)
    selected_ids={claim.id for claim in selected_claims}
    # Source order gives the planner a stable narrative spine. Ranking is used
    # only to choose what fits in the bounded context.
    planning_content.claims=[claim.model_copy(update={'text':_planning_text(claim.text)}) for claim in content.claims if claim.id in selected_ids]
    planning_assets={item[3].id:item[3] for item in catalog+selected}
    planning_content.assets=list(planning_assets.values())
    required=[c.id for c in content.claims if c.required]
    from .logic import curriculum_checkpoints
    checkpoints=curriculum_checkpoints(content,request.slide_count)
    payload={'content':planning_content.model_dump(),'brief':request.brief,'purpose':request.purpose,'slide_count':request.slide_count,'required_claim_ids':required,'curriculum_checkpoints':checkpoints,'source_image_order':[item[3].id for item in selected]}
    with tempfile.TemporaryDirectory(prefix='vktech-source-') as temp:
        model_images=[]
        if selected:
            width,height,cols=320,200,2;rows=(len(selected)+cols-1)//cols
            sheet=Image.new('RGB',(width*cols,height*rows),'white');draw=ImageDraw.Draw(sheet)
            for index,item in enumerate(selected):
                with Image.open(item[4]) as source:
                    thumb=source.convert('RGB');thumb.thumbnail((width-10,height-30),Image.Resampling.LANCZOS)
                x=(index%cols)*width;y=(index//cols)*height
                sheet.paste(thumb,(x+(width-thumb.width)//2,y+25));draw.text((x+5,y+5),item[3].id,fill='black')
            contact=Path(temp)/'source-assets.png';sheet.save(contact,'PNG',optimize=True);model_images=[contact]
            payload['visual_input']='One contact sheet. Labels are exact asset_id values from source_image_order.'
        def request_plan(batch_payload,batch_content,batch_count,existing_titles=(),existing_messages=(),assignments=()):
            error=None
            for attempt in range(3):
                plan=fit_semantic_variants(normalize_plan(gateway.structured('planning',batch_payload,PresentationPlan,images=model_images)))
                # IDs are transport metadata, not authored content. Providers
                # can repeat them even when every slide is distinct.
                # Assign deterministic batch IDs here; global IDs are assigned
                # again when the source-ordered batches are combined.
                for local_index,slide in enumerate(plan.slides):slide.id=f'slide-{local_index+1}'
                plan=disambiguate_titles(plan,existing_titles)
                try:
                    validate_plan(plan,batch_content,batch_count)
                    if assignments:
                        for slide,assignment in zip(plan.slides,assignments):
                            if set(slide.claim_ids)!=set(assignment['claim_ids']):
                                raise ValueError(f"Slide position {assignment['position']} must use exactly these claim_ids: {', '.join(assignment['claim_ids'])}")
                            if not slide.balanced_message:
                                raise ValueError(f"Slide position {assignment['position']} must contain balanced_message")
                            focus=assignment.get('focus_terms',[])
                            authored=' '.join([slide.title,slide.takeaway,slide.balanced_message,slide.message])
                            if focus and not _contains_focus(authored,focus):
                                # Exact claim assignment is authoritative. TF/IDF
                                # focus terms are only a weak lexical hint and can
                                # miss valid paraphrases or surface incidental
                                # words from OCR/transcript furniture.
                                log.warning('Slide position %s uses assigned claims without a lexical focus-term match',assignment['position'])
                    forbidden_titles={re.sub(r'\W+',' ',value.casefold()).strip() for value in existing_titles}
                    forbidden_messages={re.sub(r'\W+',' ',value.casefold()).strip() for value in existing_messages}
                    conflict=next((slide.title for slide in plan.slides if re.sub(r'\W+',' ',slide.title.casefold()).strip() in forbidden_titles),None)
                    if conflict:raise ValueError('Plan repeats a title from an earlier segment: '+conflict)
                    conflict=next((slide.title for slide in plan.slides if re.sub(r'\W+',' ',slide.message.casefold()).strip() in forbidden_messages),None)
                    if conflict:raise ValueError('Plan repeats a main idea from an earlier segment: '+conflict)
                    conflict=next((slide.title for slide in plan.slides if any(_near_duplicate(slide.message,previous) for previous in existing_messages)),None)
                    if conflict:raise ValueError('Plan repeats a near-duplicate idea from an earlier segment: '+conflict)
                    return plan
                except NeedsInput:
                    raise
                except ValueError as exc:
                    error=exc
                    log.warning('Planning validation attempt %s failed: %s',attempt+1,exc)
                    if attempt<2:
                        batch_payload['validation_feedback']=str(exc)
                        batch_payload['correction_attempt']=attempt+2
                        batch_payload['previous_slide_outline']=[
                            {'position':index+1,'title':slide.title,'message':slide.message,'claim_ids':slide.claim_ids}
                            for index,slide in enumerate(plan.slides)
                        ]
                        batch_payload['correction']='Return a complete corrected plan. Fix validation_feedback exactly. Use each slide_assignment focus_terms in takeaway, balanced_message and message. Preserve comparison direction and optimization meaning. Make the three density texts independently complete and visibly different. Rewrite every overlong field as a shorter complete sentence; never cut a word or clause. Replace every repeated or near-duplicate title or main idea with a distinct source-backed teaching step. Every required_claim_id must occur in at least one slide.claim_ids.'
            raise error

        # Large JSON plans are less stable as one response. Plan long decks in
        # coherent source-ordered segments, then validate the combined story.
        if request.slide_count>12:
            batch_total=(request.slide_count+5)//6
            base=request.slide_count//batch_total;extra=request.slide_count%batch_total
            counts=[base+(index<extra) for index in range(batch_total)]
            claims=planning_content.claims;combined=[];existing_titles=[];existing_messages=[]
            claim_groups=[]
            if len(claims)>=request.slide_count:
                for position in range(request.slide_count):
                    start=len(claims)*position//request.slide_count;end=len(claims)*(position+1)//request.slide_count
                    claim_groups.append(claims[start:end])
            for index,batch_count in enumerate(counts):
                first=len(combined);last=first+batch_count
                if claim_groups:
                    groups=claim_groups[first:last];batch_claims=[claim for group in groups for claim in group]
                    assignments=[]
                    for local,group in enumerate(groups):
                        group_ids={claim.id for claim in group}
                        assignments.append({'position':first+local+1,'claim_ids':[claim.id for claim in group],'focus_terms':_focus_terms(group,claims),
                            'checkpoint_ids':[checkpoint['id'] for checkpoint in checkpoints if group_ids.intersection(checkpoint['claim_ids'])]})
                else:
                    start=len(claims)*index//batch_total;end=len(claims)*(index+1)//batch_total
                    batch_claims=claims[start:end];assignments=[]
                batch_content=planning_content.model_copy(deep=True);batch_content.claims=batch_claims
                batch_required=[claim.id for claim in batch_content.claims if claim.required]
                checkpoint_ids={item for assignment in assignments for item in assignment.get('checkpoint_ids',[])}
                batch_payload={**payload,'content':batch_content.model_dump(),'slide_count':batch_count,'required_claim_ids':batch_required,
                    'curriculum_checkpoints':[checkpoint for checkpoint in checkpoints if checkpoint['id'] in checkpoint_ids],
                    'plan_segment':{'index':index+1,'total':batch_total,'first_position':first+1,'last_position':last,'existing_titles':existing_titles},
                    'slide_assignments':assignments}
                batch=request_plan(batch_payload,batch_content,batch_count,existing_titles,existing_messages,assignments)
                for slide in batch.slides:
                    slide.id=f'slide-{len(combined)+1}';combined.append(slide)
                existing_titles.extend(slide.title for slide in batch.slides)
                existing_messages.extend(slide.message for slide in batch.slides)
            plan=fit_titles(fit_semantic_variants(normalize_plan(PresentationPlan(slides=combined))))
            validate_plan(plan,content,request.slide_count)
            final=fit_titles(fit_semantic_variants(enrich_plan(plan,content)))
            validate_plan(final,content,request.slide_count)
            return final

        plan=request_plan(payload,content,request.slide_count)
        return fit_titles(enrich_plan(plan,content))


def regenerate_slide_with_model(gateway,slide,content,instruction):
    claims={c.id:c for c in content.claims};selected=[claims[cid] for cid in slide.claim_ids if cid in claims]
    payload={'instruction':instruction,'current_slide':slide.model_dump(),'claims':[c.model_dump() for c in selected]}
    revision=gateway.structured('regenerate_slide',payload,SlideRevision)
    source_numbers=set(re.findall(r'\d+(?:[.,]\d+)?',' '.join(c.text for c in selected)))
    visible=' '.join([revision.title,revision.message,revision.balanced_message,revision.takeaway,*revision.support_points,*revision.visual_items])
    if re.search(r'…|\.\.\.',visible):raise ValueError('Slide regeneration returned mechanically truncated text')
    if any(not _complete_sentence(value) for value in [revision.message,revision.balanced_message,revision.takeaway,*revision.support_points]):
        raise ValueError('Slide regeneration returned an unfinished sentence')
    if len(revision.message)>220 or len(revision.balanced_message)>180 or len(revision.takeaway)>130 or any(len(point)>110 for point in revision.support_points) or any(len(item)>80 for item in revision.visual_items):
        raise ValueError('Slide regeneration returned text that does not fit the selected density modes')
    if len({_phrase_key(revision.takeaway),_phrase_key(revision.balanced_message),_phrase_key(revision.message)}-{''})<3:
        raise ValueError('Slide regeneration repeated text across density modes')
    source=' '.join(c.text for c in selected)
    unsupported=_directional_markers(visible)-_directional_markers(source)
    if unsupported:raise ValueError('Slide regeneration changed comparison or optimization direction')
    invented=set(re.findall(r'\d+(?:[.,]\d+)?',visible))-source_numbers
    if invented:raise ValueError('Slide regeneration introduces numbers absent from source: '+', '.join(sorted(invented)))
    result=slide.model_copy(deep=True)
    for field in ('title','message','balanced_message','support_points','takeaway','visual_items','visual','archetype','visual_brief'):
        setattr(result,field,getattr(revision,field))
    return result


def usable_prototypes(design):
    candidates=[]
    instruction=re.compile(r'правила|инструкция|типограф|палитр|используйте|рекоменду|макет|как использовать',re.I)
    for p in design.prototypes:
        title=next((s for s in p.slots if s.role=='title'),None)
        bodies=body_slots(p)
        # A one-line 20 pt title on a widescreen slide can be less than 6%
        # of its height. Judge the inherited slot in points.
        title_height_pt=title.box.h*design.height/12700 if title else 0
        if title and title.box.w>.25 and title_height_pt>=title.style.size and bodies:
            visual_area=sum(s.box.w*s.box.h for s in p.slots if s.role=='visual')
            central_decor=sum(s.box.w*s.box.h for s in p.slots if s.role=='decor' and s.box.y<.9 and s.box.w*s.box.h>.01)
            score=max(s.box.w*s.box.h for s in bodies)+.12*sum(s.box.w*s.box.h for s in bodies)
            score-=.7*visual_area+.5*central_decor
            if visual_area>.04 or central_decor>.04:score-=1
            score+=.18 if len(bodies)<=2 else 0
            score+=.12 if min(s.style.size for s in bodies)>=16 else 0
            # A narrow title usually belongs to a split layout whose right
            # half contains an inherited photo or another authored visual.
            # The generic composer uses the slide as a full-width canvas, so
            # rank these layouts behind ordinary wide-title content slides.
            score-=.6 if title.box.w<.4 else 0
            score-=.8 if instruction.search(' '.join(s.source_text[:180] for s in p.slots if s.role=='title')) else 0
            score-=.02*len(bodies)
            score-=1 if any(re.search(r'\bpadding\b|\bmargin\b|\bbody\s*\{',s.source_text) for s in bodies) else 0
            candidates.append((score,p))
    if not candidates:
        raise NeedsInput('В PPTX не найден слайд с пригодными областями заголовка и основного текста. '
                         'Добавьте слайд «Заголовок и содержимое»; текстовые заполнители могут быть пустыми.')
    return [p for _,p in sorted(candidates,key=lambda x:(-x[0],x[1].id))]


def branded_prototypes(design):
    """Return sparse coloured layouts whose master graphics can frame new content."""
    instruction=re.compile(r'правила|инструкция|типограф|палитр|используйте|рекоменду|макет|как использовать',re.I)
    candidates=[]
    for p in design.prototypes:
        title=next((s for s in p.slots if s.role=='title'),None)
        if not title or p.background.upper() in {'FFFFFF','FEFEFE'}:continue
        bodies=[s for s in p.slots if s.role=='body']
        visual=max((s.box.w*s.box.h for s in p.slots if s.role=='visual'),default=0)
        body=max((s.box.w*s.box.h for s in bodies),default=0)
        if visual>.09 or body>.07:continue
        if instruction.search(' '.join(s.source_text[:180] for s in p.slots if s.role=='title')):continue
        score=title.box.w*title.box.h-.5*visual-.35*body
        candidates.append((score,p))
    return [p for _,p in sorted(candidates,key=lambda x:(-x[0],x[1].id))]


def body_slots(p):
    return [s for s in p.slots if s.role=='body' and s.box.w>.25 and s.box.h>.1 and 12<=s.style.size<=96]


def template_text_right(prototype):
    """Infer the right edge of a cover text column from a narrow left placeholder.

    Many branded layouts keep a large decorative image on the right while their
    empty subtitle placeholder marks the safe text column on the left.
    """
    candidates=[s.box.x+s.box.w for s in prototype.slots if s.role in {'body','visual'} and s.box.x<.25 and s.box.y>.28 and .24<s.box.w<.62 and s.box.x+s.box.w<.72]
    return min(candidates) if candidates else None


def _scale_size(design, minimum, preferred):
    valid=sorted(s for s in design.font_sizes if s>=minimum and s<=preferred)
    return min(valid,key=lambda s:abs(s-preferred)) if valid else preferred


def _readable_color(color,background,design,threshold=4.5):
    from .audit import contrast
    candidates=list(dict.fromkeys([color,*design.palette,'202020','FFFFFF']))
    valid=[c for c in candidates if isinstance(c,str) and re.fullmatch(r'[0-9A-Fa-f]{6}',c)]
    best=max(valid,key=lambda c:contrast(c,background),default='202020')
    return color if re.fullmatch(r'[0-9A-Fa-f]{6}',color or '') and contrast(color,background)>=threshold else best


def _style(base,design,background,role='body',align='left'):
    result=base.model_copy(deep=True);result.align=align;result.fill=None
    from .audit import font_for
    fallback=os.environ.get('FONT_FALLBACK','Arial')
    if font_for(result.font,18) is None and font_for(fallback,18) is not None:result.font=fallback
    result.size=_scale_size(design,28,34 if role=='title' else 20) if role=='title' else _scale_size(design,18,20)
    result.bold=role=='title';result.color=_readable_color(result.color,background,design,3 if role=='title' else 4.5)
    return result


def _clean_text(value):
    value=re.sub(r'\*{2,}|`+','',value)
    # PDF extraction can splice an isolated CJK glyph into a Russian token
    # (for example «В材»). Remove only such mixed-script tokens.
    if re.search(r'[А-Яа-яЁё]',value) and re.search(r'[\u3400-\u9fff]',value):
        value=re.sub(r'(?<!\S)[\w-]*[\u3400-\u9fff][\w-]*[.,;:]?', '', value)
    value=re.sub(r'\s+',' ',value).strip(' •–—-')
    return re.sub(r'(^|\s)[.,;:]+(?=\s|$)',r'\1',value).strip()


def _complete_excerpt(value,limit=105):
    """Select complete sentences or clauses without clipping characters."""
    value=_clean_text(value)
    if len(value)<=limit:return value
    units=[part.strip() for part in re.split(r'(?<=[.!?;])\s+|\s+[—–]\s+',value) if part.strip()]
    chosen=[]
    for unit in units:
        candidate=' '.join(chosen+[unit])
        if chosen and len(candidate)>limit:break
        chosen.append(unit)
        if len(candidate)>=limit:break
    return ' '.join(chosen) if chosen else value


def _semantic_lead(slide,variant):
    """Choose an authored complete thought for each density mode."""
    if variant=='A':
        candidates=[slide.takeaway,*slide.support_points,slide.message]
        return _clean_text(next((value for value in candidates if value and value.strip()),slide.message))
    if variant=='B':
        candidates=[slide.balanced_message,slide.takeaway,slide.message]
        return _clean_text(next((value for value in candidates if value and value.strip()),slide.message))
    return _clean_text(slide.message)


def _diagram_items(ps,claims):
    def label(value):
        value=_clean_text(value).rstrip(' .;:')
        if len(value)<=52:return value
        parts=[_clean_text(part).rstrip(' .;:') for part in re.split(r'[:;,.]|\s+[—–]\s+',value)]
        candidate=next((part for part in parts if 12<=len(part)<=52),None)
        if candidate:return candidate
        words=value.split();chosen=[]
        for word in words:
            if chosen and len(' '.join(chosen+[word]))>52:break
            chosen.append(word)
        while chosen and chosen[-1].casefold() in {'и','в','на','для','с','к','по','из','от'}:chosen.pop()
        return ' '.join(chosen) or value
    if ps.visual_items:return [label(item) for item in ps.visual_items[:3] if item.strip()]
    query=set(re.findall(r'[\w+#<>]{4,}',(ps.title+' '+ps.message).lower()))
    message_parts=[x for x in re.split(r'\n+|(?<=[.!?;])\s+|\s+[—–]\s+',ps.message) if x.strip()]
    primary=([ps.title]+message_parts if ps.visual=='hierarchy' else message_parts)
    source=[]
    for cid in ps.claim_ids:
        claim=claims.get(cid)
        if not claim:continue
        source.extend(x for x in re.split(r'\n+|(?<=[.!?;])\s+',claim.text) if x.strip() and not re.search(r'^(ответ|вопрос|решите|домашн|самопровер|конспект лекции|параллельные алгоритмы\s*\|)|^\d+[.)]?$|\b\d{1,2}:\d{2}\b',x.strip(),re.I))
    def cleaned(values):
        result=[]
        for text in values:
            text=_complete_excerpt(text)
            if re.search(r'…|\.\.\.',text):continue
            if text and not re.fullmatch(r'\d+[.)]?',text) and text.casefold() not in {x.casefold() for x in result}:result.append(text)
        return result
    chosen=cleaned(primary)
    extras=[]
    for text in cleaned(source):
        words=set(re.findall(r'[\w+#<>]{4,}',text.lower()));score=len(words&query)
        if score:extras.append((-score,len(text),text))
    for _,_,text in sorted(extras):
        if text.casefold() not in {x.casefold() for x in chosen}:chosen.append(text)
        if len(chosen)>=4:break
    unique=[]
    for text in chosen:
        text=_complete_excerpt(text)
        if text and not re.fullmatch(r'\d+[.)]?',text) and text.casefold() not in {x.casefold() for x in unique}:unique.append(text)
    def rank(text):
        words=set(re.findall(r'[\w+#<>]{4,}',text.lower()))
        return (-len(words&query),len(text))
    items=unique[:4] if len(unique)>=2 else sorted(unique,key=rank)[:4]
    if len(items)<2:items=[_complete_excerpt(ps.title),_complete_excerpt(ps.message)]
    return [label(item) for item in items]


def _diagram_layout(ps) -> str:
    """Select a semantic diagram grammar instead of a generic card stack."""
    title=ps.title.lower();text=' '.join((ps.title,ps.message,ps.visual_brief)).lower()
    groups=sum(bool(re.search(pattern,title)) for pattern in (r'broadcast',r'редукц|reduction',r'scheduler|планиров'))
    if groups>=2:return 'comparison'
    if re.search(r'от\s+конкурент\w*\s+код\w*\s+к\s+(?:абстракт|модел)',title):return 'abstraction'
    if re.search(r'^практическ\w*\s+реализац\w*\s+и\s+ограничен\w*\s+pram',title):return 'pram_reality'
    if re.search(r'редукц|reduction|бинарн\w* дерев',title):return 'reduction_tree'
    if re.search(r'erew|crew|crcw|режим\w* доступ|set\s*\(',title):return 'memory_access'
    if re.search(r'fork.?join|разветв\w*.*объедин',title) and 'broadcast' not in title:return 'fork_join'
    if re.search(r'2(?:-аппроксимац|[×x])|level.by.level.+доказ',title):return 'level_bound_proof'
    if re.search(r'level.by.level|выполнен\w*\s+по\s+уровн',title):return 'level_schedule'
    if re.search(r'work\s+и\s+span',title):return 'work_span'
    if re.search(r'critical path|критическ\w* путь',title):return 'critical_path'
    if re.search(r'формул|неравен|верхн\w* границ|нижн\w* границ',title):return 'formula_focus'
    if re.search(r'scheduler|планиров|work stealing|очеред',title):return 'scheduler'
    if re.search(r'разделен\w* сло|три слоя|уровн\w* абстракц',text):return 'layers'
    if ps.archetype=='comparison' or re.search(r'сравн|различ|разн\w* свойств|trade.?off|ограничен',title):return 'comparison'
    if ps.archetype=='process' or ps.visual=='sequence':return 'sequence'
    return ps.visual


def _supporting_points(ps,claims,limit=3):
    """Select concise source-backed details which add to, rather than repeat, the lead."""
    message_words=set(re.findall(r'[\w+#<>]{4,}',ps.message.lower()))
    query=set(re.findall(r'[\w+#<>]{4,}',(ps.title+' '+ps.message).lower()))
    found=[]
    for cid in ps.claim_ids:
        claim=claims.get(cid)
        if not claim:continue
        for part in re.split(r'\n+|(?<=[.!?;])\s+',claim.text):
            part=re.sub(r'^\s*[•–—*-]\s*','',part).strip()
            if not part or re.search(r'^(ответ|вопрос|решите|домашн|самопровер|конспект лекции|параллельные алгоритмы\s*\|)|^\d+[.)]?$|\b\d{1,2}:\d{2}\b',part,re.I):continue
            if re.search(r'…|\.\.\.',part):continue
            words=set(re.findall(r'[\w+#<>]{4,}',part.lower()))
            if not words:continue
            overlap=len(words&message_words)/max(1,len(words))
            if overlap>.72:continue
            found.append((-len(words&query),len(part),_complete_excerpt(part,105)))
    result=[]
    for _,_,text in sorted(found):
        if text.casefold() not in {x.casefold() for x in result}:result.append(text)
        if len(result)>=limit:break
    return result


def _dark_background(color):
    from .audit import contrast
    return contrast('FFFFFF',color)>=3.2


def fit_node(node,design,minimum=None):
    """Choose a size from the template scale using measured or conservative metrics.

    Missing fonts remain unknown in the audit. Fallback estimates only guide layout.
    """
    from .audit import font_for
    from PIL import ImageFont
    from .settings import ROOT
    minimum=minimum if minimum is not None else 28 if node.role=='title' else 18
    sizes={node.style.size}|{s for s in design.font_sizes if minimum<=s<node.style.size}|{float(minimum)}
    for size in sorted((s for s in sizes if s<=node.style.size),reverse=True):
        font=font_for(node.style.font,max(1,round(size*96/72)))
        if font is None:
            font=ImageFont.load_default(size=max(1,round(size*96/72)))
        width=node.box.w*design.width/914400*96;lines=0
        for paragraph in node.text.split('\n'):
            current='';lines+=1
            for word in paragraph.split():
                candidate=(current+' '+word).strip()
                if font.getlength(candidate)>width and current:lines+=1;current=word
                else:current=candidate
        height=lines*size*96/72*1.25/(design.height/914400*96)
        if height<=node.box.h:
            node.style.size=size;return
    raise NeedsInput(f'Content does not fit the template slot on {node.id}; reduce text or change composition.')


def protect_text_from_template_decor(scene: SceneIR,design: DesignIR) -> SceneIR:
    """Keep cover text out of decorative template images with matching colors."""
    result=scene.model_copy(deep=True);prototypes={p.id:p for p in design.prototypes}
    for slide in result.slides:
        prototype=prototypes.get(slide.prototype_id);right=template_text_right(prototype) if prototype else None
        title=next((node for node in slide.nodes if node.role=='title'),None)
        body=next((node for node in slide.nodes if node.role=='body'),None)
        cover_like=title is not None and body is not None and title.box.y>=.14 and body.box.y>=.5
        if right is None or not cover_like:continue
        title.box.h=max(title.box.h,.40)
        body.box.y=max(body.box.y,.62);body.box.h=min(body.box.h,.27)
        for node in (title,body):
            width=right-node.box.x-.012
            if width>.25 and node.box.x+node.box.w>right-.005:
                node.box.w=width;fit_node(node,design,24 if node.role=='title' else 16)
    return result


def build_scenes(design: DesignIR, content: ContentIR, plan: PresentationPlan, job_id: str):
    plain=usable_prototypes(design)
    branded=branded_prototypes(design)
    dark=[p for p in branded if _dark_background(p.background)]
    light=[p for p in branded if not _dark_background(p.background)]
    content_light=[p for p in light if next((s.box.y for s in p.slots if s.role=='title'),1)<.2]
    claims={c.id:c for c in content.claims};datasets={d.id:d for d in content.datasets};assets={a.id:a for a in content.assets}
    style_proto=plain[0]
    style_title=next(s for s in style_proto.slots if s.role=='title')
    style_body=max(body_slots(style_proto),key=lambda s:s.box.w*s.box.h).style
    palette_override=design.evidence.get('palette_override',{})
    surface=palette_override.get('surface') or ('EBF3F9' if 'EBF3F9' in design.palette else 'E8EEF6')
    accent=palette_override.get('accent') or next((c for c in ('0077FF','2688EB','005FF9') if c in design.palette),'0077FF')
    scenes=[]
    for vi,variant in enumerate(('A','B','C')):
        slides=[]
        for si,ps in enumerate(plan.slides):
            visual='table' if ps.dataset_id and variant=='C' else 'chart' if ps.dataset_id else ps.visual
            diagram_layout=_diagram_layout(ps)
            semantic_mode=bool(ps.logic_contract.semantic_payload_hash)
            semantic_diagram=semantic_mode and ps.visual_strategy=='diagram'
            is_cover=ps.role=='cover' or si==0
            last=si==len(plan.slides)-1
            if (is_cover or last) and dark:
                p=dark[(si+vi)%len(dark)]
            elif variant=='A' and dark and (ps.role=='divider' or (si%6==4 and visual=='none')):
                # The concise mode doubles as an editorial emphasis layout. A
                # periodic branded background keeps long decks from becoming a
                # sequence of nearly identical pale pages.
                p=dark[(si//5+vi)%len(dark)]
            elif variant=='C' or (variant=='B' and visual!='none'):
                p=plain[(si+vi)%min(3,len(plain))]
            elif ps.role=='divider' and (light or dark):
                pool=light or dark;p=pool[(si+vi)%len(pool)]
            elif visual=='none' and (content_light or light):
                pool=content_light or light;p=pool[(si+vi)%len(pool)]
            else:
                p=plain[(si+vi)%min(3,len(plain))]
            title_style=_style(style_title.style,design,p.background,'title')
            body_style=_style(style_body,design,p.background)
            dark_slide=_dark_background(p.background)
            slide_accent='FFFFFF' if dark_slide else accent
            if dark_slide:title_style.color=body_style.color='FFFFFF'
            supports=ps.support_points or _supporting_points(ps,claims,3)
            lead_text=_semantic_lead(ps,variant)
            lead_key=_phrase_key(lead_text)
            supports=[text for text in supports if _phrase_key(text) not in lead_key and not _near_duplicate(text,lead_text)]
            nodes=[]
            if is_cover or last:
                title_size={'A':48,'B':42,'C':36}[variant];body_size={'A':24,'B':22,'C':18}[variant]
                title_style.size=_scale_size(design,32,title_size);body_style.size=_scale_size(design,18,body_size)
                title_style.align=body_style.align='left';body_style.bold=True
                if _dark_background(p.background):title_style.color=body_style.color='FFFFFF'
                safe_right=template_text_right(p) if p in branded else None
                cover_w=max(.25,safe_right-.06-.012) if safe_right else .58 if p in branded else .88
                nodes.append(Node(id=f'{ps.id}-title',kind='text',role='title',box=Box(x=.06,y=.18,w=cover_w,h=.40),style=title_style,text=ps.title))
                nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.06,y=.62,w=cover_w,h=.27),style=body_style,text=lead_text,claim_ids=ps.claim_ids))
            else:
                branded_slide=p in branded
                safe_right=template_text_right(p) if branded_slide else None
                content_right=min(.70,max(.465,safe_right or .70)-.012) if branded_slide else .94
                title_h=.19 if len(ps.title)<=50 else .28
                title_style.size=_scale_size(design,28,{'A':38,'B':34,'C':32}[variant])
                nodes.append(Node(id=f'{ps.id}-title',kind='text',role='title',box=Box(x=.06,y=.07,w=content_right-.06,h=title_h),style=title_style,text=ps.title))
                nodes.append(Node(id=f'{ps.id}-accent',kind='text',role='accent',box=Box(x=.06,y=.07+title_h+.01,w=.10,h=.012),style=Style(font=body_style.font,size=18,color=slide_accent,fill=slide_accent),text=''))
                top=.07+title_h+.07
                if visual!='none':
                    if variant=='A':
                        lead=body_style.model_copy(deep=True);lead.size=_scale_size(design,22,28)
                        if visual=='image':
                            nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.06,y=top,w=.42,h=max(.34,.84-top)),style=lead,text=lead_text,claim_ids=ps.claim_ids))
                            vb=Box(x=.53,y=top,w=.41,h=max(.42,.88-top))
                        else:
                            nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.06,y=top,w=content_right-.06,h=.20),style=lead,text=lead_text,claim_ids=ps.claim_ids))
                            vb=Box(x=.08,y=top+.23,w=content_right-.10,h=max(.30,.88-(top+.23)))
                    elif variant=='B':
                        lead=body_style.model_copy(deep=True);lead.size=_scale_size(design,20,22)
                        left=.37 if branded_slide else .41
                        if semantic_diagram:
                            nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.06,y=top,w=content_right-.07,h=.17),style=lead,text=lead_text,claim_ids=ps.claim_ids))
                            vb=Box(x=.07,y=top+.20,w=content_right-.09,h=max(.34,.90-(top+.20)))
                        else:
                            nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.06,y=top,w=left,h=.27),style=lead,text=lead_text,claim_ids=ps.claim_ids))
                            vb=Box(x=.50,y=top,w=content_right-.50,h=.58)
                        if supports and not semantic_diagram:
                            support_style=body_style.model_copy(deep=True);support_style.size=_scale_size(design,18,18)
                            support_top=top+.31
                            support_h=min(.28,max(.12,.90-support_top))
                            nodes.append(Node(id=f'{ps.id}-support-1',kind='text',role='support',box=Box(x=.06,y=support_top,w=left,h=support_h),style=support_style,text=supports[0],claim_ids=ps.claim_ids))
                    else:
                        lead=body_style.model_copy(deep=True);lead.size=_scale_size(design,18,18)
                        lead_width=content_right-.07 if semantic_diagram else .46
                        lead_height=.17 if semantic_diagram else .25
                        nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.06,y=top,w=lead_width,h=lead_height),style=lead,text=lead_text,claim_ids=ps.claim_ids))
                        support_style=body_style.model_copy(deep=True);support_style.size=_scale_size(design,18,18)
                        visible_supports=supports[:2] if sum(map(len,supports[:2]))<=120 else supports[:1]
                        support_top=top+.29;available=max(.11,.90-support_top)
                        support_h=min(.22,(available-.02*max(0,len(visible_supports)-1))/max(1,len(visible_supports)))
                        for idx,text in enumerate(visible_supports if not semantic_diagram else []):
                            nodes.append(Node(id=f'{ps.id}-support-{idx+1}',kind='text',role='support',box=Box(x=.06,y=support_top+idx*(support_h+.02),w=.46,h=support_h),style=support_style,text=text,claim_ids=ps.claim_ids))
                        vb=Box(x=.07,y=top+.20,w=content_right-.09,h=max(.34,.90-(top+.20))) if semantic_diagram else Box(x=.56,y=top,w=.38,h=.50)
                else:
                    lead_w=content_right-.07
                    if variant=='A':
                        lead=body_style.model_copy(deep=True);lead.size=_scale_size(design,24,32)
                        lead.align='left'
                        nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.06,y=top,w=lead_w,h=max(.40,.82-top)),style=lead,text=lead_text,claim_ids=ps.claim_ids))
                    elif variant=='B':
                        lead=body_style.model_copy(deep=True);lead.size=_scale_size(design,20,22)
                        nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.06,y=top,w=lead_w,h=.24),style=lead,text=lead_text,claim_ids=ps.claim_ids))
                        if supports:
                            support_style=body_style.model_copy(deep=True);support_style.size=_scale_size(design,18,18)
                            cols=min(2,len(supports));col_w=(lead_w-.05)/cols
                            for idx,text in enumerate(supports[:2]):
                                x=.06+idx*(col_w+.05)
                                nodes.append(Node(id=f'{ps.id}-support-accent-{idx+1}',kind='text',role='accent',box=Box(x=x,y=top+.30,w=.055,h=.009),style=Style(font=body_style.font,size=18,color=slide_accent,fill=slide_accent),text=''))
                                nodes.append(Node(id=f'{ps.id}-support-{idx+1}',kind='text',role='support',box=Box(x=x,y=top+.34,w=col_w,h=.31),style=support_style,text=text,claim_ids=ps.claim_ids))
                    else:
                        lead=body_style.model_copy(deep=True);lead.size=_scale_size(design,18,18)
                        nodes.append(Node(id=f'{ps.id}-body-1',kind='text',role='body',box=Box(x=.06,y=top,w=lead_w,h=.15),style=lead,text=lead_text,claim_ids=ps.claim_ids))
                        support_style=body_style.model_copy(deep=True);support_style.size=_scale_size(design,18,18)
                        details=supports[:3] or ([ps.takeaway] if ps.takeaway else [])
                        cols=min(2,max(1,len(details)));col_w=(lead_w-.04*(cols-1))/cols
                        for idx,text in enumerate(details):
                            if idx<2:
                                x=.06+idx*(col_w+.04);y=top+.22;w=col_w;h=.22;accent_y=top+.19
                            else:
                                x=.06;y=top+.49;w=lead_w;h=.13;accent_y=top+.46
                            nodes.append(Node(id=f'{ps.id}-support-accent-{idx+1}',kind='text',role='accent',box=Box(x=x,y=accent_y,w=.045,h=.008),style=Style(font=body_style.font,size=18,color=slide_accent,fill=slide_accent),text=''))
                            nodes.append(Node(id=f'{ps.id}-support-{idx+1}',kind='text',role='support',box=Box(x=x,y=y,w=w,h=h),style=support_style,text=text,claim_ids=ps.claim_ids))
            if visual!='none' and not is_cover and not last:
                kind='diagram' if visual in {'sequence','list','hierarchy'} else visual
                # Density changes prose and composition, never the entities or
                # relations asserted by a visual.
                items=_diagram_items(ps,claims) if semantic_mode else _diagram_items(ps,claims)[:{'A':2,'B':3,'C':3}[variant]]
                data=datasets[ps.dataset_id].model_dump() if ps.dataset_id else assets[ps.asset_id].model_dump() if ps.asset_id else {'layout':diagram_layout,'items':items,'accent':slide_accent,'surface':surface,'graph':ps.visual_contract.diagram.model_dump() if ps.visual_contract.diagram else None}
                visualstyle=_style(style_body,design,p.background);visualstyle.size=_scale_size(design,20,22)
                visualstyle.fill=surface
                nodes.append(Node(id=f'{ps.id}-visual',kind=kind,role='visual',box=vb,style=visualstyle,data=data,claim_ids=ps.claim_ids if kind=='diagram' else []))
            for node in nodes:
                visible_parts=[node.text] if node.kind=='text' else list(node.data.get('items',[])) if node.kind=='diagram' else []
                if any(re.search(r'…|\.\.\.',str(value)) for value in visible_parts):
                    raise NeedsInput(f'Слайд «{ps.title}» содержит незавершённый текст. Перегенерируйте содержание слайда.')
                if node.kind=='text' and node.text.strip():fit_node(node,design,24 if (is_cover or last) and node.role=='title' else 16 if (is_cover or last) else None)
            slides.append(SceneSlide(id=ps.id,title=ps.title,role=ps.role,prototype_id=p.id,background=p.background,nodes=nodes))
        scenes.append(SceneIR(id=f'{job_id}-{variant}',variant=variant,template_id=design.id,content_id=content.id,width=design.width,height=design.height,slides=slides))
    return scenes
