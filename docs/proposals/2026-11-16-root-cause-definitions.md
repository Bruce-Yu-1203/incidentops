# Draft: root-cause definitions for the investigation prompt (P8)

- **Date:** 2026-11-16
- **Status:** Draft for Amy's review. Not in the prompt; `investigation-v13` does not use it.
- **Belongs to:** `2026-11-16-prompt-after-v12.md`

## Purpose and rules for this text

The prompt lists the allowed `root_cause_id` values but not what each one
means. One line per root cause would tell the model its mechanism, what kind of
**current** reading confirms it and its closest look-alike. To avoid teaching
the benchmark's answers, the text is written as general incident-response
knowledge and must not:

- name a benchmark case or scenario;
- describe how the lab injects a fault (controllers, fixtures, tokens, profiles);
- copy a threshold, probe or query from a scenario manifest;
- be derived from a scenario's expected evidence (`required_evidence`).

`tests/unit/test_root_cause_definitions_draft.py` checks the block below for
case ids and for strings that occur only in scenario manifests, so the same
check holds when the text moves into the prompt.

## Draft text (would become a `<root_causes>` block)

<!-- root-cause-definitions:start -->
```text
<root_causes>
Each root cause: its mechanism; the current reading that confirms it; its closest look-alike.
- consumer_paused: the consumer stopped fetching on purpose or by control, so lag grows while the broker and producers are healthy; confirmed by the consumer's own status reporting it paused or stopped now; look-alike consumer_slow, where it still processes.
- consumer_slow: the consumer still processes but more slowly than messages arrive, so lag grows steadily; confirmed by a non-zero processing rate below the produce rate now; look-alike consumer_paused (rate zero) or a slow dependency of the consumer.
- kafka_broker_unavailable: clients cannot reach the broker, so producers fail to send and consumers stop receiving; confirmed by the broker being unreachable or producers reporting send errors now; look-alike consumer_paused, where the broker and producers are fine.
- postgres_lock_contention: sessions wait on a lock another session holds, so queries stall while the database is up; confirmed by sessions currently waiting on a lock with a holder present; look-alike connection_pool_exhausted, where sessions cannot connect at all.
- postgres_unavailable: the database refuses or drops connections; confirmed by the database being down or unreachable in the latest reading; look-alike connection_pool_exhausted, where the server is up but has no free slots.
- redis_unavailable: the cache is unreachable or rejects clients, so cache-backed requests fail; confirmed by the cache being down or refusing clients now; look-alike application_error, where the cache is healthy.
- application_error: a code path in the running release fails without a recent change; confirmed by current errors or stack traces in the service with dependencies, releases and configuration unchanged and healthy; look-alike bad_deployment.
- bad_deployment: a new release introduced the failure; confirmed by a release shortly before the onset and errors that started with it; look-alike config_error (a runtime setting changed, not the release) or application_error (no change).
- application_memory_leak: the process holds on to memory it no longer needs, so resident memory rises without leveling off under steady load; confirmed by memory still rising in the latest samples, usually after a change; look-alike a one-off memory step or a traffic surge that raises memory with load.
- cpu_saturation: the process is CPU-bound, so latency rises at normal traffic; confirmed by high CPU now without a matching rise in request rate; look-alike traffic_surge, where CPU follows more requests.
- traffic_surge: request volume rises far above its usual level and overloads the service; confirmed by the current request rate well above its recent baseline, coming from its clients; look-alike cpu_saturation, where traffic is normal.
- connection_pool_exhausted: idle or held sessions occupy the database's connection slots, so new clients are refused while the server is up; confirmed by connections at or near the server limit now; look-alike postgres_unavailable or postgres_lock_contention.
- config_error: a runtime configuration change without a release causes the failure; confirmed by a configuration change shortly before the onset with the release unchanged; look-alike bad_deployment.
- insufficient_evidence: no mechanism is shown by a current reading, or the symptom has already recovered; use it when the checks above find nothing current; it is a correct answer, not a failure.
</root_causes>
```
<!-- root-cause-definitions:end -->

## Open points for review

1. Wording per line: does each look-alike match how you would tell them apart?
2. Length: about 3,400 characters (about 800 tokens), cached like the rest of
   the system prompt. Shorter option: drop the look-alike part (about half).
3. Version: merging it would be `investigation-v14`, so it should be measured
   apart from v13, or together with v13 before the first run.
