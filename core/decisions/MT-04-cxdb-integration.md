# CxDB Integration Specification & Project Plan

**Document ID:** MT-04  
**Status:** Draft  
**Date:** 2026-06-23  
**Author:** Beau Roberts / Hermes  
**Related:** MT-01 (Hosting & RBAC), MT-02 (Credential Broker), MT-03 (Registry Split)

---

## 1. Executive Summary

The AI Software Factory currently passes context between pipeline stages as flat string dictionaries (`codergen.{node_id}.output`). This works for short pipelines but creates three fundamental problems:

1. **No crash recovery** — if a pipeline fails at stage 5, all progress is lost
2. **No branching** — parallel exploration or retry-from-failure requires starting over
3. **No context fidelity** — every node receives full context regardless of what it needs

CxDB (Context Database) is StrongDM's open-source AI context store. It stores LLM conversation histories as immutable DAGs with O(1) forking and BLAKE3 content-addressed deduplication. Integrating CxDB solves all three problems and provides an audit trail and visual debugger for free.

This document specifies the integration architecture, data model, implementation phases, and acceptance criteria.

---

## 2. Background

### 2.1 Current Context Flow

```
start → research → design → implement → test → gate → review → exit
                   │                                     │
                   └─ context = {goal, codergen.research.output}
                                                       └─ context = {goal, codergen.research.output,
                                                                      codergen.design.output,
                                                                      codergen.implement.output,
                                                                      codergen.test.output}
```

Each `HermesCodergenBackend.handle()` call receives context as a Python dict, stringifies it into the delegation prompt, and returns the subagent's output text as a new dict key. This is stored only in memory — when the process exits, it's gone.

### 2.2 What CxDB Provides

CxDB is a Rust-based service with:
- **Turn DAG** — conversations are immutable directed acyclic graphs, not flat logs. Each turn has a `parent_turn_id` pointer. Forking is O(1): create a new context pointing to an existing turn.
- **Blob CAS** — content-addressed storage with BLAKE3 hashing. Identical payloads stored once. Zstd compression (level 3, ~70% reduction on typical payloads).
- **Type Registry** — forward-compatible schema evolution. Type IDs use reverse-domain notation (e.g., `com.beauroberts.ai-factory.StageStarted`).
- **HTTP API** (`:9010`) — JSON REST for reads, typed projections for UIs
- **Binary Protocol** (`:9009`) — high-throughput for writes (Go/Rust clients)
- **React UI** — built-in visual debugger with turn cards and DAG view
- **Performance** — ~1ms p50 append, ~1ms p50 read-last-10, ~10K appends/sec single process

### 2.3 Reference Implementation: Kilroy

`danshapiro/kilroy` is a Go implementation of the Attractor spec with deep CxDB integration. Key patterns from `internal/attractor/engine/cxdb_sink.go`:

- Each pipeline run maps to **one CxDB context** (one trajectory head)
- Each stage execution maps to **typed turn events** (`StageStarted`, `StageFinished`, etc.)
- Branching/retries map to **CxDB context forks** (Turn DAG branching)
- All artifacts stored as **blobs in CAS**, referenced from turns
- Resume-from-CxDB uses **CXDB context head** as recovery source

Kilroy tagline: *"git branch is code history; CxDB is run history."*

---

## 3. Architecture

### 3.1 Target Architecture

```
.dot file → Attractor Parser → Stylesheet Router → HermesPipelineRunner
                                                        │
                                          ┌─────────────┼──────────────┐
                                          │             │              │
                                    CxDBSink     HermesCodergenBackend  LinearSink
                                          │             │              │
                                    ┌─────┴─────┐  delegate_task    Linear API
                                    │           │  (Hermes CLI)
                              append_turn   fork_context
                              get_last      get_blob
                                    │           │
                                    └─────┬─────┘
                                          │
                                      CxDB Server
                                    (Rust, :9009/:9010)
                                          │
                                    ┌─────┴─────┐
                                    │           │
                               Turn DAG     Blob CAS
                              (contexts)   (artifacts)
```

### 3.2 Component Responsibilities

| Component | Responsibility |
|-----------|---------------|
| `CxDBSink` | Event handler that translates Attractor pipeline events into CxDB typed turns. Mirrors `LinearSink`'s event-handling pattern. |
| `HermesCodergenBackend` | Modified to fork a CxDB context for each codergen node, append input/output as turns, and pass CxDB context IDs instead of flat strings. |
| `HermesInterviewer` | Modified to append human-gate questions and responses as CxDB turns. |
| `HermesPipelineRunner` | Orchestration: creates root CxDB context, wires `CxDBSink` alongside `LinearSink`, passes CxDB client to backends. |
| CxDB Server | Sidecar service (Docker container), provides HTTP API and binary protocol. |

### 3.3 CxDB Context Lifecycle

```
Pipeline Start
  │
  ├── CxDB: Create context "MT-02 run 2026-06-23T17:30:00Z"
  │   └── Turn 1: com.beauroberts.ai-factory.PipelineStarted {goal, dot_spec}
  │
  ├── Research node (.frontier)
  │   ├── CxDB: Append turn: com.beauroberts.ai-factory.StageStarted {node_id, class, model}
  │   ├── Hermes delegate_task → subagent output
  │   └── CxDB: Append turn: com.beauroberts.ai-factory.StageFinished {node_id, output_hash, duration_ms}
  │
  ├── Design node (.frontier)
  │   ├── CxDB: Append turn: StageStarted
  │   ├── Hermes delegate_task → subagent output
  │   └── CxDB: Append turn: StageFinished
  │
  ├── Implement node (.fast)
  │   ├── CxDB: Append turn: StageStarted
  │   ├── Hermes delegate_task → subagent output
  │   └── CxDB: Append turn: StageFinished
  │
  ├── ... (test, gate, review, exit)
  │
  └── CxDB: Append turn: com.beauroberts.ai-factory.PipelineCompleted {status, total_duration_ms}
```

If `implement` fails and we want to retry:
```
  ├── Implement node (attempt 1, failed)
  │   └── CxDB: Append turn: StageFailed {node_id, error}
  │
  ├── Implement node (attempt 2, retry from design context)
  │   ├── CxDB: Fork context from design's last turn
  │   ├── CxDB: Append turn: StageStarted {node_id, retry=true, forked_from=turn_42}
  │   └── CxDB: Append turn: StageFinished {node_id, output_hash}
```

If we want parallel exploration:
```
  ├── Design node → Fork A (Opus, architecture-first)
  │   ├── CxDB: Fork context from research's last turn → context_A
  │   └── context_A: StageFinished {approach: "monolith"}
  │
  ├── Design node → Fork B (Sonnet, incremental)
  │   ├── CxDB: Fork context from research's last turn → context_B
  │   └── context_B: StageFinished {approach: "incremental"}
```

---

## 4. Data Model

### 4.1 CxDB Type Registry Bundle

All AI Software Factory types are registered under the `com.beauroberts.ai-factory` reverse-domain namespace.

```json
{
  "bundle_id": "com.beauroberts.ai-factory",
  "version": 1,
  "types": [
    {
      "type_id": "com.beauroberts.ai-factory.PipelineStarted",
      "version": 1,
      "fields": [
        {"tag": 1, "name": "goal", "type": "string"},
        {"tag": 2, "name": "dot_spec", "type": "string"},
        {"tag": 3, "name": "stylesheet", "type": "string"},
        {"tag": 4, "name": "issue_id", "type": "string"},
        {"tag": 5, "name": "dry_run", "type": "bool"}
      ]
    },
    {
      "type_id": "com.beauroberts.ai-factory.PipelineCompleted",
      "version": 1,
      "fields": [
        {"tag": 1, "name": "status", "type": "string"},
        {"tag": 2, "name": "total_duration_ms", "type": "u64", "hint": "duration_ms"},
        {"tag": 3, "name": "completed_nodes", "type": "u32"},
        {"tag": 4, "name": "failed_nodes", "type": "u32"}
      ]
    },
    {
      "type_id": "com.beauroberts.ai-factory.StageStarted",
      "version": 1,
      "fields": [
        {"tag": 1, "name": "node_id", "type": "string"},
        {"tag": 2, "name": "node_class", "type": "string"},
        {"tag": 3, "name": "model", "type": "string"},
        {"tag": 4, "name": "provider", "type": "string"},
        {"tag": 5, "name": "fidelity", "type": "string"},
        {"tag": 6, "name": "retry", "type": "bool"},
        {"tag": 7, "name": "forked_from_turn", "type": "u64"}
      ]
    },
    {
      "type_id": "com.beauroberts.ai-factory.StageFinished",
      "version": 1,
      "fields": [
        {"tag": 1, "name": "node_id", "type": "string"},
        {"tag": 2, "name": "output_hash", "type": "string"},
        {"tag": 3, "name": "duration_ms", "type": "u64", "hint": "duration_ms"},
        {"tag": 4, "name": "delegation_payload", "type": "typed_blob", "type_ref": "com.beauroberts.ai-factory.DelegationResult"}
      ]
    },
    {
      "type_id": "com.beauroberts.ai-factory.StageFailed",
      "version": 1,
      "fields": [
        {"tag": 1, "name": "node_id", "type": "string"},
        {"tag": 2, "name": "error", "type": "string"},
        {"tag": 3, "name": "duration_ms", "type": "u64", "hint": "duration_ms"},
        {"tag": 4, "name": "retryable", "type": "bool"}
      ]
    },
    {
      "type_id": "com.beauroberts.ai-factory.HumanGate",
      "version": 1,
      "fields": [
        {"tag": 1, "name": "node_id", "type": "string"},
        {"tag": 2, "name": "question", "type": "string"},
        {"tag": 3, "name": "options", "type": "array", "element_type": "string"},
        {"tag": 4, "name": "response", "type": "string"},
        {"tag": 5, "name": "auto_approved", "type": "bool"}
      ]
    },
    {
      "type_id": "com.beauroberts.ai-factory.DelegationResult",
      "version": 1,
      "fields": [
        {"tag": 1, "name": "goal", "type": "string"},
        {"tag": 2, "name": "model", "type": "string"},
        {"tag": 3, "name": "provider", "type": "string"},
        {"tag": 4, "name": "toolsets", "type": "array", "element_type": "string"},
        {"tag": 5, "name": "output_text", "type": "string"},
        {"tag": 6, "name": "output_hash", "type": "string"},
        {"tag": 7, "name": "dry_run", "type": "bool"}
      ]
    },
    {
      "type_id": "com.beauroberts.ai-factory.CheckpointSaved",
      "version": 1,
      "fields": [
        {"tag": 1, "name": "node_id", "type": "string"},
        {"tag": 2, "name": "turn_id", "type": "u64"},
        {"tag": 3, "name": "context_id", "type": "u64"},
        {"tag": 4, "name": "blob_hash", "type": "string"}
      ]
    }
  ]
}
```

### 4.2 Context Fidelity Mapping

The Attractor spec defines context fidelity modes that control how much prior conversation context is passed to each node. CxDB's branching model maps naturally to these:

| Node Class | Fidelity Mode | CxDB Behavior | Rationale |
|------------|--------------|---------------|-----------|
| `.frontier` | `full` | Fork context from parent turn; subagent receives full conversation history | Architecture/research needs full context |
| `.fast` | `compact` | Fork context + apply projection to compress prior turns | Implementation needs relevant context, not everything |
| `.reasoning` | `summary:medium` | Fork context + apply CxDB projection with summarization hint | Deep reasoning needs summarized context |
| `*` (default) | `compact` | Same as `.fast` | Sensible default |

### 4.3 Checkpoint & Resume

After each node completes, the pipeline saves a checkpoint:

```python
checkpoint = {
    "node_id": current_node.id,
    "cxdb_context_id": cxdb_context_id,
    "cxdb_head_turn_id": cxdb_client.get_head(cxdb_context_id),
    "completed_nodes": list(completed_nodes),
    "timestamp": datetime.now(timezone.utc).isoformat(),
}
```

To resume from a failed pipeline:
1. Load the last checkpoint
2. `get_last(cxdb_context_id, limit=N)` to retrieve prior context
3. Resume the Attractor pipeline from the next unfinished node
4. All prior turn data is in CxDB — no information loss

---

## 5. Implementation Plan

### Phase 1: Foundation (Day 1)

**Goal:** CxDB running locally, Python client working, type bundle registered.

| Step | Task | Details | Acceptance |
|------|------|---------|------------|
| 1.1 | Spin up CxDB | `docker run -p 9009:9009 -p 9010:9010 cxdb/cxdb:latest` | `GET /health` returns 200 |
| 1.2 | Add CxDB to docker-compose | Create `docker-compose.yml` with CxDB service + volume mount | `docker compose up -d cxdb` works |
| 1.3 | Python HTTP client | Create `core/cxdb_client.py` with `create_context()`, `fork_context()`, `append_turn()`, `get_last()`, `get_blob()`, `put_blob()` | Unit tests pass against live CxDB |
| 1.4 | Type bundle registration | Register `com.beauroberts.ai-factory` bundle via `PUT /v1/registry/bundles/com.beauroberts.ai-factory` | `GET /v1/registry/bundles/com.beauroberts.ai-factory` returns the bundle |
| 1.5 | Integration test | Run MT-02 pipeline in dry-run mode with CxDB context creation | PipelineStarted + 8 StageStarted/StageFinished turns visible in CxDB UI |

### Phase 2: CxDBSink (Day 2)

**Goal:** All pipeline events persisted in CxDB via a `CxDBSink` event handler.

| Step | Task | Details | Acceptance |
|------|------|---------|------------|
| 2.1 | Create `CxDBSink` class | `core/hermes_attractor/cxdb_sink.py` — mirrors `LinearSink` pattern, handles both `PipelineEvent` objects and simple tuples | Unit tests with mock CxDB client |
| 2.2 | Wire into `HermesPipelineRunner` | Add `cxdb_client` param to runner constructor; create root context on `pipeline.started`, append turns on each event | Integration test with live CxDB |
| 2.3 | Type-specific handling | `PipelineStarted` → create context + append turn; `StageStarted`/`StageFinished`/`StageFailed` → append typed turns; `PipelineCompleted` → final turn | All 8 MT-02 nodes produce typed turns in CxDB |
| 2.4 | Visual verification | Open CxDB UI at `:9010`, verify turn cards render with proper types | Screenshot in docs |

### Phase 3: Context Flow Refactor (Day 3-4)

**Goal:** Replace flat dict context with CxDB context references in `HermesCodergenBackend`.

| Step | Task | Details | Acceptance |
|------|------|---------|------------|
| 3.1 | Add CxDB client to `HermesCodergenBackend` | Constructor accepts `cxdb_client` and `cxdb_context_id` | Unit tests |
| 3.2 | Fork context per node | Before each `handle()` call, fork the parent context to create a node-specific context | Each node gets its own CxDB context with shared history |
| 3.3 | Store delegation payloads | After each `handle()` call, store the output as a typed blob in CxDB CAS, append a `StageFinished` turn with the blob hash | No output text stored directly in turn — referenced via blob hash |
| 3.4 | Retrieve context from CxDB | Instead of passing flat `codergen.{node_id}.output` strings, use `get_last()` to retrieve prior turns and construct the delegation prompt | MT-02 pipeline produces identical results but with CxDB-backed context |
| 3.5 | Fidelity modes | Implement fidelity mapping: `.frontier` → full context, `.fast` → compact (projection), `.reasoning` → summary | Different node classes receive different context sizes |

### Phase 4: Checkpoint & Resume (Day 5)

**Goal:** Pipeline can crash and resume from the last completed node using CxDB state.

| Step | Task | Details | Acceptance |
|------|------|---------|------------|
| 4.1 | Save checkpoint after each node | Write checkpoint to CxDB CAS as `CheckpointSaved` turn + blob | Checkpoint persists across crashes |
| 4.2 | Resume from checkpoint | Add `HermesPipelineRunner.resume(checkpoint_id)` that loads checkpoint from CxDB and skips completed nodes | Pipeline resumes from failed node without redoing prior work |
| 4.3 | Retry from failure | Add `HermesPipelineRunner.retry(node_id)` that forks the context at the specified node and re-runs from there | Failed node can be retried without losing context |

### Phase 5: Polish & Documentation (Day 6)

| Step | Task | Details | Acceptance |
|------|------|---------|------------|
| 5.1 | Update `ai-factory` skill | Add CxDB integration section, update running instructions | Skill loads correctly |
| 5.2 | Update pipeline templates | Add CxDB context params to MT-02 template | Template validates and runs |
| 5.3 | Write CxDB docker-compose | Production-ready config with volume mounts and health checks | `docker compose up` starts CxDB + runs pipeline |
| 5.4 | Update this spec | Mark as Approved, add implementation notes | Decision record updated |

---

## 6. File Structure

```
core/
├── cxdb_client.py                    # NEW: CxDB HTTP client (Python)
├── hermes_attractor/
│   ├── __init__.py                   # UPDATE: Export CxDBSink
│   ├── backends.py                   # UPDATE: CxDB context forking + blob storage
│   ├── interviewer.py               # UPDATE: Append human-gate turns to CxDB
│   ├── cxdb_sink.py                  # NEW: Event handler → CxDB typed turns
│   ├── linear_sink.py                # UNCHANGED
│   └── runner.py                     # UPDATE: Accept cxdb_client, wire CxDBSink
├── linear_sync.py                    # UNCHANGED
├── decisions/
│   ├── MT-01-hosting-rbac.md         # UNCHANGED
│   └── MT-04-cxdb-integration.md     # NEW: This document
docker-compose.yml                    # NEW: CxDB service definition
pipelines/
└── mt-02-credential-broker.dot       # UPDATE: Add CxDB context params
tests/
├── test_cxdb_client.py              # NEW: CxDB client unit tests
├── test_cxdb_sink.py                # NEW: CxDBSink unit tests
└── test_pipeline_with_cxdb.py        # NEW: End-to-end pipeline test with CxDB
```

---

## 7. API Surface: CxDB Python Client

```python
class CxDBClient:
    """HTTP client for CxDB server (:9010)."""

    def __init__(self, base_url: str = "http://localhost:9010"):
        self.base_url = base_url

    # Contexts
    def create_context(self, tag: str | None = None) -> Context
    def fork_context(self, context_id: int, turn_id: int, tag: str | None = None) -> Context
    def get_context(self, context_id: int) -> Context
    def list_contexts(self, tag: str | None = None) -> list[Context]

    # Turns
    def append_turn(self, context_id: int, type_id: str, version: int, payload: dict,
                    idempotency_key: str | None = None) -> Turn
    def get_last(self, context_id: int, limit: int = 10, view: str = "typed") -> list[Turn]
    def get_turns(self, context_id: int, view: str = "typed", limit: int = 100,
                  before_turn_id: int | None = None) -> list[Turn]

    # Blobs
    def put_blob(self, data: bytes) -> str  # Returns BLAKE3 hash
    def get_blob(self, hash: str) -> bytes

    # Type Registry
    def publish_bundle(self, bundle_id: str, bundle: dict) -> None
    def get_bundle(self, bundle_id: str) -> dict

    # Health
    def health(self) -> dict
    def stats(self) -> dict
```

---

## 8. Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CXDB_URL` | `http://localhost:9010` | CxDB HTTP API endpoint |
| `CXDB_BINARY_URL` | `http://localhost:9009` | CxDB binary protocol endpoint (future: high-throughput writes) |
| `CXDB_ENABLED` | `false` | Enable CxDB integration (set to `true` when CxDB is running) |
| `CXDB_TYPE_BUNDLE` | `com.beauroberts.ai-factory` | Type bundle ID for factory-specific types |
| `CXDB_CONTEXT_FIDELITY` | `compact` | Default context fidelity mode |

### Docker Compose

```yaml
version: "3.8"
services:
  cxdb:
    image: cxdb/cxdb:latest
    ports:
      - "9009:9009"  # Binary protocol
      - "9010:9010"  # HTTP API
    environment:
      CXDB_DATA_DIR: /data
      CXDB_BIND: "0.0.0.0:9009"
      CXDB_HTTP_BIND: "0.0.0.0:9010"
      CXDB_LOG_LEVEL: info
      CXDB_ENABLE_METRICS: "true"
    volumes:
      - cxdb_data:/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9010/health"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  cxdb_data:
```

---

## 9. Acceptance Criteria

### Must Have (Phase 1-3)

- [ ] CxDB runs locally via Docker with no manual setup beyond `docker compose up`
- [ ] All pipeline events (`PipelineStarted`, `StageStarted`, `StageFinished`, `StageFailed`, `PipelineCompleted`) are persisted as typed CxDB turns
- [ ] Each codergen node forks its own CxDB context from the parent turn
- [ ] Delegation payloads are stored as CxDB blobs, referenced from turns (not stored inline)
- [ ] `HermesPipelineRunner(dry_run=True, cxdb_enabled=True)` completes MT-02 pipeline with all turns visible in CxDB UI
- [ ] Fidelity modes work: `.frontier` nodes receive full context, `.fast` nodes receive compact context
- [ ] CxDB client handles connection failures gracefully (log warning, continue pipeline without CxDB)

### Should Have (Phase 4)

- [ ] `HermesPipelineRunner.resume(checkpoint_id)` correctly skips completed nodes
- [ ] `HermesPipelineRunner.retry(node_id)` forks context and re-runs from the specified node
- [ ] Pipeline crash + resume produces identical results to a full run

### Nice to Have (Phase 5+)

- [ ] CxDB UI accessible via proxy at `/cxdb/` on the factory dashboard
- [ ] Binary protocol client for high-throughput writes (Phase 5: Python async client using `:9009`)
- [ ] Prometheus metrics from CxDB exposed at `:9011` and scraped by factory monitoring
- [ ] Multi-tenant CxDB contexts (one per Linear issue, with tags)

---

## 10. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| CxDB v1 is single-process, no replication | High | Medium | Accept for now; CxDB is sidecar, data can be backed up from volume |
| CxDB Python client doesn't exist yet | Medium | Low | HTTP API is straightforward; ~150 lines to wrap |
| Binary protocol changes before stable | Medium | Low | Use HTTP API exclusively; add binary protocol later for performance |
| Context fidelity projection is complex | Medium | Medium | Start with `full` fidelity only; add `compact` and `summary` incrementally |
| Docker dependency for local dev | High | Low | CxDB is Rust; could compile locally if Docker is unavailable |
| CxDB repo goes stale / unmaintained | Low | High | We own our data; Turn DAG format is documented; worst case we can migrate to SQLite + content-addressed blobs |

---

## 11. Alternatives Considered

### SQLite + Content-Addressed Blobs (Build Our Own)
- **Pros:** No external dependency, Python-native, easy to embed
- **Cons:** No DAG branching (would need to build), no type registry, no visual debugger, no binary protocol, would be reinventing CxDB
- **Decision:** Rejected. CxDB provides all of these out of the box and is Apache 2.0.

### Redis Streams
- **Pros:** Fast, append-only, pub/sub
- **Cons:** No branching, no content deduplication, no type system, no visual debugger
- **Decision:** Rejected. Redis streams are logs, not DAGs.

### Git (Store Turns as Commits)
- **Pros:** Already using git, immutable, content-addressed
- **Cons:** Git is for code, not conversation turns. No O(1) forking of sub-conversations. No type projection. Awkward API for this use case.
- **Decision:** Rejected. "Git branch is code history; CxDB is run history."

---

## 12. Open Questions

1. **Binary protocol or HTTP-only?** The Go and Rust clients use the binary protocol (`:9009`) for max throughput. Our Python client starts with HTTP (`:9010`). Should we add a binary protocol client later? Recommendation: start HTTP, add binary only if throughput is a bottleneck.

2. **CxDB context per pipeline run or per Linear issue?** Current design: one CxDB context per pipeline run. Alternative: one context per Linear issue, with forks for each run. This would give us issue-level history. Recommendation: start with per-run, add per-issue grouping via CxDB tags later.

3. **Should CxDB be required or optional?** Current design: `CXDB_ENABLED` env var defaults to `false`. Pipeline runs without CxDB just like today. Recommendation: keep optional. CxDB is a power-up, not a dependency.

4. **Artifact size limit?** CxDB v1 doesn't sub-chunk blobs >1MB. Our delegation payloads could exceed this for large code generation outputs. Recommendation: store payloads >512KB as separate blob files, reference from turn via hash. CxDB just stores the hash.