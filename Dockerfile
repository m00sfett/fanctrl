FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        libgpiod2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src/ /app/src/

RUN pip install --no-cache-dir gpiod .

ENV FANCTRL_CONFIG=/config/fanctrl.toml \
    PYTHONUNBUFFERED=1

EXPOSE 9101

CMD ["python", "-m", "fanctrl.main"]
