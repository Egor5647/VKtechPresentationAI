import json
import shutil
import pytest
from pptx import Presentation
from PIL import Image
from vktech.template import import_template
from vktech.planning import NeedsInput,build_scenes,validate_plan,plan_with_model,normalize_plan,planning_claims,enrich_plan,fit_semantic_variants,assign_visual_strategies,protect_text_from_template_decor,_diagram_layout,_diagram_items
from vktech.export import export_pptx,export_html,_card_font_size
from vktech.opc import Package
from vktech.audit import audit_scene,audit_rendered_deck,repair_scene,contrast
from vktech.contracts import Box,DiagramSpec,Node,PaletteSpec,PlanSlide,RepairRequest,GenerateRequest,PresentationPlan,SceneIR,Slot,Style,ImageSelection,ImageCandidateScore
from vktech.palette import palette_from_design,recolor_design,recolor_scene,recolor_template
from vktech.store import Store,Job
from vktech.worker import execute
from vktech.selection import choose_variants,compose_scene
from vktech.settings import artifact_path
from vktech.pipeline import Pipeline
from conftest import FixtureGateway


def test_three_variants_native_objects(template_bytes,content,plan,tmp_path):
    design=import_template(template_bytes);validate_plan(plan,content,12)
    scenes=build_scenes(design,content,plan,'test')
    required={c.id for c in content.claims}
    for scene in scenes:
        assert {cid for s in scene.slides for n in s.nodes for cid in n.claim_ids}==required
        file=tmp_path/(scene.variant+'.pptx');export_pptx(template_bytes,design,scene,file)
        prs=Presentation(file);assert len(prs.slides)==12
        pkg=Package(file.read_bytes());native=[s for s in prs.slides for sh in s.shapes if sh.has_chart or sh.has_table]
        assert len(native)==1
        assert any('embeddings/' in p for p in pkg.parts) or scene.variant=='C'
        assert any('-visual-card-' in sh.name for slide in prs.slides for sh in slide.shapes)
        html=tmp_path/(scene.variant+'.html');export_html(scene,html)
        text=html.read_text();assert '<svg' in text or '<table' in text
        assert 'aria-label="Навигация"' in text and 'ArrowRight' in text
    assert len({tuple(s.prototype_id for s in sc.slides) for sc in scenes})==3


def test_scene_uses_model_summary_with_source_traceability(template_bytes,content,plan):
    concise=plan.model_copy(deep=True);concise.slides[0].message='Краткое изложение исходного факта'
    scene=build_scenes(import_template(template_bytes),content,concise,'summary')[0]
    body=next(n for n in scene.slides[0].nodes if n.role=='body')
    assert body.text=='Краткое изложение исходного факта'
    assert body.claim_ids==concise.slides[0].claim_ids


def test_missing_template_font_uses_configured_fallback(template_bytes,content,plan,monkeypatch):
    design=import_template(template_bytes)
    for prototype in design.prototypes:
        for slot in prototype.slots:slot.style.font='Unavailable Corporate Font'
    design.fonts=['Unavailable Corporate Font']
    monkeypatch.setenv('FONT_FALLBACK','Arial')
    import vktech.audit as audit
    from PIL import ImageFont
    monkeypatch.setattr(audit,'font_for',lambda name,size: ImageFont.load_default(size=size) if name=='Arial' else None)
    scene=build_scenes(design,content,plan,'fallback')[0]
    assert all(node.style.font=='Arial' for slide in scene.slides for node in slide.nodes if node.kind=='text' and node.role!='accent')


def test_plan_cannot_drop_facts_or_invent_numbers(content,plan):
    broken=plan.model_copy(deep=True);broken.slides[-1].claim_ids=[]
    with pytest.raises(ValueError,match='omits'):validate_plan(broken,content,12)
    broken=plan.model_copy(deep=True);broken.slides[0].title='Рост 999%'
    with pytest.raises(ValueError,match='numbers'):validate_plan(broken,content,12)
    broken=plan.model_copy(deep=True);broken.slides[1].title=broken.slides[0].title;broken.slides[1].message=broken.slides[0].message
    with pytest.raises(ValueError,match='duplicate'):validate_plan(broken,content,12)


def test_plan_rejects_repeated_title_or_main_idea(content,plan):
    repeated_title=plan.model_copy(deep=True);repeated_title.slides[1].title=repeated_title.slides[0].title
    with pytest.raises(ValueError,match='title'):validate_plan(repeated_title,content,12)
    repeated_message=plan.model_copy(deep=True);repeated_message.slides[1].message=repeated_message.slides[0].message
    with pytest.raises(ValueError,match='main idea'):validate_plan(repeated_message,content,12)


def test_plan_rejects_mechanically_truncated_text(content,plan):
    broken=plan.model_copy(deep=True);broken.slides[0].message='Мысль механически оборвана…'
    with pytest.raises(ValueError,match='truncated'):validate_plan(broken,content,12)


def test_plan_rejects_density_order_that_does_not_add_detail(content,plan):
    broken=plan.model_copy(deep=True);slide=broken.slides[0]
    slide.takeaway='Краткая самостоятельная мысль.'
    slide.balanced_message='Сбалансированная самостоятельная мысль с контекстом.'
    slide.message='Подробная мысль.'
    with pytest.raises(ValueError,match='increase semantic detail'):validate_plan(broken,content,12)


def test_plan_rejects_unfinished_sentence_without_ellipsis(content,plan):
    broken=plan.model_copy(deep=True);broken.slides[0].message='Мысль оборвалась посреди последнего'
    with pytest.raises(ValueError,match='unfinished sentence'):validate_plan(broken,content,12)


def test_plan_rejects_near_duplicate_main_ideas(content,plan):
    broken=plan.model_copy(deep=True)
    broken.slides[1].message='Один и тот же параллельный алгоритм использует общую память и критический путь для вычислений.'
    broken.slides[2].message='Тот же параллельный алгоритм использует общую память и критический путь при вычислении.'
    with pytest.raises(ValueError,match='near-duplicate main ideas'):validate_plan(broken,content,12)


def test_plan_rejects_text_that_cannot_fit_density_modes(content,plan):
    broken=plan.model_copy(deep=True);broken.slides[0].message='Очень длинная главная мысль, которая остаётся законченным предложением, но значительно превышает безопасную длину для подробной компоновки и поэтому должна быть семантически переформулирована моделью целиком, без обрезания отдельных слов, фактов, чисел, оговорок и смысловых связей.'
    with pytest.raises(ValueError,match='220 characters'):validate_plan(broken,content,12)
    broken=plan.model_copy(deep=True);broken.slides[0].takeaway='Слишком длинный вывод сохраняет грамматическую завершённость, однако уже не подходит для крупного режима и поэтому должен быть полностью переформулирован моделью без механического удаления слов.'
    with pytest.raises(ValueError,match='130 characters'):validate_plan(broken,content,12)


def test_density_fit_composes_whole_authored_sentences_without_clipping(plan):
    draft=plan.model_copy(deep=True);slide=draft.slides[0]
    slide.takeaway='Короткая главная мысль сохраняется полностью.'
    slide.balanced_message='Сбалансированная формулировка сохраняет основную мысль и необходимый контекст.'
    slide.message='Очень длинный подробный текст '+('с дополнительным контекстом '*12)+'и не должен быть обрезан.'
    slide.support_points=['Подтверждающий факт остаётся отдельным законченным предложением.']
    fitted=fit_semantic_variants(draft).slides[0]
    assert fitted.takeaway.endswith('.') and fitted.balanced_message.endswith('.') and fitted.message.endswith('.')
    assert len(fitted.message)<=220 and '…' not in fitted.message and '...' not in fitted.message
    assert fitted.message!=fitted.takeaway and fitted.message!=fitted.balanced_message
    assert len(fitted.takeaway)<len(fitted.balanced_message)<len(fitted.message)


def test_density_fit_accepts_provider_overflow_before_semantic_compaction():
    slide=PlanSlide(
        id='slide-1',title='Пределы параллелизма',claim_ids=['claim-1'],
        takeaway='Span ограничивает ускорение.',
        balanced_message='Span задаёт критический путь и ограничивает ускорение даже при большом числе процессоров.',
        message=(
            'Work измеряет всю выполненную работу, а Span задаёт критический путь вычисления. '
            'Эти метрики дают нижние границы T_P ≥ W/P и T_P ≥ S для любого допустимого расписания. '
            'Даже при большом числе процессоров ускорение ограничено зависимостями на критическом пути. '
            'Практическая оптимизация сначала сохраняет Work-optimality, а затем уменьшает Span. '
            'Все формулы и оговорки должны оставаться законченными мыслями без механического обрыва.'
        ),
        support_points=['Нижние границы сохраняются полностью.'],
    )
    assert len(slide.message)>360
    fitted=fit_semantic_variants(PresentationPlan(slides=[slide])).slides[0]
    assert len(fitted.message)<=220
    assert fitted.message.endswith('.')
    assert '…' not in fitted.message and '...' not in fitted.message
    assert 'Span' in fitted.message


def test_density_fit_removes_repeated_sentences_and_repairs_pdf_formula(plan):
    draft=plan.model_copy(deep=True);slide=draft.slides[0]
    slide.takeaway='Время T! ограничено работой.'
    slide.balanced_message='Время T! ограничено работой. Дополнительный контекст сохраняется.'
    slide.message='Время T! ограничено работой. Время T! ограничено работой. Дополнительный контекст сохраняется. Подробность объясняет границу.'
    fitted=fit_semantic_variants(normalize_plan(draft)).slides[0]
    assert 'T!' not in fitted.message and 'T_P' in fitted.message
    assert fitted.message.count('Время T_P ограничено работой.')==1
    assert len(fitted.takeaway)<len(fitted.balanced_message)<len(fitted.message)


def test_editorial_cleanup_removes_isolated_mixed_script_pdf_artifact(plan):
    draft=plan.model_copy(deep=True)
    draft.slides[0].title='В材. Структура и цели лекции'
    cleaned=normalize_plan(draft).slides[0].title
    assert cleaned=='Структура и цели лекции'


def test_editorial_cleanup_preserves_mathematical_optimum_marker(content,plan):
    draft=plan.model_copy(deep=True);draft.slides[1].message='Граница T_level ≤ 2T* сохраняется.'
    assert '2T*' in enrich_plan(draft,content).slides[1].message


def test_editorial_cleanup_repairs_broken_work_span_density_modes(content,plan):
    draft=plan.model_copy(deep=True);draft.slides[1].message='Границы для любого параллельного **s.'
    fixed=enrich_plan(draft,content).slides[1]
    assert '**s' not in fixed.message and 'T_P ≥ W/P' in fixed.message
    assert len(fixed.takeaway)<len(fixed.balanced_message)<len(fixed.message)


def test_plan_rejects_invented_optimization_direction(content,plan):
    broken=plan.model_copy(deep=True);broken.slides[0].message='Сначала необходимо максимизировать показатель, а затем продолжить вычисление.'
    with pytest.raises(ValueError,match='optimization direction'):validate_plan(broken,content,12)


def test_incomplete_optional_visual_intent_is_removed(plan):
    broken=plan.model_copy(deep=True);broken.slides[0].visual='image';broken.slides[0].asset_id=None
    fixed=normalize_plan(broken)
    assert fixed.slides[0].visual=='none'
    assert fixed.slides[0].claim_ids==broken.slides[0].claim_ids


def test_normalized_plan_has_native_visual_quota(plan):
    fixed=normalize_plan(plan)
    content_slides=[slide for slide in fixed.slides if slide.role=='content' and not slide.dataset_id and not slide.asset_id]
    native=[slide for slide in content_slides if slide.visual in {'sequence','list','hierarchy'}]
    assert len(native)>=round(len(content_slides)*.4)


def test_visual_strategy_prefers_images_only_for_illustrative_concepts(content,plan):
    draft=plan.model_copy(deep=True)
    conceptual=draft.slides[1];conceptual.title='Concurrency и parallelism в работе команды';conceptual.message='Практический сценарий показывает работу команды и различие подходов.';conceptual.archetype='illustration'
    formula=draft.slides[2];formula.title='Доказательство формулы';formula.message='Формула T_P ≥ W/P задаёт нижнюю границу.';formula.archetype='formula'
    selected=assign_visual_strategies(draft,content,True)
    assert selected.slides[1].visual_strategy=='generated_image'
    assert selected.slides[2].visual_strategy!='generated_image'


def test_visual_strategy_uses_native_diagram_for_dependencies(content,plan):
    draft=plan.model_copy(deep=True);target=draft.slides[3]
    target.title='DAG зависимостей задач';target.message='Граф показывает зависимости и критический путь между задачами.'
    selected=assign_visual_strategies(draft,content,True).slides[3]
    assert selected.visual_strategy=='diagram' and selected.visual=='hierarchy'


def test_visual_strategy_uses_reviewed_image_for_real_world_model_limit(content,plan):
    draft=plan.model_copy(deep=True);target=draft.slides[4]
    target.title='Практическая реализация и ограничения PRAM-модели'
    target.message='Реальная система показывает data movement, contention и инфраструктурные ограничения.'
    target.archetype='explanation'
    selected=assign_visual_strategies(draft,content,True).slides[4]
    assert selected.visual_strategy=='generated_image'
    enriched=enrich_plan(PresentationPlan(slides=[selected]),content).slides[0]
    assert enriched.visual_contract.goal
    assert 'Общая память' in enriched.visual_contract.entities


def test_visual_strategy_illustrates_concurrency_parallelism_without_diagram(content,plan):
    draft=plan.model_copy(deep=True);target=draft.slides[4]
    target.title='Различие concurrency и parallelism'
    target.message='Concurrency чередует задачи, а parallelism выполняет их одновременно.'
    selected=assign_visual_strategies(draft,content,True).slides[4]
    assert selected.visual_strategy=='generated_image' and selected.visual_score>=.85
    enriched=enrich_plan(PresentationPlan(slides=[selected]),content).slides[0]
    assert enriched.visual_contract.diagram is None
    assert 'Один вычислительный исполнитель' in enriched.visual_contract.entities


def test_visual_strategy_illustrates_physical_production_bottleneck(content,plan):
    draft=plan.model_copy(deep=True);target=draft.slides[4]
    target.title='Пределы полезного параллелизма в production'
    target.message='Реальные bottleneck включают bandwidth, cache, NUMA, locks и oversubscription.'
    selected=assign_visual_strategies(draft,content,True).slides[4]
    assert selected.visual_strategy=='generated_image'
    enriched=enrich_plan(PresentationPlan(slides=[selected]),content).slides[0]
    assert 'Узкий канал доступа' in enriched.visual_contract.entities


def test_visual_strategy_keeps_exact_scheduler_bounds_editable(content,plan):
    draft=plan.model_copy(deep=True);target=draft.slides[4]
    target.title='Верхняя граница level-by-level scheduler'
    target.message='Для unit-cost DAG выполняется T_P ≤ W/P + S.'
    target.visual='sequence'
    selected=assign_visual_strategies(draft,content,True).slides[4]
    assert selected.visual_strategy=='diagram'


def test_visual_strategy_preserves_reviewed_image_fallback(content,plan):
    draft=plan.model_copy(deep=True);target=draft.slides[4]
    target.title='Пределы полезного параллелизма в production'
    target.message='Реальные bottleneck включают bandwidth, cache, NUMA, locks и oversubscription.'
    target.visual='hierarchy';target.visual_strategy='diagram'
    target.visual_reason='Кандидаты иллюстрации отклонены автоматической проверкой; использована редактируемая схема.'
    selected=assign_visual_strategies(draft,content,True).slides[4]
    assert selected.visual_strategy=='diagram' and selected.asset_id is None


def test_diagram_item_compacts_associativity_as_a_complete_phrase(plan):
    slide=plan.slides[4].model_copy(deep=True)
    slide.visual_items=['Ассоциативность операции важна для корректной группировки']
    assert _diagram_items(slide,{})==['Ассоциативная группировка']


def test_image_candidates_are_scored_and_best_is_selected(content,plan,monkeypatch):
    class Gateway:
        def image(self,prompt,output):Image.new('RGB',(160,90),'white').save(output)
        def structured(self,role,payload,schema,images=None):
            assert role=='image_selection' and len(images)==3
            return ImageSelection(candidates=[
                ImageCandidateScore(index=i,score=score,semantic_fit=score,naturalness=score,composition=score,accepted=score>=72,reason='Кандидат проверен по смыслу и композиции.')
                for i,score in enumerate((64,91,77))
            ],selected_index=1,reason='Второй кандидат лучше раскрывает смысл слайда.')
    monkeypatch.delenv('LOCAL_MODEL_SEQUENTIAL',raising=False)
    slide=enrich_plan(PresentationPlan(slides=[plan.slides[1]]),content).slides[0]
    slide.visual_strategy='generated_image'
    folder=artifact_path('test-image-candidates');folder.mkdir(parents=True,exist_ok=True)
    updated=content.model_copy(deep=True)
    candidates=Pipeline(None,Gateway())._generate_image_candidates(slide,updated,folder,'candidate-test')
    assert len(candidates)==3 and candidates[1]['selected']
    assert slide.visual=='image' and slide.asset_id==candidates[1]['asset_id']


def test_visual_strategy_uses_image_for_conceptual_transition(content,plan):
    draft=plan.model_copy(deep=True);target=draft.slides[1]
    target.title='От конкурентного кода к абстрактным параллельным алгоритмам'
    target.message='Абстрактный алгоритм отделяет полезный параллелизм от деталей потоков.'
    target.visual_brief='Переход от сложного кода к ясной модели.';target.archetype='process'
    selected=assign_visual_strategies(draft,content,True).slides[1]
    assert selected.visual_strategy=='diagram' and selected.visual=='sequence'


@pytest.mark.parametrize(('title','expected'),[
    ('Бинарная редукция в EREW','reduction_tree'),
    ('Fork-Join и DAG вычислений','fork_join'),
    ('От конкурентного кода к абстрактной модели','abstraction'),
    ('Work и Span: критический путь','work_span'),
    ('Level-by-level scheduler: доказательство 2-аппроксимации','level_bound_proof'),
    ('Level-by-level scheduler: выполнение по уровням','level_schedule'),
    ('Greedy scheduler: формула верхней границы','formula_focus'),
    ('Level-by-level scheduler','level_schedule'),
    ('Broadcast, reduction и планировщики','comparison'),
    ('Практическая реализация и ограничения PRAM-модели','pram_reality'),
])
def test_semantic_diagram_layouts(plan,title,expected):
    slide=plan.slides[3].model_copy(deep=True);slide.title=title
    assert _diagram_layout(slide)==expected


def test_render_audit_detects_repeated_adjacent_composition(template_bytes,content,plan,tmp_path):
    design=import_template(template_bytes);scene=build_scenes(design,content,enrich_plan(plan,content),'render-audit')[0]
    scene.slides=[scene.slides[1],scene.slides[1].model_copy(deep=True)]
    scene.slides[1].id='copy'
    first=tmp_path/'first.png';second=tmp_path/'second.png'
    Image.new('RGB',(320,180),'white').save(first);Image.new('RGB',(320,180),'white').save(second)
    issues=audit_rendered_deck(scene,[first,second])
    assert any(item.rule=='D29' and item.status=='fail' for item in issues)


def test_diagram_card_font_shrinks_to_keep_full_label_inside():
    node=Node(id='visual',kind='diagram',role='visual',box=Box(x=0,y=0,w=.38,h=.5),style=Style(font='Arial',size=20),data={})
    fitted=_card_font_size(node,2_200_000,1_000_000,'Level-by-level: ждёт завершения уровня')
    assert 12<=fitted<20


def test_palette_recolors_scene_design_and_template(template_bytes,content,plan):
    design=import_template(template_bytes);scene=build_scenes(design,content,enrich_plan(plan,content),'palette')[0]
    palette=PaletteSpec(background='F4F7F4',surface='DCE9E1',accent='0B5D3B',accent_secondary='4D8A68',text_primary='14251D')
    updated=recolor_scene(scene,design,palette);updated_design=recolor_design(design,palette)
    assert updated.version==scene.version+1
    assert updated.slides[0].background==palette.background
    assert palette.accent in updated_design.palette and updated_design.evidence['palette_override']['accent']==palette.accent
    rebuilt=build_scenes(updated_design,content,enrich_plan(plan,content),'palette-rebuild')
    assert all(node.style.color!='0077FF' for scene in rebuilt for slide in scene.slides for node in slide.nodes)
    assert all(node.data.get('accent')!='0077FF' for scene in rebuilt for slide in scene.slides for node in slide.nodes if node.kind=='diagram')
    package=Package(recolor_template(template_bytes,design,palette))
    xml=b''.join(value for name,value in package.parts.items() if name.endswith('.xml'))
    assert b'F4F7F4' in xml or b'DCE9E1' in xml


def test_palette_is_inferred_from_template(template_bytes):
    palette=palette_from_design(import_template(template_bytes))
    colors=palette.model_dump(exclude={'schema_version'})
    assert all(len(value)==6 for value in colors.values())


def test_cover_text_stays_inside_template_safe_column(template_bytes,content,plan):
    design=import_template(template_bytes);prototype=design.prototypes[0]
    prototype.slots.append(Slot(id='safe-column',shape_id=999,role='visual',box=Box(x=.05,y=.70,w=.41,h=.08),style=Style()))
    scene=build_scenes(design,content,enrich_plan(plan,content),'safe-cover')[0]
    slide=scene.slides[0];slide.prototype_id=prototype.id
    title=next(node for node in slide.nodes if node.role=='title');body=next(node for node in slide.nodes if node.role=='body')
    title.box.w=body.box.w=.58
    repaired=protect_text_from_template_decor(scene,design).slides[0]
    assert all(node.box.x+node.box.w<.46 for node in repaired.nodes if node.role in {'title','body'})
    report=audit_scene(SceneIR(id='unsafe',variant='A',template_id=scene.template_id,content_id=scene.content_id,width=scene.width,height=scene.height,slides=[slide]),design,content,opened=True)
    assert any(issue.rule=='D27' and issue.status=='fail' for issue in report.issues)


def test_candidate_selection_and_composition(template_bytes,content,plan):
    scenes=build_scenes(import_template(template_bytes),content,enrich_plan(plan,content),'selection')
    slide_ids=[slide.id for slide in scenes[0].slides]
    scores={slide_id:{'A':80,'B':82,'C':79} for slide_id in slide_ids}
    selected=choose_variants(scores,slide_ids)
    assert set(selected)==set(slide_ids)
    assert len(set(selected.values()))>1
    composed=compose_scene(scenes,selected,'selected')
    for index,slide in enumerate(composed.slides):
        source=next(scene for scene in scenes if scene.variant==selected[slide.id])
        assert slide==source.slides[index]
    assert composed.variant=='selected'


def test_variants_have_distinct_density_and_type_scale(template_bytes,content,plan):
    structured=enrich_plan(plan,content);target=structured.slides[1]
    target.support_points=['Короткая деталь A','Короткая деталь B','Короткая деталь C']
    scenes=build_scenes(import_template(template_bytes),content,structured,'density')
    slides=[scene.slides[1] for scene in scenes]
    bodies=[next(node for node in slide.nodes if node.role=='body') for slide in slides]
    assert bodies[0].style.size>bodies[1].style.size>bodies[2].style.size
    assert len(slides[0].nodes)<len(slides[1].nodes)<len(slides[2].nodes)


def test_variants_use_complete_semantic_text_without_ellipsis(template_bytes,content,plan):
    structured=enrich_plan(plan,content);target=structured.slides[1]
    target.message='Полная главная мысль объясняет факт целиком. Второе предложение добавляет контекст.'
    target.balanced_message='Сбалансированная версия объясняет факт и сохраняет контекст.'
    target.takeaway='Короткая версия сохраняет основную мысль целиком.'
    target.support_points=['Первый законченный подтверждающий тезис.','Второй законченный подтверждающий тезис.']
    scenes=build_scenes(import_template(template_bytes),content,structured,'semantic')
    leads=[next(node.text for node in scene.slides[1].nodes if node.role=='body') for scene in scenes]
    assert leads[0]==target.takeaway
    assert leads[1]==target.balanced_message
    assert leads[2]==target.message
    assert len(set(leads))==3
    assert all('…' not in lead and '...' not in lead for lead in leads)


def test_diagram_uses_short_model_labels_and_readable_type(template_bytes,content,plan):
    structured=enrich_plan(plan,content);target=structured.slides[3]
    target.visual='sequence';target.visual_items=['Получить независимые задачи','Выполнить задачи параллельно','Объединить результаты']
    scenes=build_scenes(import_template(template_bytes),content,structured,'diagram-labels')
    for scene in scenes:
        node=next(node for node in scene.slides[3].nodes if node.kind=='diagram')
        limit=2 if scene.variant=='A' else 3
        assert node.data['items']==target.visual_items[:limit]
        assert node.style.size>=20


def test_graph_semantics_are_identical_across_density_variants(template_bytes,content,plan):
    draft=plan.model_copy(deep=True);target=draft.slides[3]
    target.title='Work и Span: критический путь';target.visual='hierarchy'
    structured=enrich_plan(draft,content);scenes=build_scenes(import_template(template_bytes),content,structured,'shared-graph')
    graphs=[]
    for scene in scenes:
        node=next(node for node in scene.slides[3].nodes if node.kind=='diagram')
        graphs.append(node.data['graph'])
    assert graphs[0]==graphs[1]==graphs[2]
    assert graphs[0]['educational_example'] is True
    assert graphs[0]['example_label']=='Учебный пример'


def test_weighted_source_graph_requires_claim_provenance(content,plan):
    broken=plan.model_copy(deep=True);slide=broken.slides[3]
    slide.visual_contract.diagram=DiagramSpec.model_validate({
        'kind':'work_span',
        'nodes':[{'id':'a','label':'A','weight':2,'origin':'source'},{'id':'b','label':'B','weight':1,'origin':'source'}],
        'edges':[{'source':'a','target':'b'}],
    })
    with pytest.raises(ValueError,match='require claim_ids'):validate_plan(broken,content,12)


def test_semantic_graph_exports_as_editable_shapes_and_html_svg(template_bytes,content,plan,tmp_path):
    draft=plan.model_copy(deep=True);target=draft.slides[3]
    target.title='Критический путь';target.visual='hierarchy'
    structured=enrich_plan(draft,content);design=import_template(template_bytes)
    scene=build_scenes(design,content,structured,'graph-exports')[1]
    pptx=tmp_path/'graph.pptx';html=tmp_path/'graph.html'
    export_pptx(template_bytes,design,scene,pptx);export_html(scene,html)
    slide=Presentation(pptx).slides[3];names=[shape.name for shape in slide.shapes]
    assert sum('-graph-node-' in name for name in names)>=3
    assert sum('-edge-' in name for name in names)>=2
    assert any(name.endswith('-example-label') for name in names)
    markup=html.read_text(encoding='utf-8')
    assert '<svg' in markup and '<line ' in markup and 'Учебный пример' in markup and 'Span S = 11' in markup


def test_narrow_sequence_uses_vertical_cards_and_shortens_long_labels(template_bytes,content,plan,tmp_path):
    structured=enrich_plan(plan,content);target=structured.slides[3]
    target.visual='sequence';target.visual_items=['Очень длинная подпись этапа, которая содержит дополнительное пояснение и не должна выходить из карточки','Второй этап','Третий этап']
    design=import_template(template_bytes);scene=build_scenes(design,content,structured,'vertical-diagram')[1]
    node=next(node for node in scene.slides[3].nodes if node.kind=='diagram')
    assert max(map(len,node.data['items']))<=52
    output=tmp_path/'vertical.pptx';export_pptx(template_bytes,design,scene,output)
    slide=Presentation(output).slides[3]
    cards=[shape for shape in slide.shapes if shape.name.startswith(target.id+'-visual-card-')]
    assert len(cards)==3
    assert len({shape.left for shape in cards})==1
    assert [shape.top for shape in cards]==sorted(shape.top for shape in cards)


def test_planner_retries_with_missing_claim_feedback(content,plan):
    class Gateway:
        def __init__(self):self.payloads=[]
        def structured(self,role,payload,schema,images=None):
            self.payloads.append(dict(payload))
            if len(self.payloads)==1:
                incomplete=plan.model_copy(deep=True);incomplete.slides[-1].claim_ids=[];return incomplete
            return plan
    gateway=Gateway();request=GenerateRequest(template_id='template',content_id='content',brief='test')
    assert plan_with_model(gateway,content,request)==enrich_plan(normalize_plan(plan),content)
    assert len(gateway.payloads)==2
    assert 'omits mandatory claims' in gateway.payloads[1]['validation_feedback']
    assert set(gateway.payloads[0]['required_claim_ids'])=={c.id for c in content.claims if c.required}


def test_planner_assigns_stable_ids_instead_of_retrying_model_metadata(content,plan):
    duplicate_ids=plan.model_copy(deep=True)
    for slide in duplicate_ids.slides:slide.id='slide'
    class Gateway:
        def __init__(self):self.calls=0
        def structured(self,role,payload,schema,images=None):self.calls+=1;return duplicate_ids
    gateway=Gateway();request=GenerateRequest(template_id='template',content_id='content',brief='test')
    result=plan_with_model(gateway,content,request)
    assert [slide.id for slide in result.slides]==[f'slide-{index}' for index in range(1,13)]
    assert gateway.calls==1


def test_planner_does_not_retry_a_model_request_for_missing_input(content):
    class Gateway:
        def __init__(self):self.calls=0
        def structured(self,role,payload,schema,images=None):
            self.calls+=1
            return schema(status='needs_input',reason='Нужны исходные данные',slides=[])
    gateway=Gateway();request=GenerateRequest(template_id='template',content_id='content',brief='test')
    with pytest.raises(NeedsInput,match='Нужны исходные данные'):
        plan_with_model(gateway,content,request)
    assert gateway.calls==1


def test_long_deck_is_planned_in_source_ordered_batches(content):
    from vktech.contracts import Claim,ContentIR,PlanSlide,PresentationPlan
    topics=['матрица','редукция','барьер','планировщик','память','поток','граф','вершина','ребро','очередь','синхронизация','локальность','ассоциативность','коммутативность','пропускная способность','задержка','масштабируемость','зависимость']
    claims=[Claim(id=f'claim-long-{index}',text=f'Учебная тема: {topic}.',source=f'source-{index}',required=False) for index,topic in enumerate(topics)]
    long_content=ContentIR(id='long',title='long',claims=claims)
    class Gateway:
        def __init__(self):self.payloads=[]
        def structured(self,role,payload,schema,images=None):
            self.payloads.append(payload)
            available={claim['id']:claim for claim in payload['content']['claims']};segment=payload['plan_segment']['index']
            slides=[]
            for index,assignment in enumerate(payload['slide_assignments']):
                claim=available[assignment['claim_ids'][0]]
                marker=chr(1040+(segment-1)*6+index)
                topic=claim['text'].removeprefix('Учебная тема: ').rstrip('.')
                slides.append(PlanSlide(id=f'local-{index}',title=f'{topic}: уникальная тема {marker}',takeaway=f'{topic} определяет тему {marker}.',balanced_message=f'{topic} определяет тему {marker} и её основной механизм.',message=f'{topic} определяет тему {marker}, её основной механизм и практическое следствие.',claim_ids=assignment['claim_ids']))
            return PresentationPlan(slides=slides)
    gateway=Gateway();request=GenerateRequest(template_id='template',content_id='content',brief='test',slide_count=18)
    result=plan_with_model(gateway,long_content,request)
    assert len(result.slides)==18
    assert [slide.id for slide in result.slides]==[f'slide-{index}' for index in range(1,19)]
    assert len(gateway.payloads)==3
    assert [payload['plan_segment']['index'] for payload in gateway.payloads]==[1,2,3]


def test_planning_context_rejects_unbounded_required_claims(content,monkeypatch):
    monkeypatch.setenv('MODEL_MAX_PLANNING_CLAIMS','1')
    with pytest.raises(NeedsInput,match='обязательных блоков'):
        planning_claims(content,'краткий бриф')


def test_selected_repair_is_versioned(template_bytes,content,plan):
    design=import_template(template_bytes);scene=build_scenes(design,content,plan,'repair')[0]
    first=scene.slides[0].nodes[0];second=scene.slides[1].nodes[0];first.box.x=-.2;second.box.x=-.3
    report=audit_scene(scene,design,content)
    selected=next(i for i in report.issues if i.rule=='D01' and i.element_ids==[first.id])
    request=RepairRequest(variant='A',expected_version=1,issue_ids=[selected.id],idempotency_key='repair-test')
    new,patch=repair_scene(scene,report,request,design)
    assert new.version==2 and new.slides[0].nodes[0].box.x==0
    assert new.slides[1].nodes[0].box.x==-.3 and first.box.x==-.2
    with pytest.raises(ValueError,match='Stale'):repair_scene(new,report,request,design)
    assert contrast('FFFFFF','000000')==pytest.approx(21)


def test_job_lease_recovery_and_idempotency(tmp_path):
    store=Store('sqlite:///'+str(tmp_path/'db.sqlite'))
    jid=store.enqueue('generate',{'test':True},'test-key');assert store.enqueue('generate',{'test':True},'test-key')==jid
    with pytest.raises(ValueError):store.enqueue('generate',{'test':False},'test-key')
    job=store.claim();assert job.id==jid and store.claim() is None
    store.owned_update(jid,job.lease_owner,lease_until=0)
    recovered=store.claim();assert recovered.lease_owner!=job.lease_owner
    with pytest.raises(RuntimeError):store.owned_update(jid,job.lease_owner,stage='old')
    store.cancel(jid)
    with pytest.raises(RuntimeError):store.owned_update(jid,recovered.lease_owner,stage='old')


def test_missing_inference_is_explicit(tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_DIR',str(tmp_path));monkeypatch.setenv('AI_PROVIDER','polza');monkeypatch.delenv('POLZA_API_KEY',raising=False)
    from vktech.model import ModelGateway,ModelUnavailable
    from vktech.contracts import PresentationPlan
    with pytest.raises(ModelUnavailable):ModelGateway().structured('planning',{},PresentationPlan)


@pytest.mark.skipif(not shutil.which('soffice') or not shutil.which('pdftoppm'),reason='render tools not configured')
def test_end_to_end_worker(template_bytes,content,plan,tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_DIR',str(tmp_path/'artifacts'));store=Store('sqlite:///'+str(tmp_path/'db.sqlite'))
    design=import_template(template_bytes)
    tid=store.save_record('template','unknown.pptx',template_bytes,design.model_dump(),'pptx')
    cid=store.save_record('content','fixture',content.model_dump_json().encode(),content.model_dump(),'json')
    jid=store.enqueue('generate',{'template_id':tid,'content_id':cid,'brief':'Test fixture','slide_count':12})
    execute(store,store.claim(),FixtureGateway(plan))
    job=store.job(jid);assert job.state=='ready',job.error
    result=json.loads(job.result);assert len(result['variants'])==3
    assert len(result['slides'])==12 and len(result['selection'])==12
    assert set(result['selection'].values())<= {'A','B','C'}
    assert all(len(slide['options'])==3 for slide in result['slides'])
    assert all(artifact_path(result['presentation'][key]).exists() for key in ('pptx','pdf','html','audit','scene'))
    for v in result['variants'].values():
        assert all(artifact_path(v[k]).exists() for k in ('pptx','pdf','html','audit','scene'))
        audit=json.loads(artifact_path(v['audit']).read_text())
        assert any(i['category']=='contextual' and i['status']=='unknown' for i in audit['issues'])
    slide_id=result['slides'][0]['id'];before=result['selection'][slide_id]
    replacement=next(candidate for candidate in ('A','B','C') if candidate!=before)
    child=store.enqueue('compose',{'parent_job_id':jid,'request':{'slide_id':slide_id,'variant':replacement}})
    execute(store,store.claim(),FixtureGateway(plan))
    composed=json.loads(store.job(child).result)
    assert composed['selection'][slide_id]==replacement
    assert composed['slides'][0]['selected']==replacement
    assert artifact_path(composed['presentation']['pptx']).exists()
    exported_selection={**composed['selection'],slide_id:before}
    exported_job=store.enqueue('compose',{'parent_job_id':child,'request':{'selection':exported_selection}})
    execute(store,store.claim(),FixtureGateway(plan))
    exported=json.loads(store.job(exported_job).result)
    assert exported['selection']==exported_selection
    assert artifact_path(exported['presentation']['pdf']).exists()
