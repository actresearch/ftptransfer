FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y bash \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY BaseTokenBackend.py .
COPY watchdog_runner.py .

EXPOSE 5000

CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:5000 --worker-class gevent --workers ${GUNICORN_WORKERS:-3} --worker-connections ${GUNICORN_WORKER_CONNECTIONS:-100} --timeout 0 app:app"]
