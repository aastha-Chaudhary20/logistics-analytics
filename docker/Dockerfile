# StellarKeep AI — single app container (backend + RAG API + frontend)
# Ollama runs on the HOST (keeps Apple Metal / NVIDIA GPU acceleration,
# which is lost if Ollama itself is containerized on Mac).
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg build-essential \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y nodejs \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY docker/requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

ENV OLLAMA_HOST=http://host.docker.internal:11434
ENV RAG_CONFIG_MODE=cpu_fast
ENV GENERATION_MODEL=qwen3:0.6b

EXPOSE 3000 8000 8001
CMD ["python", "run_system.py"]
