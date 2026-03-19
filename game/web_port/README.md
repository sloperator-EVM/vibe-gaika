# Web Port (Python + HTML/JS)

Этот модуль содержит web-порт игры с backend-симуляцией на Python и визуализацией в браузере.

## Запуск

Из корня репозитория:

```bash
python -m web_port.main --bot-port 9001 --web-port 8080
```

По умолчанию карта выбирается случайно для матча.

После старта:

- TCP для ботов: `127.0.0.1:9001`
- Web UI: `http://127.0.0.1:8080/`

## Запуск сразу с тестовыми ботами

Основная команда:

```bash
python -m web_port.main
```

В этом режиме после завершения раунда автоматически стартует следующий через `1` секунду.

## Протокол TCP бота

Сервер шлёт JSON-lines:

- `{"type":"hello", ...}`
- `{"type":"round_start", ...}`
- `{"type":"tick", ...}`
- `{"type":"round_end", ...}`

Бот отправляет JSON-lines:

```json
{
  "type": "command",
  "seq": 42,
  "move": [1, 0],
  "aim": [1, 0],
  "shoot": false,
  "kick": false,
  "interact": true
}
```
