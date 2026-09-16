# Модели

## Режимы текста и vision

`MODEL_MODE` выбирает локальный профиль без изменения контрактов сервиса:

| Режим | Модель | Назначение | Параметры | Веса |
| --- | --- | --- | ---: | ---: |
| `quality` — по умолчанию | `mlx-community/Ministral-3-14B-Instruct-2512-4bit` | Основной кандидат для планирования и vision-аудита | 13 945 032 240 | 8 424 582 838 байт |
| `fast` | `mlx-community/Qwen3-VL-4B-Instruct-4bit` | Быстрый прогон и разработка | 4 437 815 808 | 3 093 767 283 байта |

Обе модели мультимодальные, имеют открытые веса и Apache 2.0. Зафиксированные
revision и aliases находятся в `config/models.yaml`. Роли одинаковы:
планирование содержания и контекстуальный vision-аудит.

Карточки:

- https://huggingface.co/mistralai/Ministral-3-14B-Instruct-2512
- https://huggingface.co/mlx-community/Ministral-3-14B-Instruct-2512-4bit
- https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct
- https://huggingface.co/mlx-community/Qwen3-VL-4B-Instruct-4bit

Запуск основного режима:

```bash
MODEL_MODE=quality ./scripts/start_mlx_model.sh
```

Быстрый режим:

```bash
MODEL_MODE=fast ./scripts/start_mlx_model.sh
```

Model server загружает один checkpoint. При смене режима его нужно перезапустить;
worker должен получить то же значение `MODEL_MODE`. `MODEL_NAME` оставляют пустым,
если не требуется разрешённый alias из manifest.

На MacBook Pro M4 Pro с 24 GB unified memory оба режима прошли полный pipeline.
`quality` уложился в 300 секунд с пиком памяти модели около 10 GB, `fast` — около
3,2 GB. Название `quality` означает основной кандидат и увеличенный размер модели,
а не доказанное превосходство: содержательное качество сравнивается на размеченном
реальном наборе.

Gateway передаёт полную Pydantic JSON Schema в `response_format=json_schema`.
Для планирования запрошенное число слайдов дополнительно фиксируется через
`minItems=maxItems`. Локальный аудит возвращает только доказанные нарушения;
пропущенные результаты получают `unknown`.

В финале `MODEL_PROFILE=final` требует `VK_BASE_URL` и разрешённый `VK_MODEL_NAME`;
тихий переход на локальный endpoint запрещён.

## Z-Image-Turbo

- Репозиторий: `Tongyi-MAI/Z-Image-Turbo`
- Revision: `f332072aa78be7aecdf3ee76d5c247082da564a6`
- 10 261 196 515 параметров во всём pipeline
- Открытые веса, Apache 2.0
- Роль: иллюстрация по запросу пользователя
- Карточка: https://huggingface.co/Tongyi-MAI/Z-Image-Turbo

Числовые графики, таблицы и схемы T2I не рисует. Изображение имеет prompt, model
revision и происхождение `generated`; оно не подтверждает факты. Реальный T2I
вызов ожидает доступного endpoint.

## Контроль допуска

`config/models.yaml` проверяется перед первым вызовом. Для LLM/VLM действует лимит
35B, для T2I — 20B. Допустимы только Apache 2.0 и MIT. Каждый ответ проходит
Pydantic-схему; после одной корректирующей попытки задание завершается ошибкой.
Тестовые doubles находятся только в `tests/`.

Перед приёмкой измеряются полнота обязательных фактов, качество русского текста,
vision precision/recall, доля `unknown`, latency, пик памяти и работа на неизвестном
шаблоне. Версии моделей не обновляются автоматически.
