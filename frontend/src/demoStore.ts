import type {
  AuditLogEntry,
  DeployPayload,
  DeploymentRecord,
  LogsResponse,
  ServiceMetrics,
  WorkloadHealth,
} from "./types";

interface DemoState {
  deployments: DeploymentRecord[];
  auditLogs: AuditLogEntry[];
}

const STORAGE_KEY = "infrawatch-public-sandbox-v1";

function minutesAgo(minutes: number) {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

function createInitialState(): DemoState {
  const deployments: DeploymentRecord[] = [
    {
      name: "checkout-api",
      image: "ghcr.io/infrawatch/checkout-api:2.4.1",
      namespace: "infrawatch",
      replicas: 3,
      port: 8080,
      status: "Running",
      url: "https://checkout.demo.internal",
      commit_sha: "7b2f9ad",
      message: "Rollout completed successfully in the public sandbox.",
      ready_replicas: 3,
      available_replicas: 3,
      created_at: minutesAgo(180),
      updated_at: minutesAgo(8),
    },
    {
      name: "catalog-api",
      image: "ghcr.io/infrawatch/catalog-api:1.9.0",
      namespace: "infrawatch",
      replicas: 2,
      port: 8080,
      status: "Running",
      url: "https://catalog.demo.internal",
      commit_sha: "95ce112",
      message: "Healthy across all replicas.",
      ready_replicas: 2,
      available_replicas: 2,
      created_at: minutesAgo(720),
      updated_at: minutesAgo(34),
    },
    {
      name: "worker-queue",
      image: "ghcr.io/infrawatch/worker-queue:0.8.3",
      namespace: "infrawatch",
      replicas: 1,
      port: 9091,
      status: "Pending",
      commit_sha: "c41a903",
      message: "Waiting for the rollout health gate.",
      ready_replicas: 0,
      available_replicas: 0,
      created_at: minutesAgo(24),
      updated_at: minutesAgo(3),
    },
  ];

  return {
    deployments,
    auditLogs: [
      demoAudit("deployment.completed", "checkout-api", "success", "Three replicas passed the rollout health gate.", 8),
      demoAudit("deployment.completed", "catalog-api", "success", "Catalog API release promoted to the sandbox.", 34),
      demoAudit("deployment.started", "worker-queue", "pending", "Release accepted and validation is in progress.", 3),
    ],
  };
}

function demoAudit(action: string, service: string, status: string, message: string, minutes: number): AuditLogEntry {
  return {
    id: `${service}-${action}-${minutes}`,
    action,
    service,
    actor: "public-demo",
    status,
    message,
    metadata: { mode: "browser-sandbox" },
    created_at: minutesAgo(minutes),
  };
}

function readState(): DemoState {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    return saved ? (JSON.parse(saved) as DemoState) : createInitialState();
  } catch {
    return createInitialState();
  }
}

function writeState(state: DemoState) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function randomId() {
  return typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

export function listDemoDeployments(): DeploymentRecord[] {
  return readState().deployments.sort(
    (left, right) => new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime(),
  );
}

export function deployDemoService(payload: DeployPayload): { deployment: DeploymentRecord } {
  const state = readState();
  const now = new Date().toISOString();
  const existing = state.deployments.find((deployment) => deployment.name === payload.name);
  const deployment: DeploymentRecord = {
    name: payload.name,
    image: payload.image,
    namespace: "infrawatch",
    replicas: payload.replicas,
    port: payload.port,
    status: "Running",
    url: `https://${payload.name}.demo.internal`,
    message: "Deployment completed in your private browser sandbox.",
    ready_replicas: payload.replicas,
    available_replicas: payload.replicas,
    created_at: existing?.created_at ?? now,
    updated_at: now,
  };

  state.deployments = [deployment, ...state.deployments.filter((item) => item.name !== payload.name)];
  state.auditLogs.unshift({
    id: randomId(),
    action: existing ? "deployment.updated" : "deployment.created",
    service: payload.name,
    actor: "visitor",
    status: "success",
    message: `${payload.image} is running with ${payload.replicas} replica${payload.replicas === 1 ? "" : "s"}.`,
    metadata: { port: payload.port, mode: "browser-sandbox" },
    created_at: now,
  });
  writeState(state);
  return { deployment };
}

export function deleteDemoDeployment(name: string): { status: string; name: string } {
  const state = readState();
  state.deployments = state.deployments.filter((deployment) => deployment.name !== name);
  state.auditLogs.unshift({
    id: randomId(),
    action: "deployment.deleted",
    service: name,
    actor: "visitor",
    status: "success",
    message: "Deployment removed from your private browser sandbox.",
    metadata: { mode: "browser-sandbox" },
    created_at: new Date().toISOString(),
  });
  writeState(state);
  return { status: "deleted", name };
}

export function getDemoMetrics(service: string): ServiceMetrics {
  const seed = service.split("").reduce((total, char) => total + char.charCodeAt(0), 0);
  const now = Math.floor(Date.now() / 1000);
  const points = Array.from({ length: 12 }, (_, index) => ({
    timestamp: now - (11 - index) * 60,
    wave: Math.sin((index + seed) / 2.2),
    drift: Math.cos((index + seed) / 3.5),
  }));
  return {
    service,
    cpu_cores: points.map((point) => ({ timestamp: point.timestamp, value: 0.24 + Math.abs(point.wave) * 0.19 })),
    memory_megabytes: points.map((point) => ({ timestamp: point.timestamp, value: 220 + Math.abs(point.drift) * 96 + (seed % 37) })),
    request_rate: points.map((point) => ({ timestamp: point.timestamp, value: 18 + Math.abs(point.wave) * 28 + (seed % 9) })),
    error_rate: points.map((point) => ({ timestamp: point.timestamp, value: Math.abs(point.drift) * 0.08 })),
    source: "browser sandbox",
  };
}

export function getDemoLogs(service: string, search = ""): LogsResponse {
  const lines = [
    { timestamp: minutesAgo(2), line: `[info] ${service} health check passed`, pod: `${service}-demo-1`, container: "app" },
    {
      timestamp: minutesAgo(5),
      line: "[info] rollout is serving traffic on the configured port",
      pod: `${service}-demo-1`,
      container: "app",
    },
    {
      timestamp: minutesAgo(9),
      line: "[info] deployment reconciled by InfraWatch",
      pod: `${service}-demo-2`,
      container: "app",
    },
  ];
  const normalizedSearch = search.trim().toLowerCase();
  return {
    service,
    source: "browser sandbox",
    lines: normalizedSearch
      ? lines.filter((entry) => entry.line.toLowerCase().includes(normalizedSearch))
      : lines,
  };
}

export function getDemoWorkloadHealth(service: string): WorkloadHealth {
  const deployment = listDemoDeployments().find((item) => item.name === service);
  const desired = deployment?.replicas ?? 0;
  const ready = deployment?.ready_replicas ?? desired;
  const available = deployment?.available_replicas ?? ready;
  return {
    service,
    namespace: deployment?.namespace ?? "infrawatch",
    desired_replicas: desired,
    updated_replicas: ready,
    ready_replicas: ready,
    available_replicas: available,
    unavailable_replicas: Math.max(desired - available, 0),
    observed_generation: deployment?.observed_generation,
    pods: Array.from({ length: desired }, (_, index) => ({
      name: `${service}-demo-${index + 1}`,
      phase: ready > index ? "Running" : "Pending",
      ready: ready > index,
      restart_count: 0,
      reason: ready > index ? undefined : "Pending",
      message: ready > index ? undefined : "Demo pod is waiting for simulated readiness.",
    })),
    source: "browser sandbox",
  };
}

export function listDemoAuditLogs(limit: number): AuditLogEntry[] {
  return readState().auditLogs.slice(0, limit);
}

export function resetDemoState() {
  window.localStorage.removeItem(STORAGE_KEY);
}
