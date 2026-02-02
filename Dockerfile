FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgpiod2 \
        python3-libgpiod \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src/ /app/src/

RUN pip install --no-cache-dir .

ENV FANCTRL_CONFIG=/config/fanctrl.toml \
    PYTHONUNBUFFERED=1

EXPOSE 9101

CMD ["python", "-m", "fanctrl.main"]
