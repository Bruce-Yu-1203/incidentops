import { describe, expect, it } from "vitest";

import {
  ACTIONS,
  ROOT_CAUSES,
  actionDescription,
  actionLabel,
  rootCauseDescription,
  rootCauseLabel,
  rootCauseOptions,
  templateLabel,
  toolLabel,
} from "./vocabulary";

// Mirrors ROOT_CAUSE_TAXONOMY in incidentops/agent/prompt.py (investigation-v6).
const TAXONOMY = [
  "consumer_paused",
  "consumer_slow",
  "kafka_broker_unavailable",
  "postgres_lock_contention",
  "postgres_unavailable",
  "redis_unavailable",
  "application_error",
  "bad_deployment",
  "application_memory_leak",
  "cpu_saturation",
  "traffic_surge",
  "connection_pool_exhausted",
  "config_error",
  "insufficient_evidence",
];

// Mirrors the action rules in config/policy.yaml (p4).
const POLICY_ACTIONS = [
  "resume_test_consumer",
  "release_lab_db_blocker",
  "restart_lab_dependency",
  "rollback_demo_api",
  "stop_lab_cpu_load",
  "reset_lab_load_profile",
  "release_lab_idle_connections",
  "revert_lab_config",
];

describe("vocabulary", () => {
  it("names and explains every root cause in the taxonomy", () => {
    expect(Object.keys(ROOT_CAUSES)).toEqual(TAXONOMY);
    for (const id of TAXONOMY) {
      expect(rootCauseLabel(id)).not.toContain("_");
      expect(rootCauseDescription(id)?.endsWith(".")).toBe(true);
    }
  });

  it("names and explains every policy action", () => {
    expect(Object.keys(ACTIONS).sort()).toEqual([...POLICY_ACTIONS].sort());
    expect(actionLabel("revert_lab_config")).toBe("Revert the configuration");
    expect(actionDescription("reset_lab_load_profile")).toContain("baseline request rate");
  });

  it("labels the new tools and evidence templates", () => {
    expect(toolLabel("get_redis_health")).toBe("Redis health");
    expect(toolLabel("get_recent_config_changes")).toBe("config changes");
    expect(templateLabel("api_memory_growth_rate")).toBe("API memory growth rate");
    expect(templateLabel("postgres_connection_utilization")).toBe("database connection usage");
    expect(templateLabel("api_request_rate_vs_baseline")).toBe("API traffic vs its usual level");
  });

  it("falls back to a readable form for unknown identifiers", () => {
    expect(rootCauseLabel("disk_full")).toBe("disk full");
    expect(rootCauseDescription("disk_full")).toBeNull();
    expect(actionLabel("scale_out")).toBe("scale out");
    expect(toolLabel("get_something_new")).toBe("something new");
  });

  it("offers every root cause as a history filter, plus recorded extras", () => {
    const options = rootCauseOptions(["cpu_saturation", "legacy_cause"]);
    expect(options.map((o) => o.id)).toEqual([...TAXONOMY, "legacy_cause"]);
    expect(options.find((o) => o.id === "traffic_surge")?.label).toBe("Traffic surge");
  });
});
