"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  BadgeDollarSign,
  CheckCircle2,
  Gauge,
  KeyRound,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  UserRound,
} from "lucide-react";

const fallback = {
  metrics: {
    requests: 0,
    tokens: 0,
    cost_usd: 0,
    latency_ms: { p50: 0, p95: 0, p99: 0 },
    cache: { hits: 0, misses: 0 },
    by_model: {},
    by_provider: {},
    keys: { count: 0, active: 0 },
    users: { count: 0, spend_usd: 0 },
    teams: { count: 0, spend_usd: 0 },
  },
  usage: { data: [] },
  pricing: { data: [] },
  guardrails: { enabled: false, checks: [] },
  features: {},
  keys: { data: [] },
  users: { data: [] },
  teams: { data: [] },
  audit: { data: [] },
  policies: {},
  plugins: { loaded: [] },
  providerHealth: { data: [] },
  alerts: { data: [] },
  compliance: { controls: {}, retention: {}, operator_required: [] },
};

export default function DashboardClient() {
  const [gatewayUrl, setGatewayUrl] = useState(
    process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:8000",
  );
  const [masterKey, setMasterKey] = useState("");
  const [data, setData] = useState(fallback);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [keyForm, setKeyForm] = useState({
    name: "production-app",
    models: "gpt-5-nano",
    allowed_routes: "chat,embeddings",
    budget_usd: "25",
    rpm_limit: "120",
    tpm_limit: "120000",
    user_id: "",
    team_id: "",
  });
  const [userForm, setUserForm] = useState({ user_id: "user-1", user_email: "", max_budget: "10" });
  const [teamForm, setTeamForm] = useState({ team_id: "platform", team_alias: "Platform", max_budget: "100" });

  const headers = useMemo(() => {
    return masterKey ? { Authorization: `Bearer ${masterKey}` } : {};
  }, [masterKey]);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [metrics, usage, pricing, guardrails, features, keys, users, teams, policies, plugins, providerHealth, alerts, compliance] = await Promise.all([
        getJson(gatewayUrl, "/v1/metrics", headers),
        getJson(gatewayUrl, "/v1/usage", headers),
        getJson(gatewayUrl, "/v1/pricing", headers),
        getJson(gatewayUrl, "/v1/guardrails", headers),
        getJson(gatewayUrl, "/v1/proxy/features", headers),
        getJson(gatewayUrl, "/v1/keys", headers).catch(() => ({ data: [] })),
        getJson(gatewayUrl, "/v1/proxy/users", headers).catch(() => ({ data: [] })),
        getJson(gatewayUrl, "/v1/proxy/teams", headers).catch(() => ({ data: [] })),
        getJson(gatewayUrl, "/v1/policies", headers).catch(() => ({})),
        getJson(gatewayUrl, "/v1/plugins", headers).catch(() => ({ loaded: [] })),
        getJson(gatewayUrl, "/v1/providers/health", headers).catch(() => ({ data: [] })),
        getJson(gatewayUrl, "/v1/alerts", headers).catch(() => ({ data: [] })),
        getJson(gatewayUrl, "/v1/compliance/status", headers).catch(() => ({ controls: {}, retention: {}, operator_required: [] })),
      ]);
      const audit = await getJson(gatewayUrl, "/v1/audit", headers).catch(() => ({ data: [] }));
      setData({ metrics, usage, pricing, guardrails, features, keys, users, teams, audit, policies, plugins, providerHealth, alerts, compliance });
    } catch (err) {
      setError(err.message || "Unable to load gateway dashboard data.");
    } finally {
      setLoading(false);
    }
  }

  async function submitUser(event) {
    event.preventDefault();
    await mutate("/v1/proxy/users", {
      ...userForm,
      max_budget: Number(userForm.max_budget || 0),
    });
  }

  async function submitTeam(event) {
    event.preventDefault();
    await mutate("/v1/proxy/teams", {
      ...teamForm,
      max_budget: Number(teamForm.max_budget || 0),
    });
  }

  async function submitKey(event) {
    event.preventDefault();
    await mutate("/v1/keys", {
      ...keyForm,
      models: csv(keyForm.models),
      allowed_routes: csv(keyForm.allowed_routes),
      budget_usd: Number(keyForm.budget_usd || 0),
      rpm_limit: Number(keyForm.rpm_limit || 0),
      tpm_limit: Number(keyForm.tpm_limit || 0),
    });
  }

  async function mutate(path, payload) {
    setLoading(true);
    setError("");
    setNotice("");
    try {
      const result = await postJson(gatewayUrl, path, headers, payload);
      setNotice(result.key ? `Created key ${result.preview}` : `${path} saved`);
      await load();
    } catch (err) {
      setError(err.message || "Unable to save control-plane change.");
    } finally {
      setLoading(false);
    }
  }

  async function toggleKey(key) {
    await mutate("/key/update", { key: key.preview, disabled: !key.disabled });
  }

  async function deleteKey(key) {
    await mutate("/key/delete", { key: key.preview });
  }

  async function deleteUser(user) {
    await mutate("/user/delete", { user_id: user.user_id });
  }

  async function deleteTeam(team) {
    await mutate("/team/delete", { team_id: team.team_id });
  }

  async function reloadConfig() {
    await mutate("/v1/config/reload", {});
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const byModel = Object.entries(data.metrics.by_model || {});
  const byProvider = Object.entries(data.metrics.by_provider || {});
  const usage = data.usage.data || [];
  const pricing = data.pricing.data || [];
  const checks = data.guardrails.checks || [];
  const features = Object.entries(data.features || {});
  const audit = data.audit.data || [];
  const policyRows = Object.entries(data.policies || {}).map(([name, value]) => ({ name, value }));
  const pluginRows = (data.plugins.loaded || []).map((name) => ({ name }));
  const providerHealth = data.providerHealth.data || [];
  const alerts = data.alerts.data || [];
  const complianceControls = Object.entries(data.compliance.controls || {});
  const retentionRows = Object.entries(data.compliance.retention || {}).map(([name, value]) => ({ name, value }));
  const operatorRequired = (data.compliance.operator_required || []).map((task) => ({ task }));

  return (
    <main className="shell">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <div className="mark">
              <Sparkles size={22} />
            </div>
            <div>
              <h1>justfastllm Dashboard</h1>
              <p>Proxy analytics and controls</p>
            </div>
          </div>
          <div className="connection">
            <input
              className="field"
              value={gatewayUrl}
              onChange={(event) => setGatewayUrl(event.target.value)}
              aria-label="Gateway URL"
            />
            <input
              className="field"
              value={masterKey}
              onChange={(event) => setMasterKey(event.target.value)}
              placeholder="Master key"
              type="password"
              aria-label="Master key"
            />
            <button className="icon-button" onClick={load} disabled={loading}>
              <RefreshCw size={16} />
              {loading ? "Loading" : "Refresh"}
            </button>
            <button className="icon-button ghost-button" onClick={reloadConfig} disabled={loading || !masterKey}>
              <RefreshCw size={16} />
              Reload config
            </button>
          </div>
        </div>
      </header>

      <section className="content">
        <div className="status-row">
          <Metric icon={<Activity />} label="Requests" value={formatInt(data.metrics.requests)} detail="Tracked gateway calls" />
          <Metric icon={<Gauge />} label="p99 speed" value={`${data.metrics.latency_ms?.p99 || 0} ms`} detail="Observed proxy latency" />
          <Metric icon={<BadgeDollarSign />} label="Spend" value={`$${formatCost(data.metrics.cost_usd)}`} detail="Estimated model cost" />
          <Metric icon={<KeyRound />} label="Virtual keys" value={formatInt(data.metrics.keys?.active || 0)} detail={`${formatInt(data.metrics.keys?.count || 0)} total keys`} />
        </div>

        <div className="grid">
          <Metric icon={<Sparkles />} label="Tokens" value={formatInt(data.metrics.tokens)} detail="Prompt + completion" />
          <Metric icon={<ShieldCheck />} label="Guardrails" value={data.guardrails.enabled ? "On" : "Off"} detail={`${checks.length} configured checks`} />
          <Metric icon={<CheckCircle2 />} label="Users" value={formatInt(data.metrics.users?.count || 0)} detail={`$${formatCost(data.metrics.users?.spend_usd || 0)} tracked spend`} />
          <Metric icon={<Gauge />} label="Teams" value={formatInt(data.metrics.teams?.count || 0)} detail={`$${formatCost(data.metrics.teams?.spend_usd || 0)} tracked spend`} />
        </div>

        {error ? <div className="error">{error}</div> : null}
        {notice ? <div className="notice">{notice}</div> : null}

        <div className="admin-grid">
          <Card label="Create user" title="User spend bucket">
            <form className="control-form" onSubmit={submitUser}>
              <input className="field" value={userForm.user_id} onChange={(event) => setUserForm({ ...userForm, user_id: event.target.value })} aria-label="User ID" />
              <input className="field" value={userForm.user_email} onChange={(event) => setUserForm({ ...userForm, user_email: event.target.value })} placeholder="Email" aria-label="User email" />
              <input className="field" value={userForm.max_budget} onChange={(event) => setUserForm({ ...userForm, max_budget: event.target.value })} aria-label="User budget" />
              <button className="icon-button" disabled={loading || !masterKey}>
                <UserRound size={16} />
                Save user
              </button>
            </form>
          </Card>
          <Card label="Create team" title="Team budget bucket">
            <form className="control-form" onSubmit={submitTeam}>
              <input className="field" value={teamForm.team_id} onChange={(event) => setTeamForm({ ...teamForm, team_id: event.target.value })} aria-label="Team ID" />
              <input className="field" value={teamForm.team_alias} onChange={(event) => setTeamForm({ ...teamForm, team_alias: event.target.value })} aria-label="Team alias" />
              <input className="field" value={teamForm.max_budget} onChange={(event) => setTeamForm({ ...teamForm, max_budget: event.target.value })} aria-label="Team budget" />
              <button className="icon-button" disabled={loading || !masterKey}>
                <CheckCircle2 size={16} />
                Save team
              </button>
            </form>
          </Card>
          <Card label="Create key" title="Access and limits">
            <form className="control-form wide-form" onSubmit={submitKey}>
              <input className="field" value={keyForm.name} onChange={(event) => setKeyForm({ ...keyForm, name: event.target.value })} aria-label="Key name" />
              <input className="field" value={keyForm.models} onChange={(event) => setKeyForm({ ...keyForm, models: event.target.value })} aria-label="Models" />
              <input className="field" value={keyForm.allowed_routes} onChange={(event) => setKeyForm({ ...keyForm, allowed_routes: event.target.value })} aria-label="Allowed routes" />
              <input className="field" value={keyForm.budget_usd} onChange={(event) => setKeyForm({ ...keyForm, budget_usd: event.target.value })} aria-label="Budget" />
              <input className="field" value={keyForm.rpm_limit} onChange={(event) => setKeyForm({ ...keyForm, rpm_limit: event.target.value })} aria-label="RPM limit" />
              <input className="field" value={keyForm.tpm_limit} onChange={(event) => setKeyForm({ ...keyForm, tpm_limit: event.target.value })} aria-label="TPM limit" />
              <input className="field" value={keyForm.user_id} onChange={(event) => setKeyForm({ ...keyForm, user_id: event.target.value })} placeholder="User ID" aria-label="Key user ID" />
              <input className="field" value={keyForm.team_id} onChange={(event) => setKeyForm({ ...keyForm, team_id: event.target.value })} placeholder="Team ID" aria-label="Key team ID" />
              <button className="icon-button" disabled={loading || !masterKey}>
                <KeyRound size={16} />
                Save key
              </button>
            </form>
          </Card>
        </div>

        <div className="split">
          <Card label="Traffic by model" title="Model usage, tokens, and spend">
            <Bars rows={byModel} metric="tokens" />
          </Card>
          <Card label="Provider routing" title="Provider distribution">
            <Bars rows={byProvider} metric="requests" />
          </Card>
        </div>

        <div className="split">
          <Card label="Guardrails" title="Runtime safety controls">
            <div className="guardrails">
              {checks.length ? checks.map((check) => (
                <div className="guardrail" key={check.name}>
                  <strong>{humanize(check.name)}</strong>
                  <span className={check.enabled ? "ok" : "warn"}>{check.enabled ? "Enabled" : "Disabled"}</span>
                </div>
              )) : <p>No guardrail data loaded.</p>}
            </div>
          </Card>
          <Card label="Feature inventory" title="Gateway control-plane coverage">
            <div className="feature-grid">
              {features.map(([name, enabled]) => (
                <div className="feature" key={name}>
                  <span>{humanize(name)}</span>
                  <span className={enabled ? "ok" : "warn"}>{enabled ? "Yes" : "No"}</span>
                </div>
              ))}
            </div>
          </Card>
        </div>

        <div className="tables">
          <Card label="Usage log" title="Recent gateway calls">
            <Table
              rows={usage.slice(0, 10)}
              columns={[
                ["model", "Model"],
                ["provider", "Provider"],
                ["total_tokens", "Tokens"],
                ["cost_usd", "Cost"],
                ["latency_ms", "Latency"],
                ["cache", "Cache"],
              ]}
            />
          </Card>
          <Card label="Pricing" title="Model pricing table">
            <Table
              rows={pricing}
              columns={[
                ["model", "Model"],
                ["input_per_1k_usd", "Input / 1K"],
                ["output_per_1k_usd", "Output / 1K"],
              ]}
            />
          </Card>
        </div>

        <div className="tables">
          <Card label="Virtual keys" title="Access, budget, and rate controls">
            <AdminRows
              rows={data.keys.data || []}
              columns={[
                ["preview", "Key"],
                ["name", "Name"],
                ["spend_usd", "Spend"],
                ["budget_usd", "Budget"],
                ["disabled", "Disabled"],
              ]}
              actions={[
                ["Toggle", toggleKey],
                ["Delete", deleteKey],
              ]}
            />
          </Card>
          <Card label="Speed" title="Latency percentiles">
            <Table
              rows={[
                { percentile: "p50", latency_ms: data.metrics.latency_ms?.p50 || 0 },
                { percentile: "p95", latency_ms: data.metrics.latency_ms?.p95 || 0 },
                { percentile: "p99", latency_ms: data.metrics.latency_ms?.p99 || 0 },
              ]}
              columns={[
                ["percentile", "Percentile"],
                ["latency_ms", "Latency ms"],
              ]}
            />
          </Card>
        </div>

        <div className="tables">
          <Card label="Users" title="Spend buckets">
            <AdminRows
              rows={data.users.data || []}
              columns={[
                ["user_id", "User"],
                ["user_email", "Email"],
                ["spend_usd", "Spend"],
                ["max_budget", "Budget"],
              ]}
              actions={[["Delete", deleteUser]]}
            />
          </Card>
          <Card label="Teams" title="Budget groups">
            <AdminRows
              rows={data.teams.data || []}
              columns={[
                ["team_id", "Team"],
                ["team_alias", "Alias"],
                ["spend_usd", "Spend"],
                ["max_budget", "Budget"],
              ]}
              actions={[["Delete", deleteTeam]]}
            />
          </Card>
        </div>

        <div className="tables">
          <Card label="Audit" title="Recent control-plane changes">
            <Table
              rows={audit.slice(0, 12)}
              columns={[
                ["action", "Action"],
                ["target_type", "Target"],
                ["target_id", "ID"],
                ["actor", "Actor"],
                ["created_at", "Created"],
              ]}
            />
          </Card>
          <Card label="Endpoint coverage" title="Gateway routes">
            <div className="feature-grid">
              {["chat", "messages", "completions", "responses", "embeddings", "images", "audio", "moderations", "rerank", "agents"].map((name) => (
                <div className="feature" key={name}>
                  <span>{humanize(name)}</span>
                  <span className="ok">Ready</span>
                </div>
              ))}
            </div>
          </Card>
        </div>

        <div className="tables">
          <Card label="Policies" title="Runtime enforcement">
            <Table
              rows={policyRows}
              columns={[
                ["name", "Policy"],
                ["value", "Value"],
              ]}
            />
          </Card>
          <Card label="Plugins" title="Loaded hooks">
            <Table
              rows={pluginRows}
              columns={[
                ["name", "Module"],
              ]}
            />
          </Card>
        </div>

        <div className="tables">
          <Card label="Provider health" title="Routing status">
            <Table
              rows={providerHealth}
              columns={[
                ["provider", "Provider"],
                ["status", "Status"],
                ["requests", "Requests"],
                ["errors", "Errors"],
                ["error_rate", "Error rate"],
                ["latency_ms", "Latency"],
              ]}
            />
          </Card>
          <Card label="Alerts" title="Actionable signals">
            <Table
              rows={alerts}
              columns={[
                ["severity", "Severity"],
                ["type", "Type"],
                ["target", "Target"],
                ["message", "Message"],
              ]}
            />
          </Card>
        </div>

        <div className="tables">
          <Card label="Compliance" title="Technical controls">
            <div className="feature-grid">
              {complianceControls.map(([name, enabled]) => (
                <div className="feature" key={name}>
                  <span>{humanize(name)}</span>
                  <span className={enabled ? "ok" : "warn"}>{enabled ? "Yes" : "No"}</span>
                </div>
              ))}
            </div>
          </Card>
          <Card label="Retention" title="Lifecycle and operator tasks">
            <Table
              rows={retentionRows}
              columns={[
                ["name", "Setting"],
                ["value", "Value"],
              ]}
            />
            <Table
              rows={operatorRequired.slice(0, 5)}
              columns={[["task", "Operator task"]]}
            />
          </Card>
        </div>
      </section>
    </main>
  );
}

function Metric({ icon, label, value, detail }) {
  return (
    <div className="card metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
      <div className="ok">{icon}</div>
    </div>
  );
}

function Card({ label, title, children }) {
  return (
    <section className="card">
      <div className="section-head">
        <div>
          <div className="section-label">{label}</div>
          <h2>{title}</h2>
        </div>
      </div>
      {children}
    </section>
  );
}

function Bars({ rows, metric }) {
  const max = Math.max(1, ...rows.map(([, value]) => Number(value?.[metric] || 0)));
  if (!rows.length) return <p>No traffic has been recorded yet.</p>;
  return (
    <div className="bars">
      {rows.map(([name, value]) => {
        const amount = Number(value?.[metric] || 0);
        return (
          <div className="bar-row" key={name}>
            <strong>{name}</strong>
            <div className="bar-track">
              <div className="bar-fill" style={{ width: `${Math.max(4, (amount / max) * 100)}%` }} />
            </div>
            <span>{formatInt(amount)}</span>
          </div>
        );
      })}
    </div>
  );
}

function Table({ rows, columns }) {
  if (!rows.length) return <p>No data loaded.</p>;
  return (
    <table className="table">
      <thead>
        <tr>
          {columns.map(([, label]) => <th key={label}>{label}</th>)}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => (
          <tr key={row.call_id || row.preview || row.model || index}>
            {columns.map(([key]) => <td key={key}>{formatCell(row[key])}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function AdminRows({ rows, columns, actions }) {
  if (!rows.length) return <p>No data loaded.</p>;
  return (
    <table className="table">
      <thead>
        <tr>
          {columns.map(([, label]) => <th key={label}>{label}</th>)}
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => (
          <tr key={row.preview || row.user_id || row.team_id || index}>
            {columns.map(([key]) => <td key={key}>{formatCell(row[key])}</td>)}
            <td>
              <div className="row-actions">
                {actions.map(([label, action]) => (
                  <button className="text-button" key={label} onClick={() => action(row)}>
                    {label}
                  </button>
                ))}
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

async function getJson(baseUrl, path, headers) {
  const response = await fetch(`${baseUrl.replace(/\/$/, "")}${path}`, {
    headers,
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`${path} returned HTTP ${response.status}`);
  }
  return response.json();
}

async function postJson(baseUrl, path, headers, payload) {
  const response = await fetch(`${baseUrl.replace(/\/$/, "")}${path}`, {
    method: "POST",
    headers: {
      ...headers,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`${path} returned HTTP ${response.status}`);
  }
  return response.json();
}

function formatInt(value) {
  return new Intl.NumberFormat("en-US").format(Number(value || 0));
}

function formatCost(value) {
  return Number(value || 0).toFixed(6);
}

function formatCell(value) {
  if (typeof value === "number") {
    return Number.isInteger(value) ? formatInt(value) : value.toFixed(6);
  }
  if (Array.isArray(value)) return value.join(", ");
  if (value && typeof value === "object") return JSON.stringify(value);
  return value ?? "";
}

function humanize(value) {
  return String(value).replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function csv(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}
