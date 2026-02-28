FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=5200
ENV SETTINGS_PATH=/config/settings.json

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 5200
VOLUME ["/config"]

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5200", "app.main:app"]