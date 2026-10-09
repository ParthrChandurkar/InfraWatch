// Shared TypeScript types for InfraWatch API responses.
export type DeploymentStatus = "Running" | "Failed" | "Pending" | "Deploying" | "Deleting" | "Deleted";

export interface DeploymentRecord {
  name: string;
  image: string;
  namespace: string;
  replicas: number;
  port: number;
  status: DeploymentStatus;
  url?: string;
  commit_sha?: string;
  ready_replicas?: number;
  available_replicas?: number;
  observed_generation?: number;
  last_failure?: string;
  message: string;
  created_at: string;
  updated_at: string;
}

export interface DeployPayload {
  name: string;
  image: string;
  replicas: number;
  port: number;
  environment: Record<string, string>;
}

export interface MetricPoint {
  timestamp: number;
  value: number;
}

export interface ServiceMetrics {
  service: string;
  cpu_cores: MetricPoint[];
  memory_megabytes: MetricPoint[];
  request_rate: MetricPoint[];
  error_rate: MetricPoint[];
  source: string;
}

export interface PodHealth {
  name: string;
  phase: string;
  ready: boolean;
  restart_count: number;
  reason?: string;
  message?: string;
}

export interface WorkloadHealth {
  service: string;
  namespace: string;
  desired_replicas: number;
  updated_replicas: number;
  ready_replicas: number;
  available_replicas: number;
  unavailable_replicas: number;
  observed_generation?: number;
  pods: PodHealth[];
  source: string;
}

export interface LogLine {
  timestamp: string;
  line: string;
  namespace?: string;
  pod?: string;
  container?: string;
  labels?: Record<string, string>;
}

export interface LogsResponse {
  service: string;
  lines: LogLine[];
  source: string;
}

export interface AlertSummary {
  fingerprint: string;
  status: string;
  alertname: string;
  severity?: string;
  instance?: string;
  service?: string;
  namespace?: string;
  pod?: string;
  starts_at?: string;
  summary?: string;
  description?: string;
}

export interface AlertsResponse {
  source: string;
  alerts: AlertSummary[];
}

export interface AuditLogEntry {
  id: string;
  action: string;
  service?: string;
  actor: string;
  status: string;
  message: string;
  metadata: Record<string, unknown>;
  created_at: string;
}
