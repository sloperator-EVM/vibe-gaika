# Пример бота для GAICA

Этот шаблон теперь ориентирован на **AI-пайплайн**, а не на ручные эвристики.

Что внутри:

- `main.py` — точка входа, запускает `AIBot`
- `gaica_bot/models.py` — OOP-модели сообщений и состояния игры
- `gaica_bot/client.py` — сетевой клиент и цикл обработки сообщений
- `gaica_bot/ai_features.py` — преобразование `tick` в фиксированный вектор признаков
- `gaica_bot/ai_policy.py` — компактная MLP-политика без внешних ML-зависимостей
- `gaica_bot/ai_bot.py` — бот-инференс поверх модели
- `models/bootstrap_policy.json` — стартовые веса политики
- `tests/test_ai_bot.py` — базовые проверки извлечения признаков и инференса

## Как это работает

На каждом тике бот:

1. собирает фиксированный вектор признаков из `tick` и `snapshot`;
2. прогоняет его через маленькую MLP-сеть;
3. переводит выходы сети в `move`, `aim` и action-флаги.

Это сделано специально так, чтобы дальше можно было:

- заменить `bootstrap_policy.json` на обученные веса;
- учить policy offline или в self-play без смены socket-обвязки;
- быстро проверять разные модели через переменную окружения `GAICA_MODEL_PATH`.

## Быстрый старт

```bash
python participant_bot_template/main.py 127.0.0.1 9001
```

Использовать другой файл весов:

```bash
GAICA_MODEL_PATH=participant_bot_template/models/bootstrap_policy.json \
python participant_bot_template/main.py 127.0.0.1 9001
```

Запуск браузерного матча из корня репозитория:

```bash
python scripts/start_browser_match.py \
  --bot-a-cmd "{python} participant_bot_template/main.py {host} {port}" \
  --bot-b-cmd "{python} participant_bot_template/main.py {host} {port}"
```

## Дальше по AI

Следующий разумный шаг — сделать отдельный pipeline для:

- сбора датасета из матчей;
- тренировки новой политики;
- сохранения весов обратно в JSON-формат, совместимый с `MLPPolicy`.
