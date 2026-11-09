# IncidentOps

Reliable, observable, policy-governed AI SRE runtime — **lab prototype, under
construction**. No benchmark results have been measured yet.

IncidentOps ingests alerts, lets a bounded read-only agent collect evidence,
and routes any proposed remediation through deterministic policy, exact human
approval, an isolated executor and an independent verifier. See:

- [Project constitution](constitute.md) — mandatory working rules
- [Requirements](docs/requirements.md) · [Architecture](docs/architecture.md) ·
  [Technology stack](docs/tech-stack.md) · [Roadmap](docs/roadmap.md) ·
  [Benchmark](docs/benchmark.md)
- [Architecture decision records](docs/adr/)

## Development quick start

Requirements: macOS or Linux, [uv](https://docs.astral.sh/uv/) ≥ 0.5,
Python 3.12, Docker with Compose v2.

```bash
make install   # create .venv from uv.lock
make check     # lint + format check + type check + tests
```
