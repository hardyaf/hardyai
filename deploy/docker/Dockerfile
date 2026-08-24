FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/jarvis

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --requirement requirements.txt \
    && python -m pip check

COPY app ./app
COPY scripts ./scripts
COPY .env.example ./.env.example

RUN chmod -R a+rX /opt/jarvis/app /opt/jarvis/scripts
RUN mkdir -p /opt/jarvis/data /opt/jarvis/secrets/live

EXPOSE 8000

CMD ["sh", "-c", "umask 077 && exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
