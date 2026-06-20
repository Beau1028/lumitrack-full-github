FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium
ENV ESCAPE_ROOM_MONITOR_HOME=/var/data
ENV LUMITRACK_CLOUD_SAFE=1
ENV LUMITRACK_MAX_PARALLEL_ORIGINS=2
ENV LUMITRACK_NAVIGATION_TIMEOUT_MS=15000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        chromium \
        fonts-noto-cjk \
        procps \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /var/data/data /var/data/logs

EXPOSE 8501

CMD ["python", "render_start.py"]
