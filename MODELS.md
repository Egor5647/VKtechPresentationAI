# Модели

## Qwen3-VL-4B-Instruct-4bit

- Репозиторий: `mlx-community/Qwen3-VL-4B-Instruct-4bit`
- Фиксированная revision: `2fd8dacbdb8f1e54b8c005f081ec5bf79c56376b`
- 4 437 815 808 параметров по Safetensors metadata; файл весов занимает
  3 093 767 283 байта
- исходная модель и конверсия имеют открытые веса, Apache 2.0
- Роли: планирование содержания и контекстуальный vision-аудит
- Карточки: https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct и
  https://huggingface.co/mlx-community/Qwen3-VL-4B-Instruct-4bit

На текущем MacBook Pro с M4 Pro и 24 GB unified memory модель запускается через
MLX-VLM. Это сохраняет запас памяти для рендера, API и worker. Среда инференса
изолирована в `.mlx-venv`, потому что MLX-VLM и API имеют разные наборы серверных
зависимостей. Запуск: `scripts/start_mlx_model.sh`.

Для структурированного ответа gateway передаёт полную Pydantic JSON Schema в
`response_format=json_schema`; MLX-VLM ограничивает декодирование этой схемой.
Локальный профиль проверяет все C01–C11, но просит модель возвращать только
доказанные нарушения (`MODEL_AUDIT_MODE=failures`); отсутствующие результаты
становятся `unknown`. Это удерживает три vision-проверки в лимите 300 секунд.
Полный режим с явными pass/not_applicable можно включить значением `complete` на
более быстром endpoint.
В финале `MODEL_PROFILE=final` требует `VK_BASE_URL`; тихий переход на локальный
endpoint запрещён.

## Z-Image-Turbo

- Репозиторий: `Tongyi-MAI/Z-Image-Turbo`
- Фиксированная revision: `f332072aa78be7aecdf3ee76d5c247082da564a6`
- 10 261 196 515 параметров во всём pipeline по заголовкам Safetensors
- Открытые веса, Apache 2.0; 8 шагов в карточке модели
- Роль: иллюстрация по запросу пользователя
- Карточка: https://huggingface.co/Tongyi-MAI/Z-Image-Turbo

Числовые графики, таблицы и схемы T2I не рисует. Изображение имеет prompt, model
revision и происхождение `generated`; оно не подтверждает факты.

## Контроль допуска

`config/models.yaml` проходит проверку перед первым вызовом. Для LLM/VLM лимит
35B, для T2I применяется более строгий лимит 20B. Допустимы только Apache 2.0 и
MIT. Каждый ответ проходит Pydantic-схему; после одной корректирующей попытки
задание завершается ошибкой. Тестовые doubles находятся только в `tests/` и не
подключаются из API или конфигурации production.

Перед приёмкой измеряются соблюдение схем, полнота обязательных фактов, качество
русского текста и vision-проверки, latency, пик памяти и работа на неизвестном
шаблоне. Версия модели не обновляется автоматически.
