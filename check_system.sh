#!/bin/bash
# Обертка для запуска проверки системы с автоматической активацией venv

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Проверяем наличие venv
if [ -d "venv" ]; then
    echo "Активация виртуального окружения..."
    source venv/bin/activate
    python3 check_system.py "$@"
    exit_code=$?
    deactivate
    exit $exit_code
else
    echo "ВНИМАНИЕ: Виртуальное окружение не найдено!"
    echo "Используется системный Python (могут быть ошибки с модулями)"
    python3 check_system.py "$@"
    exit $?
fi
