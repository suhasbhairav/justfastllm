# Docker

Build and run the gateway as a container:

```bash
cp .env.example .env
docker build -t justfastllm:local .
docker run --rm --env-file .env -p 8000:8000 justfastllm:local
```

The image runs as the non-root `justfastllm` user and includes a `/health` healthcheck.

Run with Redis using Docker Compose:

```bash
cp .env.example .env
docker compose --env-file .env up --build
```

The gateway will be available at:

```text
http://localhost:8000
```

Useful checks:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/openapi.json
```

Verify image build when the Docker daemon is running:

```bash
docker build -t justfastllm:test .
docker run --rm --env-file .env -p 8000:8000 justfastllm:test
```

## Ollama From Docker

If Ollama is running on the host machine, set this in `.env` for Docker Desktop:

```bash
OLLAMA_BASE_URL=http://host.docker.internal:11434/v1
```

On Linux, use the host gateway address for your Docker network, or run the gateway with host networking if appropriate for your environment.
