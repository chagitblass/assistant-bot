FROM python:3.12-slim

WORKDIR /app

# System deps for Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ARG CACHE_BUST=1
RUN echo "Cache bust: $CACHE_BUST"
COPY . .

ENV PORT=8080

CMD ["gunicorn", "main:app", "--workers", "2", "--timeout", "30", "--bind", "0.0.0.0:8080"]
