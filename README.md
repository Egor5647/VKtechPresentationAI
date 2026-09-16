# VK Tech Presentation AI

Сервис принимает неизвестный PPTX-шаблон и контент-пакет, формирует единый план
содержания, создаёт три варианта вёрстки, проводит аудит и экспортирует HTML,
редактируемый PPTX и PDF. Варианты сохраняют одинаковые факты и порядок слайдов,
но используют разные композиции и способы визуализации.

## Текущее состояние

Работает полный программный путь: безопасный импорт PPTX, Design IR и Content IR,
планирование в переключаемом режиме `quality`/`fast`, три Scene IR, нативные текст, таблицы, графики и
SmartArt, экспорты, превью, детерминированный и контекстуальный аудит, выбор
исправлений и версионирование. Внешний инференс не заменяется тестовыми ответами:
без настроенного endpoint задача переходит в `awaiting_input`.

Контрольный прогон с явно синтетическим планом создаёт 3 × 3 колоды на трёх
шаблонах. Он проверяет формат и рендер, но не служит демонстрацией качества модели.
Для приёмочного прогона нужен фактический материал будущей презентации. Под
«контент-пакетом» здесь понимаются тема, тексты, факты, числа, таблицы, изображения
и источники; это может быть один PPTX, PDF, документ или структурированный бриф.

## Быстрый запуск

Требуются Docker с Compose и не менее 8 CPU, 16 GB RAM и 20 GB свободного места.
GPU использует отдельный сервер инференса; контейнеры приложения не включают веса.

```bash
cp .env.example .env
# Укажите MODEL_BASE_URL или параметры VK для финального профиля.
docker compose up --build
```

Откройте `http://127.0.0.1:8000`. API доступен на `/docs`. В отдельном процессе
должен работать worker из `compose.yaml`. Артефакты лежат в volume `artifacts`,
PostgreSQL — в volume `postgres`.

Локальный запуск без Docker:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[test]'
cd frontend && pnpm install && pnpm run build && cd ..
export DATABASE_URL='postgresql+psycopg://vktech:vktech@127.0.0.1:5432/vktech'
export MODEL_BASE_URL='http://127.0.0.1:8001/v1'
export MODEL_MODE=quality  # quality: Ministral 14B; fast: Qwen 4B
./scripts/start_mlx_model.sh
.venv/bin/uvicorn vktech.api:app --host 127.0.0.1 --port 8000
.venv/bin/python -m vktech.worker
```

Нужны `soffice` и `pdftoppm` в `PATH`; пути можно задать переменными `SOFFICE` и
`PDFTOPPM`. `FONT_DIRS` содержит разделённые системным символом пути к доступным
TTF/OTF для точной проверки переноса текста. Загруженные PPTX могут содержать
встроенные шрифты, но это не гарантирует их доступность LibreOffice или браузеру.

## Настройка моделей

На Apple Silicon режим отбора (`MODEL_PROFILE=selection`) использует локальный
OpenAI-совместимый сервер MLX-VLM. `quality` выбран по умолчанию; смена режима
требует перезапуска model server и того же значения `MODEL_MODE` у worker:

```dotenv
MODEL_BASE_URL=http://127.0.0.1:8001/v1
MODEL_API_KEY=
MODEL_MODE=quality
MODEL_NAME=
MODEL_TIMEOUT_SECONDS=300
```

| Режим | Модель | Локальные веса | Полный прогон |
| --- | --- | --- | --- |
| `quality` | Ministral 3 14B Instruct 4-bit | около 8,42 GB | 222,993 с |
| `fast` | Qwen3-VL-4B Instruct 4-bit | около 3,09 GB | 99,176 с |

Оба замера включают три варианта по 12 слайдов, PPTX/PDF/HTML и vision-аудит и
выполнены на синтетическом fixture. Название `quality` обозначает основной профиль;
превосходство по качеству должно быть подтверждено на размеченном реальном наборе.

Финальный режим (`MODEL_PROFILE=final`) требует предоставленный VK endpoint и
доступный на нём model ID. После получения доступа этот ID добавляется в список
разрешённых aliases в `config/models.yaml` и проверяется отдельным прогоном:

```dotenv
VK_BASE_URL=https://vk.example/v1
VK_API_KEY=...
VK_MODEL_NAME=<approved-vk-model-id>
```

Ключи хранятся только в `.env`, файл исключён из Git. Если протокол VK отличается
от OpenAI Chat Completions с `json_schema`, потребуется отдельный транспорт в
`model.py`.

Для генерации изображения задаются `T2I_BASE_URL` и `T2I_API_KEY`. Endpoint должен
возвращать base64 PNG/JPEG/WebP от модели Z-Image-Turbo. Пользователь может
отключить T2I для запуска. Планировщик никогда не использует изображение как
источник фактов.

## Формат контента

Поддерживаются PPTX, PDF, UTF-8 TXT/Markdown, CSV и JSON `ContentIR`. PPTX и PDF
импортируются как источники текста. CSV должен иметь категории в первом столбце,
названия рядов в первой строке и числовые значения в остальных ячейках. Для
сложного пакета используйте JSON по схеме `/openapi.json`; `source` обязателен для
каждого факта и набора данных. Изображения сначала загружаются через `/api/assets`,
после чего возвращённый относительный `path` можно указать в `ContentIR.assets`.

## Проверки

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/verify_templates.py /path/to/templates --output output/smoke
cd frontend && pnpm run build
```

`verify_templates.py` использует подписанный синтетический fixture и не вызывает
модель. Он проверяет девять PPTX, PDF, HTML, Scene IR и отчётов аудита. PostgreSQL
проверяется при наличии `TEST_DATABASE_URL`. Результаты smoke-прогона исключены
из Git.

## Ограничения

- Серверный рендер LibreOffice может отличаться от PowerPoint при отсутствии
  корпоративных шрифтов. В предоставленных материалах отдельных файлов шрифтов
  нет: используются системные шрифты и настройки PPTX, а аудит возвращает
  `unknown` при невозможности подтвердить метрики.
- Контекстуальный аудит зависит от vision-возможностей настроенного Qwen endpoint.
- Настоящий SmartArt проверен в Microsoft PowerPoint: PowerPoint показывает вкладку
  «Конструктор», позволяет добавлять фигуры и менять макет. LibreOffice отображает
  синхронизированный drawing cache и не редактирует семантическое дерево SmartArt.
- Поддержка шаблонов PDF и изображений остаётся расширением. Основной адаптер — PPTX.
- Публичный доступ требует внешней аутентификации, TLS, антивирусной проверки и
  объектного хранилища; текущая конфигурация слушает только localhost.

Подробности: [ARCHITECTURE.md](ARCHITECTURE.md), [MODELS.md](MODELS.md),
[MODEL_BENCHMARK.md](MODEL_BENCHMARK.md), [AUDIT.md](AUDIT.md). Проект архитектуры:
[VK_Tech_Architecture.docx](output/documents/VK_Tech_Architecture.docx).
