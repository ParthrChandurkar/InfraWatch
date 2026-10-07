// Main InfraWatch dashboard experience.
import {
  Activity,
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Clock,
  Cpu,
  ExternalLink,
  Gauge,
  GitBranch,
  HardDrive,
  Layers,
  RefreshCw,
  RotateCcw,
  Rocket,
  Server,
  ShieldCheck,
  Terminal,
  Trash2,
  Zap,
} from "lucide-react";
import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  apiMode,
  deleteDeployment,
  deployService,
  getLogs,
  getMetrics,
  getWorkloadHealth,
  isDemoMode,
  listAuditLogs,
  listDeployments,
  resetSandbox,
  rollbackDeployment,
  subscribeToApiFallback,
} from "./api";
import type {
  AuditLogEntry,
  DeploymentRecord,
  DeploymentStatus,
  LogsResponse,
  ServiceMetrics,
  WorkloadHealth,
} from "./types";

const STATUS_THEME: Record<DeploymentStatus, { color: string; label: string; icon: ReactNode }> = {
  Running: { color: "#3ddc97", label: "Healthy", icon: <CheckCircle2 size={14} /> },
  Pending: { color: "#f5b942", label: "Pending", icon: <Clock size={14} /> },
  Deploying: { color: "#4da3ff", label: "Deploying", icon: <RefreshCw size={14} /> },
  Failed: { color: "#ff5f6d", label: "Failed", icon: <AlertTriangle size={14} /> },
  Deleting: { color: "#9aa4b2", label: "Deleting", icon: <RefreshCw size={14} /> },
  Deleted: { color: "#9aa4b2", label: "Deleted", icon: <Trash2 size={14} /> },
};

const EMPTY_METRICS: ServiceMetrics = {
  service: "",
  cpu_cores: [],
  memory_megabytes: [],
  request_rate: [],
  error_rate: [],
  source: "empty",
};

const EMPTY_LOGS: LogsResponse = { service: "", lines: [], source: "empty" };
const REPOSITORY_URL = "https://github.com/ParthrChandurkar/InfraWatch";
const LOCAL_TOOLS = [
  { label: "Grafana", href: "http://localhost:3001", icon: <BarChart3 size={16} /> },
  { label: "Prometheus", href: "http://localhost:9090", icon: <Activity size={16} /> },
  { label: "Alloy", href: "http://localhost:12345", icon: <Gauge size={16} /> },
  { label: "API Docs", href: "http://localhost:8000/docs", icon: <Terminal size={16} /> },
];

function App() {
  const isLocalApi = apiMode === "local";
  const [deployments, setDeployments] = useState<DeploymentRecord[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [metrics, setMetrics] = useState<ServiceMetrics>(EMPTY_METRICS);
  const [workload, setWorkload] = useState<WorkloadHealth | null>(null);
  const [logs, setLogs] = useState<LogsResponse>(EMPTY_LOGS);
  const [auditLogs, setAuditLogs] = useState<AuditLogEntry[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isObservabilityLoading, setIsObservabilityLoading] = useState(false);
  const [isDeploying, setIsDeploying] = useState(false);
  const [usingApiFallback, setUsingApiFallback] = useState(isDemoMode);
  const [error, setError] = useState("");
  const [observabilityError, setObservabilityError] = useState("");
  const observabilityRequestActive = useRef(false);
  const [form, setForm] = useState({
    name: "catalog-api",
    image: "docker.io/example/catalog-api:latest",
    replicas: 2,
    port: 8080,
  });

  const selectedDeployment = deployments.find((item) => item.name === selected) ?? deployments[0] ?? null;
  const isBrowserFallback = isDemoMode || usingApiFallback;

  useEffect(() => subscribeToApiFallback(setUsingApiFallback), []);

  const summary = useMemo(() => {
    return deployments.reduce(
      (acc, item) => {
        acc[item.status] = (acc[item.status] ?? 0) + 1;
        acc.replicas += item.replicas;
        return acc;
      },
      {
        Running: 0,
        Failed: 0,
        Pending: 0,
        Deploying: 0,
        Deleting: 0,
        Deleted: 0,
        replicas: 0,
      } as Record<DeploymentStatus, number> & { replicas: number },
    );
  }, [deployments]);

  const refreshDeployments = useCallback(async () => {
    const items = await listDeployments();
    setDeployments(items);
    setSelected((current) => items.find((item) => item.name === current)?.name ?? items[0]?.name ?? "");
  }, []);

  const refreshAuditLogs = useCallback(async () => {
    const entries = await listAuditLogs(30);
    setAuditLogs(entries);
  }, []);

  const refreshObservability = useCallback(async (serviceName: string) => {
    const [metricResponse, logResponse, workloadResponse] = await Promise.all([
      getMetrics(serviceName),
      getLogs(serviceName),
      getWorkloadHealth(serviceName),
    ]);
    setMetrics(metricResponse);
    setLogs(logResponse);
    setWorkload(workloadResponse);
  }, []);

  useEffect(() => {
    let isMounted = true;
    Promise.all([refreshDeployments(), refreshAuditLogs()])
      .catch((reason: Error) => isMounted && setError(reason.message))
      .finally(() => isMounted && setIsLoading(false));
    return () => {
      isMounted = false;
    };
  }, [refreshAuditLogs, refreshDeployments]);

  useEffect(() => {
    const serviceName = selectedDeployment?.name;
    if (!serviceName) {
      setMetrics(EMPTY_METRICS);
      setLogs(EMPTY_LOGS);
      setWorkload(null);
      setObservabilityError("");
      return;
    }

    let isMounted = true;
    const load = async () => {
      if (observabilityRequestActive.current) {
        return;
      }
      observabilityRequestActive.current = true;
      setIsObservabilityLoading(true);
      try {
        await refreshObservability(serviceName);
        if (isMounted) {
          setObservabilityError("");
        }
      } catch (reason) {
        if (isMounted) {
          setObservabilityError(reason instanceof Error ? reason.message : "Observability refresh failed");
        }
      } finally {
        observabilityRequestActive.current = false;
        if (isMounted) {
          setIsObservabilityLoading(false);
        }
      }
    };

    load();
    const timer = window.setInterval(load, 7000);
    return () => {
      isMounted = false;
      window.clearInterval(timer);
    };
  }, [refreshObservability, selectedDeployment?.name]);

  async function handleRefresh() {
    setError("");
    try {
      await refreshDeployments();
      await refreshAuditLogs();
      if (selectedDeployment?.name) {
        await refreshObservability(selectedDeployment.name);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Refresh failed");
    }
  }

  async function handleDeploy(event: FormEvent) {
    event.preventDefault();
    setIsDeploying(true);
    setError("");
    try {
      const response = await deployService({
        name: form.name.trim(),
        image: form.image.trim(),
        replicas: form.replicas,
        port: form.port,
        environment: { ENVIRONMENT: "production" },
      });
      await refreshDeployments();
      await refreshAuditLogs();
      setSelected(response.deployment.name);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Deployment failed");
    } finally {
      setIsDeploying(false);
    }
  }

  async function handleDelete(name: string) {
    if (!window.confirm(`Remove ${name} from ${isBrowserFallback ? "your browser sandbox" : "InfraWatch"}?`)) {
      return;
    }
    setError("");
    try {
      await deleteDeployment(name);
      await refreshDeployments();
      await refreshAuditLogs();
      setSelected("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Delete failed");
    }
  }

  async function handleRollback(name: string) {
    if (!window.confirm(`Rollback ${name} to the previous Kubernetes revision?`)) {
      return;
    }
    setError("");
    try {
      await rollbackDeployment(name);
      await refreshDeployments();
      await refreshAuditLogs();
      setSelected(name);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Rollback failed");
    }
  }

  async function handleResetSandbox() {
    resetSandbox();
    setError("");
    setIsLoading(true);
    try {
      await Promise.all([refreshDeployments(), refreshAuditLogs()]);
    } finally {
      setIsLoading(false);
    }
  }

  const liveChartData = useMemo(
    () =>
      metrics.cpu_cores.map((point, index) => ({
        time: new Date(point.timestamp * 1000).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        }),
        cpu: point.value,
        memory: metrics.memory_megabytes[index]?.value ?? 0,
        requests: metrics.request_rate[index]?.value ?? 0,
        errors: metrics.error_rate[index]?.value ?? 0,
      })),
    [metrics],
  );
  const chartData = liveChartData;
  const hasMetricData = chartData.length > 0;
  const telemetryMode = liveChartData.length
    ? sourceLabel(metrics.source)
    : selectedDeployment
        ? "No live data"
        : "Waiting";

  const telemetry = useMemo(() => {
    const latest = chartData[chartData.length - 1];
    const latestErrorRate = Number(latest?.errors ?? 0);
    const healthScore = Math.max(
      0,
      Math.min(
        100,
        Math.round(100 - summary.Failed * 20 - (summary.Pending + summary.Deploying) * 6 - latestErrorRate * 12),
      ),
    );

    return {
      cpu: Number(latest?.cpu ?? 0),
      memory: Number(latest?.memory ?? 0),
      requests: Number(latest?.requests ?? 0),
      errors: latestErrorRate,
      healthScore,
      peakMemory: maxValue(chartData.map((item) => Number(item.memory))),
      avgCpu: averageValue(chartData.map((item) => Number(item.cpu))),
    };
  }, [chartData, summary.Deploying, summary.Failed, summary.Pending]);

  const workloadDesired = workload?.desired_replicas ?? selectedDeployment?.replicas ?? 0;
  const workloadReady = workload?.ready_replicas ?? selectedDeployment?.ready_replicas ?? 0;
  const workloadAvailable = workload?.available_replicas ?? selectedDeployment?.available_replicas ?? 0;
  const workloadUnavailable = workload?.unavailable_replicas ?? Math.max(workloadDesired - workloadAvailable, 0);

  const healthTone = telemetry.healthScore >= 90 ? "excellent" : telemetry.healthScore >= 70 ? "steady" : "attention";
  const activeRolloutCount = summary.Pending + summary.Deploying;
  const signalCount = [
    summary.Failed === 0,
    activeRolloutCount === 0,
    telemetry.errors < 0.2,
    Boolean(selectedDeployment),
  ].filter(Boolean).length;

  const pipelineSteps = [
    { icon: <GitBranch size={17} />, label: "GitHub", value: "main synced", state: "ready" },
    { icon: <Rocket size={17} />, label: "Delivery", value: "local validation", state: "ready" },
    { icon: <Layers size={17} />, label: "Deployments", value: isBrowserFallback ? "Manifest simulation" : "Docker images", state: "ready" },
    { icon: <Server size={17} />, label: "Runtime", value: isBrowserFallback ? "Browser sandbox" : isLocalApi ? "Local full stack" : "Configured API", state: "live" },
    { icon: <Gauge size={17} />, label: "Telemetry", value: telemetryMode, state: "live" },
  ];

  const platformSignals = [
    {
      icon: <Server size={18} />,
      label: "Local Kubernetes",
      value: isBrowserFallback ? "Simulated" : "Connected",
      detail: isBrowserFallback ? "Safe browser sandbox" : "FastAPI can talk to the local control plane",
      tone: isBrowserFallback ? "warn" : "ok",
    },
    {
      icon: <Activity size={18} />,
      label: "Prometheus",
      value: isBrowserFallback ? "Mock metrics" : telemetryMode,
      detail: "CPU, memory, request rate, and error-rate charts",
      tone: "ok",
    },
    {
      icon: <Terminal size={18} />,
      label: "Alloy → Loki",
      value: isBrowserFallback ? "Mock logs" : sourceLabel(logs.source),
      detail: "Grafana Alloy forwards app logs into Loki",
      tone: logs.source === "empty" && !isBrowserFallback ? "warn" : "ok",
    },
    {
      icon: <BarChart3 size={18} />,
      label: "Grafana",
      value: "Deep dive",
      detail: "Use Grafana for detailed observability views",
      tone: "blue",
    },
    {
      icon: <ShieldCheck size={18} />,
      label: "Audit trail",
      value: `${auditLogs.length} events`,
      detail: "Deployment, rollback, and delete actions are tracked",
      tone: auditLogs.length ? "ok" : "blue",
    },
  ];

  const flowSteps = [
    {
      icon: <Rocket size={18} />,
      title: "Add app",
      body: "Enter image, port, replicas, and a service name.",
    },
    {
      icon: <Layers size={18} />,
      title: "Deploy locally",
      body: "InfraWatch creates Kubernetes Deployment and Service manifests.",
    },
    {
      icon: <Activity size={18} />,
      title: "Observe",
      body: "Prometheus handles metrics; Alloy forwards logs into Loki.",
    },
    {
      icon: <RotateCcw size={18} />,
      title: "Recover",
      body: "Use audit history, rollback, and delete actions during testing.",
    },
  ];

  const selectedStatus = selectedDeployment?.status ?? "Pending";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">IW</span>
          <div>
            <strong>InfraWatch</strong>
            <small>Local K8s observability</small>
          </div>
        </div>

        <div className="environment-card">
          <div>
            <span>Environment</span>
            <strong>{isBrowserFallback ? "Browser Demo Mode" : isLocalApi ? "Local full stack" : "Configured API"}</strong>
          </div>
          <span className="pulse-dot" />
        </div>

        <nav className="sidebar-nav" aria-label="Dashboard sections">
          <a href="#overview">Overview</a>
          <a href="#deploy">Deploy</a>
          <a href="#workloads">Workloads</a>
          <a href="#observability">Observability</a>
          <a href="#logs">Logs</a>
        </nav>

        <button className="refresh-button" type="button" onClick={handleRefresh} title="Refresh dashboard">
          <RefreshCw size={16} />
          Refresh
        </button>

        {isBrowserFallback && (
          <button className="reset-button" type="button" onClick={handleResetSandbox} title="Restore sample services">
            <RotateCcw size={16} />
            Reset sandbox
          </button>
        )}

        <nav className="service-list" aria-label="Deployed services">
          <div className="sidebar-label">Service Fleet</div>
          {deployments.map((deployment) => (
            <button
              className={`service-item ${deployment.name === selectedDeployment?.name ? "active" : ""}`}
              key={deployment.name}
              onClick={() => setSelected(deployment.name)}
              type="button"
            >
              <span className="status-dot" style={{ background: STATUS_THEME[deployment.status].color }} />
              <span>
                <strong>{deployment.name}</strong>
                <small>{deployment.namespace} / {deployment.replicas} replicas</small>
              </span>
              <StatusPill status={deployment.status} />
            </button>
          ))}
          {!deployments.length && !isLoading && (
            <div className="empty-service">
              <Server size={18} />
              <span>No active services</span>
            </div>
          )}
        </nav>
      </aside>

      <main className="main-panel">
        <header className="command-header">
          <div>
            <span className="eyebrow">Local Kubernetes Console</span>
            <h1>CloudWatch-style visibility for local apps.</h1>
            <p>Deploy lightweight services to local Kubernetes, watch rollout health, inspect metrics, stream logs, and keep audit history in one polished control plane.</p>
          </div>
          <div className="header-actions" aria-label="External operations tools">
            {!isLocalApi ? (
              <>
                <a href={REPOSITORY_URL} target="_blank" rel="noreferrer">
                  <GitBranch size={16} />
                  GitHub
                  <ExternalLink size={14} />
                </a>
                <a href={`${REPOSITORY_URL}/blob/main/flow.md`} target="_blank" rel="noreferrer">
                  <GitBranch size={16} />
                  Architecture
                  <ExternalLink size={14} />
                </a>
                <a href={`${REPOSITORY_URL}#main-api-endpoints`} target="_blank" rel="noreferrer">
                  <Terminal size={16} />
                  API Contract
                  <ExternalLink size={14} />
                </a>
              </>
            ) : (
              <>
                {LOCAL_TOOLS.map((tool) => (
                  <a href={tool.href} target="_blank" rel="noreferrer" key={tool.label}>
                    {tool.icon}
                    {tool.label}
                    <ExternalLink size={14} />
                  </a>
                ))}
              </>
            )}
          </div>
        </header>

        {isDemoMode && <DemoModeBanner />}

        {error && <div className="error-banner">{error}</div>}
        {observabilityError && <div className="error-banner">Observability unavailable: {observabilityError}</div>}

        <section className={`hero-console ${healthTone}`} id="overview" aria-label="InfraWatch overview">
          <div className="hero-copy">
            <span className="section-kicker">Mission Control</span>
            <h2>{selectedDeployment ? `${selectedDeployment.name} is under watch` : "Start with a lightweight container image"}</h2>
            <p>
              InfraWatch turns your local Kubernetes lab into a clean deployment and observability workspace for
              testing apps before they move anywhere near cloud infrastructure.
            </p>
            <div className="hero-badges" aria-label="Platform capabilities">
              <span>☸ Local Kubernetes</span>
              <span>📈 Prometheus metrics</span>
              <span>📜 Alloy + Loki logs</span>
              <span>🧾 Audit trail</span>
            </div>
          </div>
          <div className="hero-score-card">
            <div
              className="score-ring"
              style={{ background: `conic-gradient(#3ddc97 ${telemetry.healthScore * 3.6}deg, #26313d 0deg)` }}
              aria-label={`Health score ${telemetry.healthScore}%`}
            >
              <span>{telemetry.healthScore}%</span>
            </div>
            <strong>Local health score</strong>
            <small>{signalCount}/4 platform checks healthy</small>
          </div>
          <div className="hero-mini-grid">
            <MiniStat label="Services" value={deployments.length} />
            <MiniStat label="Replicas" value={summary.replicas} />
            <MiniStat label="Rollouts" value={activeRolloutCount} />
            <MiniStat label="Alerts" value={summary.Failed} />
          </div>
        </section>

        <section className="pipeline-strip" aria-label="Delivery pipeline">
          {pipelineSteps.map((step) => (
            <article className={`pipeline-step ${step.state}`} key={step.label}>
              <div className="pipeline-icon">{step.icon}</div>
              <div>
                <span>{step.label}</span>
                <strong>{step.value}</strong>
              </div>
            </article>
          ))}
        </section>

        <section className="status-grid" aria-label="Deployment status summary">
          <SummaryCard icon={<ShieldCheck size={20} />} label="Health score" value={`${telemetry.healthScore}%`} tone="green" />
          <SummaryCard icon={<Server size={20} />} label="Running services" value={summary.Running} tone="blue" />
          <SummaryCard icon={<Layers size={20} />} label="Active replicas" value={summary.replicas} tone="violet" />
          <SummaryCard icon={<AlertTriangle size={20} />} label="Open failures" value={summary.Failed} tone="red" />
        </section>

        <section className="stack-panel" aria-label="InfraWatch platform stack">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">Stack Health</span>
              <h2>What the user sees in one dashboard</h2>
              <p>React stays as the main product UI. Grafana, Prometheus, Loki, and Alloy remain supporting tools for deeper inspection.</p>
            </div>
          </div>
          <div className="stack-grid">
            {platformSignals.map((signal) => (
              <article className={`stack-card ${signal.tone}`} key={signal.label}>
                <div className="stack-icon">{signal.icon}</div>
                <div>
                  <span>{signal.label}</span>
                  <strong>{signal.value}</strong>
                  <p>{signal.detail}</p>
                </div>
              </article>
            ))}
          </div>
        </section>

        <div className="dashboard-grid">
          <section className="panel service-overview">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Selected Service</span>
                <h2>{selectedDeployment?.name ?? "No service selected"}</h2>
                <p>{selectedDeployment?.message ?? "Deploy a service to populate the command center."}</p>
              </div>
              {selectedDeployment && <StatusPill status={selectedStatus} size="large" />}
            </div>

            <div className="service-facts">
              <Fact icon={<Layers size={17} />} label="Image" value={selectedDeployment?.image ?? "Waiting for deployment"} />
              <Fact icon={<Server size={17} />} label="Namespace" value={selectedDeployment?.namespace ?? "infrawatch"} />
              <Fact icon={<Zap size={17} />} label="Desired replicas" value={String(workloadDesired)} />
              <Fact
                icon={<CheckCircle2 size={17} />}
                label="Ready pods"
                value={`${workloadReady}/${workloadDesired}`}
              />
              <Fact icon={<Activity size={17} />} label="Available replicas" value={String(workloadAvailable)} />
              <Fact icon={<AlertTriangle size={17} />} label="Unavailable replicas" value={String(workloadUnavailable)} />
              <Fact icon={<Clock size={17} />} label="Updated" value={selectedDeployment ? relativeTime(selectedDeployment.updated_at) : "Not available"} />
            </div>
            <div className="workload-health-strip" aria-label="Kubernetes workload health">
              <span>Workload source: {workload ? sourceLabel(workload.source) : "Waiting"}</span>
              <span>Updated: {workload?.updated_replicas ?? 0}</span>
              <span>Observed generation: {workload?.observed_generation ?? "n/a"}</span>
            </div>
            {workload?.pods.length ? (
              <div className="pod-health-list" aria-label="Pod health">
                {workload.pods.map((pod) => (
                  <div className={`pod-health ${pod.ready ? "ready" : "problem"}`} key={pod.name}>
                    <strong>{pod.name}</strong>
                    <span>{pod.phase} / {pod.ready ? "Ready" : pod.reason ?? "Not ready"}</span>
                    <small>{pod.restart_count} restart{pod.restart_count === 1 ? "" : "s"}</small>
                  </div>
                ))}
              </div>
            ) : selectedDeployment ? (
              <div className="telemetry-notice">Pod-level details are not available yet for this workload.</div>
            ) : null}
            {selectedDeployment?.last_failure && (
              <div className="rollout-failure">
                <AlertTriangle size={16} />
                <span>{selectedDeployment.last_failure}</span>
              </div>
            )}
          </section>

          <section className="panel deploy-panel" id="deploy">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Release Control</span>
                <h2>Deploy Service</h2>
              </div>
            </div>

            <form className="deploy-form" onSubmit={handleDeploy}>
              {isDemoMode && (
                <p className="sandbox-note">
                  Try any valid service name and container image. Changes are private to this device in browser Demo Mode.
                </p>
              )}
              <label>
                Service name
                <input
                  aria-label="Service name"
                  required
                  minLength={2}
                  maxLength={63}
                  pattern="[a-z0-9]([-a-z0-9]*[a-z0-9])?"
                  value={form.name}
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                  placeholder="service-name"
                />
              </label>
              <label>
                Container image
                <input
                  aria-label="Container image"
                  required
                  minLength={3}
                  maxLength={255}
                  value={form.image}
                  onChange={(event) => setForm({ ...form, image: event.target.value })}
                  placeholder="registry/image:tag"
                />
              </label>
              <div className="deploy-number-row">
                <label>
                  Replicas
                  <input
                    aria-label="Replica count"
                    type="number"
                    min={1}
                    max={10}
                    value={form.replicas}
                    onChange={(event) => setForm({ ...form, replicas: Number(event.target.value) })}
                  />
                </label>
                <label>
                  Port
                  <input
                    aria-label="Service port"
                    type="number"
                    min={1}
                    max={65535}
                    value={form.port}
                    onChange={(event) => setForm({ ...form, port: Number(event.target.value) })}
                  />
                </label>
              </div>
              <button type="submit" disabled={isDeploying}>
                <Rocket size={17} />
                {isDeploying ? "Deploying" : "Deploy"}
              </button>
            </form>
          </section>

          <section className="panel flow-panel">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Project Flow</span>
                <h2>How InfraWatch works</h2>
              </div>
            </div>
            <div className="flow-list">
              {flowSteps.map((step, index) => (
                <article className="flow-step" key={step.title}>
                  <div className="flow-index">
                    <span>{index + 1}</span>
                    {step.icon}
                  </div>
                  <div>
                    <strong>{step.title}</strong>
                    <p>{step.body}</p>
                  </div>
                </article>
              ))}
            </div>
          </section>

          <section className="panel telemetry-status-panel">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Real Metrics</span>
                <h2>Prometheus-backed service telemetry</h2>
                <p>
                  {metrics.source === "prometheus"
                    ? "Showing real Prometheus data through the FastAPI metrics endpoint and Redis cache."
                    : metrics.source === "mock" || metrics.source === "browser sandbox"
                      ? "Showing fallback telemetry. This is not real Prometheus data."
                      : isObservabilityLoading
                        ? "Loading service telemetry from the backend."
                        : "No metric series is currently available for the selected service."}
                </p>
              </div>
              <span className="data-source">{isObservabilityLoading ? "Refreshing" : telemetryMode}</span>
            </div>
          </section>

          <MetricChart area="cpu-card" title="CPU Usage" value={hasMetricData ? `${telemetry.cpu.toFixed(2)} cores` : "No data"} data={chartData} dataKey="cpu" color="#4da3ff" icon={<Cpu size={18} />} />
          <MetricChart area="memory-card" title="Memory Usage" value={hasMetricData ? `${Math.round(telemetry.memory)} MB` : "No data"} data={chartData} dataKey="memory" color="#3ddc97" icon={<HardDrive size={18} />} />
          <MetricChart area="requests-card" title="Request Rate" value={hasMetricData ? `${telemetry.requests.toFixed(1)} rps` : "No data"} data={chartData} dataKey="requests" color="#b38cff" icon={<Activity size={18} />} />
          <MetricChart area="errors-card" title="Error Rate" value={hasMetricData ? `${telemetry.errors.toFixed(2)} rps` : "No data"} data={chartData} dataKey="errors" color="#ff6b7a" icon={<AlertTriangle size={18} />} />

          <section className="panel fleet-panel" id="workloads">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Deployment Inventory</span>
                <h2>Service Fleet</h2>
              </div>
              <span className="data-source">{deployments.length} records</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Service</th>
                    <th>Status</th>
                    <th>Ready / desired</th>
                    <th>Image</th>
                    <th>Updated</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {deployments.map((deployment) => (
                    <tr key={deployment.name}>
                      <td>
                        <button className="table-service" type="button" onClick={() => setSelected(deployment.name)}>
                          {deployment.name}
                        </button>
                      </td>
                      <td><StatusPill status={deployment.status} /></td>
                      <td>{deployment.ready_replicas ?? 0}/{deployment.replicas}</td>
                      <td className="image-cell">{deployment.image}</td>
                      <td>{relativeTime(deployment.updated_at)}</td>
                      <td>
                        <div className="table-actions">
                          <button
                            className="table-action rollback"
                            type="button"
                            onClick={() => handleRollback(deployment.name)}
                            title="Rollback deployment"
                          >
                            <RefreshCw size={15} />
                          </button>
                          <button className="table-action" type="button" onClick={() => handleDelete(deployment.name)} title="Delete deployment">
                            <Trash2 size={15} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {!deployments.length && (
                    <tr>
                      <td colSpan={6} className="empty-row">No services have been deployed yet.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="panel logs-panel" id="logs">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Runtime Logs</span>
                <h2><Terminal size={18} /> Live Stream</h2>
              </div>
              <span className="data-source">{sourceLabel(logs.source)}</span>
            </div>
            <div className="log-viewer">
              {logs.lines.map((entry) => (
                <div className="log-line" key={`${entry.timestamp}-${entry.line}`}>
                  <time>{new Date(entry.timestamp).toLocaleTimeString()}</time>
                  <code>{entry.line}</code>
                </div>
              ))}
              {!logs.lines.length && (
                <div className="empty-log">
                  <Terminal size={18} />
                  <span>Waiting for log events</span>
                </div>
              )}
            </div>
          </section>

          <section className="panel audit-panel" id="observability">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Audit Trail</span>
                <h2><ShieldCheck size={18} /> Recent Actions</h2>
              </div>
              <span className="data-source">{auditLogs.length} events</span>
            </div>
            <div className="audit-list">
              {auditLogs.map((entry) => (
                <div className={`audit-entry ${auditTone(entry.status)}`} key={entry.id}>
                  <span className="audit-marker" />
                  <div>
                    <div className="audit-topline">
                      <strong>{formatAuditAction(entry.action)}</strong>
                      <time>{relativeTime(entry.created_at)}</time>
                    </div>
                    <p>{entry.message}</p>
                    <span>{entry.service ?? "platform"} / {entry.actor}</span>
                  </div>
                </div>
              ))}
              {!auditLogs.length && (
                <div className="empty-log">
                  <ShieldCheck size={18} />
                  <span>No audit events yet</span>
                </div>
              )}
            </div>
          </section>

          <section className="panel readiness-panel">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Platform Signals</span>
                <h2>Readiness</h2>
              </div>
            </div>
            <Signal label="Control plane" value={isDemoMode || usingApiFallback ? "Browser sandbox ready" : "FastAPI online"} state="ok" />
            <Signal label="Metrics path" value={telemetryMode} state="ok" />
            <Signal label="Log pipeline" value={isBrowserFallback ? "Mock logs" : "Alloy → Loki"} state="ok" />
            <Signal label="Average CPU" value={`${telemetry.avgCpu.toFixed(2)} cores`} state="ok" />
            <Signal label="Peak memory" value={`${Math.round(telemetry.peakMemory)} MB`} state="ok" />
            <Signal
              label="Pending work"
              value={`${summary.Pending + summary.Deploying} rollout events`}
              state={summary.Pending + summary.Deploying ? "warn" : "ok"}
            />
          </section>
        </div>
      </main>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="mini-stat">
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function SummaryCard({ icon, label, value, tone }: { icon: ReactNode; label: string; value: string | number; tone: string }) {
  return (
    <article className={`summary-card ${tone}`}>
      <div className="summary-icon">{icon}</div>
      <div>
        <strong>{value}</strong>
        <span>{label}</span>
      </div>
    </article>
  );
}

function DemoModeBanner() {
  return (
    <section className="demo-mode-banner" role="note" aria-label="Demo Mode">
      <div className="demo-mode-title">
        <AlertTriangle size={22} aria-hidden="true" />
        <div>
          <strong>DEMO MODE</strong>
          <span>Browser sandbox</span>
        </div>
      </div>
      <p>
        This frontend-only sandbox keeps all changes in your browser and uses realistic simulated infrastructure data.
      </p>
      <div className="demo-capabilities" aria-label="Demo capabilities">
        <span className="real">Interactive UI</span>
        <span>Simulated Kubernetes</span>
        <span>Mock metrics & logs</span>
      </div>
    </section>
  );
}

function Fact({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="fact">
      <div className="fact-icon">{icon}</div>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
    </div>
  );
}

function Signal({ label, value, state }: { label: string; value: string; state: "ok" | "warn" }) {
  return (
    <div className={`signal ${state}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function StatusPill({ status, size = "default" }: { status: DeploymentStatus; size?: "default" | "large" }) {
  const theme = STATUS_THEME[status];
  return (
    <span className={`status-pill ${size}`} style={{ color: theme.color }}>
      {theme.icon}
      {theme.label}
    </span>
  );
}

function MetricChart({
  area,
  title,
  value,
  data,
  dataKey,
  color,
  icon,
}: {
  area: string;
  title: string;
  value: string;
  data: Record<string, string | number>[];
  dataKey: string;
  color: string;
  icon: ReactNode;
}) {
  return (
    <section className={`metric-card ${area}`}>
      <div className="metric-heading">
        <div>
          <span>{title}</span>
          <strong>{value}</strong>
        </div>
        <div className="metric-icon" style={{ color }}>{icon}</div>
      </div>
      {data.length ? (
        <ResponsiveContainer width="100%" height={170}>
          <AreaChart data={data}>
            <defs>
              <linearGradient id={`gradient-${dataKey}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={color} stopOpacity={0.35} />
                <stop offset="95%" stopColor={color} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#27313c" strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="time" tick={{ fill: "#9aa4b2", fontSize: 11 }} stroke="#354252" />
            <YAxis tick={{ fill: "#9aa4b2", fontSize: 11 }} stroke="#354252" width={42} />
            <Tooltip
              contentStyle={{ background: "#121821", border: "1px solid #303b48", borderRadius: 8, color: "#f4f7fb" }}
              labelStyle={{ color: "#f4f7fb" }}
            />
            <Area type="monotone" dataKey={dataKey} stroke={color} strokeWidth={2.4} fill={`url(#gradient-${dataKey})`} dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      ) : (
        <div className="metric-empty">No Prometheus series is available for this metric yet.</div>
      )}
    </section>
  );
}

function sourceLabel(source: string) {
  if (source === "mock") {
    return "Simulated telemetry";
  }
  if (source === "empty") {
    return "Waiting";
  }
  return source.charAt(0).toUpperCase() + source.slice(1);
}

function auditTone(status: string) {
  if (status === "failure") {
    return "failure";
  }
  if (status === "not_found") {
    return "warning";
  }
  return "success";
}

function formatAuditAction(action: string) {
  return action
    .split(".")
    .map((part) => part.replace(/_/g, " "))
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function relativeTime(value: string) {
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) {
    return "Unknown";
  }
  const diffSeconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (diffSeconds < 60) {
    return `${diffSeconds}s ago`;
  }
  const diffMinutes = Math.round(diffSeconds / 60);
  if (diffMinutes < 60) {
    return `${diffMinutes}m ago`;
  }
  const diffHours = Math.round(diffMinutes / 60);
  if (diffHours < 24) {
    return `${diffHours}h ago`;
  }
  return new Date(value).toLocaleDateString();
}

function averageValue(values: number[]) {
  if (!values.length) {
    return 0;
  }
  return values.reduce((sum, item) => sum + item, 0) / values.length;
}

function maxValue(values: number[]) {
  if (!values.length) {
    return 0;
  }
  return Math.max(...values);
}

export default App;
