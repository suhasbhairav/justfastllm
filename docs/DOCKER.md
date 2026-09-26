# Docker

Build and run the gateway as a container:

```bash
cp .env.example .env
docker build -t justfastllm:local .
docker run --rm --env-file .env -p 8000:8000 justfastllm:local
```

The image runs as the non-root `justfastllm` user and includes a `/health` healthcheck.

Run with Postgres using Docker Compose:

```bash
cp .env.example .env
# Set POSTGRES_PASSWORD, JUSTFASTLLM_MASTER_KEY, JUSTFASTLLM_PRIVACY_CONTACT,
# JUSTFASTLLM_SECURITY_CONTACT, JUSTFASTLLM_SUBPROCESSORS_URL,
# and JUSTFASTLLM_DPA_URL before production compose deployment.
docker compose --env-file .env up --build
```

The Compose file enables the compliance readiness health gate and uses Postgres for control-plane state, user memory, and response-cache settings.

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
