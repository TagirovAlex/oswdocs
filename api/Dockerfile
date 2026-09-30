# СЭД Фаза 0 Задача 3 — образ api.
# Контекст сборки: ./api (см. docker-compose.yml Задачи 1).
# worker использует ТОТ ЖЕ образ с другой командой (celery/rq — в Фазе 4,
# команда переопределяется в compose, отдельный Dockerfile для worker не нужен).
# LibreOffice (soffice) для DOCX->PDF — системный пакет, ставится в образ
# (TASKS_DEPLOY.md шаг 6, вариант a), чтобы печать работала сразу;
# в pip-зависимости образа НЕ включается.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Зависимости отдельным слоем для кэширования.
# Реальные зависимости — в Фазах 2-4, сейчас requirements.txt — заглушка.
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Только нужный код (реальный код app/ появляется в Фазах 2-4).
COPY ./app ./app

# LibreOffice (soffice) для генерации PDF из DOCX (W3a): headless-конвертер.
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*

# Non-root запуск.
RUN groupadd -r appuser && useradd -r -g appuser appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# CMD для api; worker переопределяет команду в compose (Фаза 4).
# Реальная точка входа (app.main) — в Фазе 2.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
