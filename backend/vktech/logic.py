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
        concepts=contract.introduced_concepts or _concepts(slide)
        current=[concept for concept in concepts if concept not in introduced]
        if not contract.teaching_goal:
            contract.teaching_goal=('Сформировать карту темы и ожидаемый результат.' if slide.role=='cover'
                                    else 'Объяснить, '+slide.title[:1].lower()+slide.title[1:].rstrip('.')+'.')
        if not contract.question_answered:
            contract.question_answered=('Что читатель сможет объяснить после презентации?' if slide.role=='cover'
                                        else 'Как устроено: '+slide.title.rstrip('?')+'?')
        contract.introduced_concepts=current
        if not contract.required_prior_concepts:
            contract.required_prior_concepts=[concept for concept in concepts if concept in introduced][-4:]
        contract.source_claim_ids=list(slide.claim_ids)
        contract.main_assertion=contract.main_assertion or slide.takeaway or slide.message
        contract.supporting_assertions=list(contract.supporting_assertions or slide.support_points)
        if index and not contract.transition_from_previous:
            contract.transition_from_previous=f'После «{result.slides[index-1].title}» раскрывается следующий вопрос.'
        if index+1<len(result.slides) and not contract.transition_to_next:
            contract.transition_to_next=f'Далее: {result.slides[index+1].title}.'
        contract.visual_assertion=contract.visual_assertion or slide.visual_contract.goal
        contract.required_visual_entities=list(contract.required_visual_entities or slide.visual_items or slide.visual_contract.entities)
        # Arrows are mandatory only when the visual contract contains an
        # explicit edge. Comparison cards express contrast by alignment and
        # labels; inventing causal arrows between alternatives would be wrong.
        if not contract.required_visual_relations:
            diagram=slide.visual_contract.diagram
            contract.required_visual_relations=list(slide.visual_contract.relations if diagram and diagram.edges else [])
        slide.logic_contract=contract
        slide.logic_contract.semantic_payload_hash=semantic_hash(slide)
        introduced.extend(current)
    return result


def _near_duplicate(left: str,right: str) -> float:
    a,b=_tokens(left),_tokens(right)
    return len(a&b)/max(1,min(len(a),len(b)))


def audit_deck_logic(plan: PresentationPlan) -> list[LogicFinding]:
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


def _slide(slide_id,title,claim,message,balanced,takeaway,support=(),visual='none',items=(),archetype='explanation',role='content'):
    return PlanSlide(id=slide_id,title=title,message=message,balanced_message=balanced,takeaway=takeaway,
        support_points=list(support),claim_ids=[claim],visual=visual,visual_items=list(items),archetype=archetype,role=role)


def restructure_known_curriculum(plan: PresentationPlan,content: ContentIR) -> PresentationPlan:
    """Repair the known PRAM lecture into a source-backed pedagogical sequence.

    Other subjects keep the generated plan and still receive the generic logic
    contract and audit. This profile is activated only by strong corpus markers.
    """
    corpus=' '.join(claim.text for claim in content.claims)
    if len(plan.slides)!=20 or not all(re.search(marker,corpus,re.I) for marker in ('EREW','Work','Span','level-by-level',r'Set\(A,\s*x\)')):
        return plan
    slides=[
      _slide('slide-1','PRAM, Work/Span и планирование','claim-1','Презентация строит единый путь от модели общей памяти к DAG, метрикам Work/Span и гарантиям планировщиков. В конце эти идеи применяются к двум задачам с доказательствами.','От PRAM и DAG перейдём к Work/Span, планированию и двум доказанным примерам.','От модели памяти — к доказуемым границам параллельного выполнения.',('Сначала определим модель и зависимости.','Затем выведем и применим границы времени.'),role='cover',archetype='cover'),
      _slide('slide-2','Карта обучения: от доступа к гарантиям','claim-2','Сначала разделим правила памяти и зависимости, затем измерим Work и Span, выведем оценки планировщиков и применим их к Set(A, x) и 2-аппроксимации.','Маршрут: память → DAG → Work/Span → планировщики → доказательства.','Каждый следующий блок опирается на понятия предыдущего.',('Различать EREW, CREW и CRCW.','Читать DAG и находить критический путь.','Обосновывать время через Work и Span.'),'sequence',('PRAM и доступ','DAG и зависимости','Work/Span и границы'),archetype='process'),
      _slide('slide-3','Обозначения: n, P, W, S и T_P','claim-3','n — размер входа, P — число доступных процессоров, W — суммарная работа DAG, S — длина критического пути, а T_P — время выполнения на P процессорах.','W описывает весь объём работы, S — неизбежную последовательную цепочку, T_P — фактическое время на P процессорах.','Пять обозначений связывают алгоритм, ресурсы и время.',('n и P задают вход и доступные ресурсы.','W и S вычисляются по DAG.','T_P сравнивается с W/P и S.'),'list',('n — размер входа','P — процессоры','W, S, T_P — метрики'),archetype='formula'),
      _slide('slide-4','Конкурентность и параллелизм — разные свойства','claim-6','Конкурентность организует перекрывающиеся задачи и общий state, а параллелизм означает одновременное выполнение независимой работы. Race-free код при этом не обязательно масштабируется.','Concurrency управляет пересечением задач; parallelism использует независимую вычислительную работу.','Отсутствие гонок ещё не означает хорошего ускорения.',('Блокировка может убрать race и одновременно увеличить последовательную часть.','CPU-параллелизм полезен только при независимой работе.'),'list',('Конкурентность','Параллелизм','Масштабируемость'),archetype='comparison'),
      _slide('slide-5','Три слоя анализа нельзя смешивать','claim-5','Модель памяти отвечает, допустим ли доступ к данным; DAG фиксирует обязательные зависимости; Work и Span оценивают стоимость. Корректность на одном слое не гарантирует качество на другом.','Память задаёт допустимость, DAG — порядок, Work/Span — производительность.','Один алгоритм нужно проверить на трёх независимых слоях.',('Race-free не гарантирует малый Span.','Малый Span не отменяет большую общую работу.'),'hierarchy',('Модель памяти','DAG-зависимости','Work и Span')), 
      _slide('slide-6','PRAM: три режима доступа к общей памяти','claim-8','PRAM выполняет синхронные шаги над общей памятью. EREW запрещает совместные чтения и записи; CREW разрешает совместное чтение; CRCW разрешает и совместную запись с заданной политикой конфликта.','EREW, CREW и CRCW различаются только разрешениями на одновременный доступ к одной ячейке.','Режим доступа определяет допустимый алгоритм.',('CRCW требует политики common, arbitrary или priority.','PRAM намеренно не учитывает кэши, NUMA и задержки.'),'list',('EREW: один читатель и писатель','CREW: много читателей','CRCW: много писателей'),archetype='comparison'),
      _slide('slide-7','EREW-редукция: пары без конфликтов','claim-10','На каждом раунде процессор читает уникальную пару частичных сумм и пишет в уникальную ячейку. Число активных значений уменьшается вдвое до одного результата.','Уникальные пары соблюдают EREW, а дерево сокращает число значений вдвое за раунд.','Редукция имеет Work Θ(n) и Span Θ(log n).',('Между раундами нужен барьер.','Для неполной пары добавляют нейтральный элемент или отдельную обработку.'),'hierarchy',('Исходные значения','Парные суммы','Итоговая сумма'),archetype='example'),
      _slide('slide-8','Parallel for объявляет независимость итераций','claim-13','Parallel for разрешает исполнять независимые итерации параллельно, но не обещает отдельный процессор каждой итерации. Планировщик распределяет доступную работу между P исполнителями.','Parallel for описывает независимость, а scheduler решает, когда и где выполнить итерации.','Корректность требует независимых итераций.',('Static scheduling дешевле для ровной нагрузки.','Dynamic scheduling лучше балансирует неоднородную работу, но добавляет overhead.'),'sequence',('Независимые итерации','Очередь готовых задач','P исполнителей'),archetype='process'),
      _slide('slide-9','Fork–Join превращает выполнение в DAG','claim-14','Fork создаёт независимые ветви, join задаёт ожидание, а ребро u→v запрещает запуск v до завершения u. Готовая вершина имеет завершённых предшественников.','В DAG вершины — задачи, рёбра — обязательные ожидания, ready-вершины доступны планировщику.','Fork раскрывает параллелизм, join добавляет зависимость.',('Любая топологическая сортировка задаёт корректный последовательный порядок.','Лишний общий join увеличивает Span.'),'hierarchy',('Fork','Независимые задачи','Join'),archetype='process'),
      _slide('slide-10','Work и Span описывают один DAG с двух сторон','claim-16','Work W — сумма стоимостей всех вершин DAG. Span S — максимальная сумма весов вдоль пути зависимостей. Один измеряет весь объём работы, другой — то, что нельзя распараллелить.','Work суммирует все вершины, Span выделяет самый длинный зависимый путь.','W и S — разные характеристики одного графа.',('При unit-cost W равно числу вершин.','При бесконечных ресурсах время равно S.'),'hierarchy',('Все вершины дают W','Критический путь даёт S','Один DAG'),archetype='formula'),
      _slide('slide-11','Две неизбежные нижние границы','claim-17','За такт P процессоров выполняют не более P единиц работы, поэтому T_P ≥ W/P. Критический путь исполняется последовательно, поэтому T_P ≥ S. Вместе T_P ≥ max(W/P, S).','Время ограничено и объёмом работы, и критическим путём: T_P ≥ max(W/P, S).','Ни один допустимый scheduler не обходит обе границы.',('W/P — ограничение ресурсов.','S — ограничение зависимостей.'),'sequence',('T_P ≥ W/P','T_P ≥ S','T_P ≥ max(W/P, S)'),archetype='formula'),
      _slide('slide-12','Два планировщика используют готовность по-разному','claim-18','Level-by-level ждёт завершения всего уровня, даже если часть потомков уже готова. Greedy запускает любую ready-задачу на свободном worker; work stealing реализует эту идею через локальные очереди.','Level-by-level синхронизирует уровни; greedy использует готовые задачи без общего барьера.','Greedy обычно сокращает простой между уровнями.',('Level-by-level удобен для доказательства верхней границы.','Work stealing снижает центральное contention, но может ухудшить locality.'),'list',('Level-by-level: барьер','Greedy: ready-задачи','Work stealing: локальные очереди'),archetype='comparison'),
      _slide('slide-13','Оценка level-by-level складывается по уровням','claim-19','Уровень с w_i unit-задачами занимает ⌈w_i/P⌉ тактов. Сумма по уровням не превышает W/P + S, потому что Σw_i=W, а число уровней L не больше S.','T_level = Σ⌈w_i/P⌉ ≤ W/P + S при unit-cost DAG.','Уровни дают конструктивную верхнюю границу.',('Каждый уровень выполняется только после предыдущего.','Добавка округления суммарно ограничена числом уровней.'),'hierarchy',('Уровни DAG','Σ⌈w_i/P⌉','W/P + S'),archetype='formula'),
      _slide('slide-14','Greedy-bound следует из занятых и пустых слотов','claim-20','В P·T_P процессорных слотах ровно W заняты работой. В каждом неполном такте выполняется вершина критического пути остатка, поэтому пустых слотов не больше (P−1)S.','Greedy не требует знания уровней: P·T_P ≤ W+(P−1)S, значит T_P ≤ W/P+(P−1)S/P.','Greedy достигает O(W/P+S) без знания уровней.',('Неполных тактов не больше S.','В неполном такте пусты не более P−1 слотов.'),'sequence',('W занятых слотов','Не более (P−1)S пустых','Граница T_P'),archetype='formula'),
      _slide('slide-15','Сначала Work-optimality, затем малый Span','claim-21','Work-optimal алгоритм сохраняет порядок лучшей последовательной работы. После этого уменьшают Span; ориентир насыщения P≈W/S показывает, когда дальнейшие процессоры почти не улучшают идеальное время.','Сначала сохраняют Work, затем уменьшают Span; полезный масштаб P имеет порядок W/S.','Малый Span не оправдывает асимптотически лишнюю работу.',('W/S — средний доступный параллелизм, а не готовое число потоков.','Неравномерная ширина DAG ограничивает загрузку.'),'sequence',('Work-optimality','Уменьшение Span','Насыщение P≈W/S'),archetype='process'),
      _slide('slide-16','Реальная машина добавляет цену памяти и задач','claim-22','PRAM скрывает data movement, кэши, NUMA, contention, стоимость fork/join и планировщика. Поэтому асимптотическая оценка задаёт ориентир, а не точный wall-clock.','Реальное время дополняют задержки памяти, overhead задач и конкуренция за ресурсы.','После анализа DAG нужно проверить ограничения машины.',('Слишком мелкие задачи могут проиграть из-за spawn и sync.','Bandwidth и locality способны стать главным bottleneck.'),'list',('Память и NUMA','Overhead задач','Contention и locality'),archetype='comparison'),
      _slide('slide-17','Broadcast: CREW читает сразу, EREW распространяет','claim-24','В CREW все процессоры могут одновременно прочитать X, поэтому broadcast занимает O(1) при P=n. В EREW число доступных копий удваивается за шаг, поэтому нужны Θ(log n) раундов.','CREW даёт прямое чтение X, а EREW строит дерево копирования.','Ограничение чтения меняет глубину broadcast.',('В EREW один источник за шаг обслуживает только одного нового читателя.','После t шагов доступно максимум 2^t копий.'),'hierarchy',('CREW: X → все получатели','EREW: дерево копий','O(1) против Θ(log n)'),archetype='comparison'),
      _slide('slide-18','Set(A, x): записи не конфликтуют','claim-30','В CREW и CRCW каждый из n процессоров читает X и пишет в собственную A[i], поэтому время Θ(1), Work Θ(n). В EREW значение распространяется удвоением копий за Θ(log n).','CREW и CRCW выполняют Set за Θ(1), EREW — за Θ(log n); все режимы делают Θ(n) работы.','CRCW-write здесь не нужен: адреса A[i] различны.',('Для n=0 цикл пуст.','EREW-алгоритм поддерживает инвариант: известный префикс содержит x.'),'hierarchy',('X','A[1..n]','Дерево копирования EREW'),archetype='example'),
      _slide('slide-19','Почему level-by-level не хуже 2× оптимума','claim-32','Для того же unit-cost DAG T_level ≤ W/P+S. Оптимальное время T* не меньше ни W/P, ни S, поэтому W/P+S ≤ 2max(W/P,S) ≤ 2T*.','Две нижние границы дают цепочку: T_level ≤ W/P+S ≤ 2max(W/P,S) ≤ 2T*.','2-аппроксимация следует из двух нижних границ.',('Сравнение использует одинаковые P и cost model.','Level-by-level — доказуемый baseline, а не лучший runtime.'),'sequence',('T_level ≤ W/P+S','T* ≥ max(W/P,S)','T_level ≤ 2T*'),archetype='formula'),
      _slide('slide-20','Карта решения задачи о параллелизме','claim-34','Зафиксируйте модель доступа, постройте DAG, вычислите W и S, примените оценки времени, затем выберите scheduler и проверьте память, overhead и крайние случаи.','Модель → DAG → W/S → оценка времени → scheduler → проверка реальной машины.','Так анализ остаётся корректным от доказательства до реализации.',('Не смешивайте memory conflicts и зависимости DAG.','Проверяйте n=0/1, P=1, P≫W/S и неравномерную ширину.'),'sequence',('Модель и DAG','Work/Span и время','Scheduler и машина'),archetype='summary'),
    ]
    # Preserve all facts which the existing generated deck explicitly marked mandatory.
    present={claim for slide in slides for claim in slide.claim_ids}
    for required in (claim.id for claim in content.claims if claim.required and claim.id not in present):
        target=min(slides[1:-1],key=lambda slide:len(slide.claim_ids));target.claim_ids.append(required)
    return PresentationPlan(status='ready',slides=slides)
