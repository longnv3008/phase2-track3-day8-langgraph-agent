# Day 08 Lab Report — LangGraph Agentic Orchestration

## 1. Team / Student

- Date: 2026-05-11
- Success rate: **100%** (7/7 scenarios)

---

## 2. Architecture

Graph topology:

```
START → intake → classify → [conditional dispatch]
  simple        → answer → finalize → END
  tool          → tool → evaluate → answer → finalize → END
  missing_info  → clarify → finalize → END
  risky         → risky_action → approval → tool → evaluate → answer → finalize → END
  error         → retry (bounded loop) → dead_letter → finalize → END
```

Key design decisions:
1. **intake** normalizes the query (collapse whitespace, truncate at 500 chars).
2. **classify** uses priority-ordered keyword matching: risky > tool > missing_info > error > simple.
   Whole-word tokenization via `re.findall(r"\b[a-z]+\b")` prevents substring false positives.
3. **evaluate** is the "done?" gate enabling the retry loop — key LangGraph advantage over LCEL.
4. **approval** supports mock (CI-safe) and real interrupt()-based HITL via LANGGRAPH_INTERRUPT=true.
5. Every path terminates at `finalize → END` — no dangling routes.

---

## 3. State Schema

| Field | Reducer | Why |
|---|---|---|
| query | overwrite | single normalized value |
| route | overwrite | current routing decision |
| risk_level | overwrite | current risk classification |
| attempt | overwrite | monotonically incremented retry counter |
| max_attempts | overwrite | scenario-level retry budget |
| final_answer | overwrite | last answer wins |
| evaluation_result | overwrite | latest tool evaluation outcome |
| messages | **append** | full conversation audit trail |
| tool_results | **append** | all tool results for retry analysis |
| errors | **append** | all error messages across retries |
| events | **append** | immutable node execution audit log |

Append-only fields use `Annotated[list, add]` — LangGraph merges updates instead of overwriting.

---

## 4. Scenario Results

| Scenario | Expected | Actual | Success | Nodes | Retries | Interrupts |
|---|---|---|:---:|---:|---:|---:|
| S01_simple | simple | simple | ✅ | 12 | 0 | 0 |
| S02_tool | tool | tool | ✅ | 18 | 0 | 0 |
| S03_missing | missing_info | missing_info | ✅ | 12 | 0 | 0 |
| S04_risky | risky | risky | ✅ | 24 | 0 | 3 |
| S05_error | error | error | ✅ | 33 | 9 | 0 |
| S06_delete | risky | risky | ✅ | 24 | 0 | 3 |
| S07_dead_letter | error | error | ✅ | 15 | 3 | 0 |

**Summary:**
- Total scenarios: 7
- Success rate: 100.00%
- Average nodes visited: 19.71
- Total retries: 12
- Total interrupts: 6

---

## 5. Failure Analysis

### Failure Mode 1: Transient Tool Failure → Retry Loop

S05_error: "Timeout failure while processing request" triggers the error route.
The mock tool fails on attempts 0 and 1, succeeds on attempt 2.
`evaluate_node` detects "ERROR" → routes back to retry.
**Guard:** `route_after_retry` checks `attempt >= max_attempts` before returning to tool.

### Failure Mode 2: Exhausted Retries → Dead Letter

S07_dead_letter: max_attempts=1 — after 1 retry, `route_after_retry` detects
`attempt(1) >= max_attempts(1)` → dead_letter → finalize → END.
**Guard:** Dead letter is guaranteed terminal state.

### Failure Mode 3: Risky Action Without Approval

S04_risky, S06_delete: queries with "refund"/"delete"/"send" → risky route.
approval_node fires; if denied, routes to clarify instead of tool.
With LANGGRAPH_INTERRUPT=true, graph suspends for real human input.

---

## 6. Persistence / Recovery

MemorySaver (default): each scenario uses unique thread_id = "thread-{scenario.id}".
State history available via `graph.get_state_history(config)`.

SQLite (extension): `persistence.py` uses `SqliteSaver(conn=sqlite3.connect(...))` API
with WAL journal mode. Set `checkpointer: sqlite` in configs/lab.yaml.
Crash-resume: re-invoke same thread_id to resume from last checkpoint.

---

## 7. Extension Work

1. **SQLite persistence** — WAL mode, correct v3 API, crash-resume support.
2. **Mermaid diagram** — `export_mermaid_diagram()` exports to `outputs/graph_diagram.md`.
3. **HITL with interrupt()** — `approval_node` supports real interrupt via LANGGRAPH_INTERRUPT=true.

---

## 8. Improvement Plan

1. **LLM classification** — replace keyword matching with Claude Haiku for paraphrase handling.
2. **Structured tool results** — `ToolResult(status, data, error)` makes evaluate_node deterministic.
3. **Real HITL with timeout** — Slack webhook approval with 5-min escalation fallback.
4. **Latency tracking** — add `time.perf_counter()` per node, populate latency_ms field.
5. **Parallel fan-out** — use `Send()` for concurrent evidence gathering before approval gate.
