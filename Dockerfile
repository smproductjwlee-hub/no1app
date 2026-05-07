FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app ./app
COPY static ./static
COPY main.py ./

# /app/data is the persistent-disk mount point (when Render disk is attached);
# uploaded files live under it via a symlink so they survive deploys too.
RUN mkdir -p /app/data \
 && rm -rf /app/static/uploads \
 && ln -s /app/data/uploads /app/static/uploads

EXPOSE 8000

CMD mkdir -p /app/data/uploads \
 && uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'
