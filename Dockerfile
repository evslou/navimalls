# ----------------------------------------------------------------------
# Stage 1: build (установка зависимостей)
# ----------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /app
COPY requirements.txt .

# Устанавливаем зависимости во временную директорию /install
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ----------------------------------------------------------------------
# Stage 2: runtime
# ----------------------------------------------------------------------
FROM python:3.11-slim

# Устанавливаем CA-сертификаты (исправляет SSL ошибки)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    update-ca-certificates --fresh && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем зависимости из первого этапа
COPY --from=builder /install /usr/local

# Копируем исходный код приложения
COPY . .

# Создаём непривилегированного пользователя для безопасности
RUN adduser --disabled-password --gecos "" appuser && \
    chown -R appuser /app

USER appuser

# Порт, который будет слушать Gunicorn (должен совпадать с пробросом в docker-compose)
EXPOSE 49200

# Запуск Gunicorn с привязкой к порту 49200
CMD ["gunicorn", "--workers", "3", "--bind", "0.0.0.0:49200", "--timeout", "60", "app:app"]