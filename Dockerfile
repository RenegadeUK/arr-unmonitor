FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=5200
ENV SETTINGS_PATH=/config/settings.json
ENV CHANGE_LOG_PATH=/config/change-log.jsonl
ENV LOG_PATH=/config/app-log.jsonl

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 5200
VOLUME ["/config"]

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5200/health')" || exit 1

CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:5200", "app.main:app"]