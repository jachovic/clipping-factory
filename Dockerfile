FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core postgresql-client \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 factory
WORKDIR /app
COPY pyproject.toml ./
COPY factory ./factory
RUN pip install --no-cache-dir .
USER factory
ENV FACTORY_DATA_DIR=/data HF_HUB_DISABLE_XET=1 PYTHONUNBUFFERED=1
VOLUME /data
CMD ["factory", "worker"]
