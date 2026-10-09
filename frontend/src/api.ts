// Thin API wrapper around the InfraWatch backend REST endpoints.
import type {
  AuditLogEntry,
  AlertsResponse,
  DeployPayload,
  DeploymentRecord,
  LogsResponse,
  ServiceMetrics,
  WorkloadHealth,
} from "./types";
import {
  deleteDemoDeployment,
  deployDemoService,
  getDemoAlerts,
  getDemoLogs,
  getDemoMetrics,
  getDemoWorkloadHealth,
  listDemoAuditLogs,
  listDemoDeployments,
  resetDemoState,
} from "./demoStore";

const configuredApiUrl = import.meta.env.VITE_API_BASE_URL?.trim();
const isLocalBrowser = ["localhost", "127.0.0.1"].includes(window.location.hostname);
const demoModeRequested = import.meta.env.VITE_DEMO_MODE === "true";
const API_BASE_URL = demoModeRequested ? "" : configuredApiUrl || (isLocalBrowser ? "http://localhost:8000" : "");

export const isDemoMode = API_BASE_URL === "";
export const apiMode = isDemoMode ? "browser" : isLocalBrowser ? "local" : "configured";

type FallbackListener = (active: boolean) => void;

let fallbackActive = isDemoMode;
const fallbackListeners = new Set<FallbackListener>();

class ApiResponseError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

function setFallbackActive(active: boolean) {
  if (fallbackActive === active) {
    return;
  }
  fallbackActive = active;
  fallbackListeners.forEach((listener) => listener(active));
}

export function subscribeToApiFallback(listener: FallbackListener) {
  fallbackListeners.add(listener);
  listener(fallbackActive);
  return () => {
    fallbackListeners.delete(listener);
  };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiResponseError(body.detail ?? response.statusText, response.status);
  }

  return response.json() as Promise<T>;
}

async function withDemoFallback<T>(apiCall: () => Promise<T>, demoCall: () => T): Promise<T> {
  if (isDemoMode) {
    return demoCall();
  }

  try {
    const result = await apiCall();
    setFallbackActive(false);
    return result;
  } catch (error) {
    throw error;
  }
}

export function listDeployments(): Promise<DeploymentRecord[]> {
  return withDemoFallback(
    () => request<DeploymentRecord[]>("/deployments"),
    () => listDemoDeployments(),
  );
}

export function deployService(payload: DeployPayload): Promise<{ deployment: DeploymentRecord }> {
  return withDemoFallback(
    () => request<{ deployment: DeploymentRecord }>("/deploy", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
    () => deployDemoService(payload),
  );
}

export function deleteDeployment(name: string): Promise<{ status: string; name: string }> {
  return withDemoFallback(
    () => request<{ status: string; name: string }>(`/deployment/${name}`, { method: "DELETE" }),
    () => deleteDemoDeployment(name),
  );
}

export function rollbackDeployment(name: string): Promise<DeploymentRecord> {
  return withDemoFallback(
    () => request<DeploymentRecord>(`/deployment/${name}/rollback`, { method: "POST" }),
    () => {
      const deployment = listDemoDeployments().find((item) => item.name === name);
      if (!deployment) {
        throw new Error("Deployment not found");
      }
      return {
        ...deployment,
        message: "Rollback is simulated in the browser sandbox. Real rollback uses kubectl rollout undo.",
        updated_at: new Date().toISOString(),
      };
    },
  );
}

export function getMetrics(service: string): Promise<ServiceMetrics> {
  return withDemoFallback(
    () => request<ServiceMetrics>(`/metrics/${service}`),
    () => getDemoMetrics(service),
  );
}

export function getWorkloadHealth(service: string): Promise<WorkloadHealth> {
  return withDemoFallback(
    () => request<WorkloadHealth>(`/workloads/${service}`),
    () => getDemoWorkloadHealth(service),
  );
}

export function getLogs(
  service: string,
  options: { search?: string; pod?: string; container?: string; minutes?: number; limit?: number } = {},
): Promise<LogsResponse> {
  const params = new URLSearchParams();
  if (options.search?.trim()) {
    params.set("search", options.search.trim());
  }
  if (options.pod) {
    params.set("pod", options.pod);
  }
  if (options.container) {
    params.set("container", options.container);
  }
  if (options.minutes) {
    params.set("minutes", String(options.minutes));
  }
  if (options.limit) {
    params.set("limit", String(options.limit));
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return withDemoFallback(
    () => request<LogsResponse>(`/logs/${service}${suffix}`),
    () => getDemoLogs(service, options.search),
  );
}

export function getAlerts(): Promise<AlertsResponse> {
  return withDemoFallback(
    () => request<AlertsResponse>("/alerts"),
    () => getDemoAlerts(),
  );
}

export function listAuditLogs(limit = 50): Promise<AuditLogEntry[]> {
  return withDemoFallback(
    () => request<AuditLogEntry[]>(`/audit-logs?limit=${limit}`),
    () => listDemoAuditLogs(limit),
  );
}

export function resetSandbox() {
  resetDemoState();
}
