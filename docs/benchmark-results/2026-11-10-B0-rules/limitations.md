# Limitations

- Toy fault distribution: six controlled families on one Docker host do not
  represent real production incidents.
- The CPU, memory-leak and bad-release faults are application-level
  simulations inside the lab API (no container or image changes); the Redis
  outage is simulated by an authentication change, not a process restart.
- The benchmark approver approves every pending proposal; it tests the
  authorization path, not human judgement or response time.
- Shared-host resource effects, scrape intervals and alert `for` windows
  affect timing; timings are only comparable within the same environment.
- Small samples: fewer than 5 valid trials per case is a smoke test.
- Model and provider behaviour can drift; the model ID is recorded per trial.
