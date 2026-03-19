# Пример бота для GAICA

Этот архив показывает рабочую базовую структуру бота под актуальный socket API GAICA.

Что внутри:

- `main.py` — точка входа
- `gaica_bot/models.py` — OOP-модели сообщений и состояния игры
- `gaica_bot/client.py` — сетевой клиент и цикл обработки сообщений
- `gaica_bot/sample_bot.py` — простой бот-пример
- `gaica_bot/smart_bot.py` — более сильный эвристический бот для локальных матчей
- `tests/test_smart_bot.py` — базовые проверки принятия решений

Что умеет `SmartBot`:

- уклоняться от опасных пуль рядом с траекторией
- подбирать оружие и предпочитать `Uzi`
- удерживать выгодную дистанцию в зависимости от оружия
- стрелять только при относительно чистой линии прострела
- использовать kick вблизи и выбрасывать пустое оружие

Быстрый старт:

```bash
python participant_bot_template/main.py 127.0.0.1 9001
```

Запуск браузерного матча из корня репозитория:

```bash
python scripts/start_browser_match.py \
  --bot-a-cmd "{python} participant_bot_template/main.py {host} {port}" \
  --bot-b-cmd "{python} participant_bot_template/main.py {host} {port}"
```

Можно подставить и другие команды ботов, если они принимают `host` и `port` последними аргументами.
