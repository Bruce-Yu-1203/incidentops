// Plain-language names for the identifiers the control plane records: root
// causes (the investigation taxonomy), remediation actions, read tools and
// metric templates. Unknown identifiers fall back to a readable form, so a
// new backend value never breaks the console.

type Entry = { label: string; description: string };

/** The investigation taxonomy, in the order the agent prompt lists it. */
export const ROOT_CAUSES: Record<string, Entry> = {
  consumer_paused: {
    label: "Consumer paused",
    description: "The Kafka test consumer is paused, so messages pile up unprocessed.",
  },
  consumer_slow: {
    label: "Consumer too slow",
    description:
      "The consumer is running but processes messages slower than they arrive; there is no safe automated fix.",
  },
  kafka_broker_unavailable: {
    label: "Kafka broker unavailable",
    description: "Producer, consumer and monitoring cannot reach the Kafka broker.",
  },
  postgres_lock_contention: {
    label: "Database lock contention",
    description: "A transaction holds an exclusive lock, so queries on the table wait and time out.",
  },
  postgres_unavailable: {
    label: "Database unavailable",
    description: "PostgreSQL refuses connections, so the API and the consumer cannot reach it.",
  },
  redis_unavailable: {
    label: "Cache unavailable",
    description: "Redis cannot be reached, so cache-backed requests fail.",
  },
  application_error: {
    label: "Application error",
    description:
      "A code path in the running release throws errors (stack traces in the logs) without a deploy or config change; it needs a code fix.",
  },
  bad_deployment: {
    label: "Bad deployment",
    description: "Errors started with a new release; rolling back to the pinned release fixes it.",
  },
  application_memory_leak: {
    label: "Memory leak",
    description: "A new release keeps allocating memory on every request.",
  },
  cpu_saturation: {
    label: "CPU saturation",
    description: "Background work uses so much CPU that requests slow down, with normal traffic.",
  },
  traffic_surge: {
    label: "Traffic surge",
    description: "Requests arrive several times faster than usual, which slows the API down.",
  },
  connection_pool_exhausted: {
    label: "Database connections exhausted",
    description:
      "Idle connections hold every ordinary database slot, so the API cannot open new ones although the database is up.",
  },
  config_error: {
    label: "Configuration error",
    description: "A runtime configuration change (no new release) made requests fail.",
  },
  insufficient_evidence: {
    label: "Not enough evidence",
    description: "The signals did not support a confident diagnosis, so a person decides.",
  },
};

export const ACTIONS: Record<string, Entry> = {
  resume_test_consumer: {
    label: "Resume the consumer",
    description: "Unpause the registered Kafka test consumer.",
  },
  release_lab_db_blocker: {
    label: "Release the lock blocker",
    description: "End the one known transaction that holds the exclusive table lock.",
  },
  restart_lab_dependency: {
    label: "Restart the dependency",
    description: "Restore a registered lab dependency (Redis, Kafka or PostgreSQL) to its baseline.",
  },
  rollback_demo_api: {
    label: "Roll back the API",
    description: "Deploy the pinned healthy release of the lab API again.",
  },
  stop_lab_cpu_load: {
    label: "Stop the CPU load",
    description: "Stop the registered background load generator inside the API.",
  },
  reset_lab_load_profile: {
    label: "Reset the traffic profile",
    description: "Return the registered load generator to its baseline request rate.",
  },
  release_lab_idle_connections: {
    label: "Release idle connections",
    description: "Close the known idle database sessions that hold the connection slots.",
  },
  revert_lab_config: {
    label: "Revert the configuration",
    description: "Restore the API's last known good runtime configuration.",
  },
};

export const TOOLS: Record<string, string> = {
  get_kafka_consumer_lag: "consumer lag",
  get_consumer_status: "consumer status",
  query_metrics: "metrics",
  search_logs: "logs",
  search_traces: "traces",
  get_postgres_health: "PostgreSQL health",
  get_redis_health: "Redis health",
  get_recent_deployments: "deployments",
  get_recent_config_changes: "config changes",
  get_target_revision: "running version",
  get_evidence: "recorded evidence",
};

/** Evidence templates: what a recorded reading measured, in plain words. */
export const TEMPLATES: Record<string, string> = {
  api_latency_p95: "API latency (P95)",
  api_error_ratio: "API error rate",
  api_request_rate: "API request rate",
  api_request_rate_vs_baseline: "API traffic vs its usual level",
  api_memory_bytes: "API memory",
  api_memory_growth_rate: "API memory growth rate",
  api_cpu_cores: "API CPU usage",
  api_release: "running API release",
  kafka_group_lag: "consumer lag",
  kafka_group_lag_by_partition: "consumer lag by partition",
  consumer_processing_rate: "consumer processing rate",
  consumer_status: "consumer status",
  producer_send_rate: "producer send rate",
  producer_send_error_rate: "producer send failures",
  kafka_broker_count: "visible Kafka brokers",
  postgres_up: "database availability",
  postgres_health: "database health",
  postgres_connections_by_state: "database connections",
  postgres_connection_utilization: "database connection usage",
  postgres_max_connections: "database connection limit",
  postgres_locks_by_mode: "database locks",
  postgres_exclusive_locks: "exclusive database locks",
  postgres_max_transaction_seconds: "longest database transaction",
  redis_up: "cache availability",
  redis_health: "cache health",
  redis_memory_used_bytes: "cache memory",
  redis_connected_clients: "cache clients",
  redis_commands_per_second: "cache command rate",
  recent_deployments: "recent deployments",
  recent_config_changes: "recent config changes",
  service_logs: "service logs",
  service_traces: "service traces",
};

const readable = (id: string) => id.replace(/^get_/, "").replace(/_/g, " ");

export const rootCauseLabel = (id: string) => ROOT_CAUSES[id]?.label ?? readable(id);
export const rootCauseDescription = (id: string) => ROOT_CAUSES[id]?.description ?? null;
export const actionLabel = (id: string) => ACTIONS[id]?.label ?? readable(id);
export const actionDescription = (id: string) => ACTIONS[id]?.description ?? null;
export const toolLabel = (id: string) => TOOLS[id] ?? readable(id);
export const templateLabel = (id: string) => TEMPLATES[id] ?? readable(id);

/** History filter options: the whole taxonomy first, then any other recorded value. */
export function rootCauseOptions(present: readonly string[]): { id: string; label: string }[] {
  const ids = [...Object.keys(ROOT_CAUSES), ...present.filter((r) => !(r in ROOT_CAUSES)).sort()];
  return ids.map((id) => ({ id, label: rootCauseLabel(id) }));
}
