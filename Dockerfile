FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml requirements.lock README.md alembic.ini ./
RUN pip install --no-cache-dir -r requirements.lock

COPY src ./src
COPY migrations ./migrations

RUN pip install --no-cache-dir --no-deps .

RUN useradd --create-home --uid 10001 bot && mkdir -p /app/data && chown -R bot:bot /app
USER bot

CMD ["python", "-m", "vk_chat_bot"]
