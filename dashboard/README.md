# justfastllm Dashboard

Next.js + JavaScript + App Router dashboard for gateway operations.

## Run Locally

```bash
npm install
npm run dev
```

Open `http://localhost:3000`.

## Configuration

```bash
NEXT_PUBLIC_GATEWAY_URL=http://localhost:8000
```

The dashboard can also change the gateway URL from the top bar at runtime.

## Views

- Requests, tokens, spend, and speed percentiles
- Provider and model distribution
- Guardrail state
- Virtual keys, budgets, and rate limits
- User, team, and virtual-key creation
- Virtual-key disable/delete actions
- User and team delete actions
- Config reload action
- User and team spend rollups
- Usage events
- Audit history
- Provider health and alerts
- Compliance controls, retention windows, and operator tasks
- Pricing table
- Gateway feature inventory
