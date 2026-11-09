# Observability plane

Prometheus, Alertmanager, OpenTelemetry Collector, Loki, Tempo, Grafana and
exporters for the Fault Lab. IncidentOps reads these backends; it does not
replace them.

| Component | Purpose | Local URL |
|---|---|---|
| Prometheus | Scrapes lab services and exporters every 5 s; evaluates `prometheus/rules` | http://127.0.0.1:9090 |
| Alertmanager | Sends lab alerts to `POST /api/v1/alerts/alertmanager` with a bearer token file | http://127.0.0.1:9093 |
| OTel Collector | Receives OTLP from the lab API; traces → Tempo, logs → Loki | (lab network only) |
| Loki / Tempo | Log and trace storage | http://127.0.0.1:3100 · http://127.0.0.1:3200 |
| Grafana | Dashboards ("IncidentOps Fault Lab"); anonymous viewer access | http://127.0.0.1:3000 |
| postgres / redis / kafka exporters | Lab PostgreSQL (`pg_monitor` role with 1 s lock and 3 s statement timeouts; `stat_user_tables` disabled so lock fixtures cannot blind it), Redis and consumer-group lag | (lab network only) |

The fault controller is never scraped: fault state is benchmark ground truth.
Container CPU/memory (cAdvisor) is deferred because it needs Docker socket
access; see ADR-018.

## Alert rules

| Rule | Condition (initial, uncalibrated) | `service_id` |
|---|---|---|
| `lab_api_latency_p95_high` | `/items` P95 > 500 ms for 30 s | `lab-api` |
| `lab_api_error_ratio_high` | `/items` 5xx ratio > 5 % for 30 s | `lab-api` |
| `lab_kafka_consumer_lag_high` | `persist-demo-consumer` total lag > 200 for 30 s | `persist-demo-consumer` |

Rules are unit-tested with `promtool test rules` (`prometheus/tests/`), and
labels are checked to never contain root-cause identifiers.

## Running the P1 loop

```bash
make lab-up       # creates the shared incidentops-lab network
make infra-up     # writes infra/secrets/webhook_token from .env, starts the stack
make api-run      # control-plane API on 127.0.0.1:8000 (separate terminal)
make p1-gate      # pause consumer + DB lock -> one incident per service
```
