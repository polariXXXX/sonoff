FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN pip install -r requirements.lock
COPY pyproject.toml /app/
COPY src /app/src
RUN pip install --no-deps . && useradd --uid 10001 --create-home controller \
    && mkdir /data && chown controller:controller /data
USER 10001:10001
ENV DATA_DIR=/data CONFIG_PATH=/config/config.json
EXPOSE 8000
CMD ["uvicorn", "sonoff.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log", "--no-proxy-headers"]
