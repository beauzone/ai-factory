# Graphify Integration Specification & Project Plan

**Document ID:** MT-05  
**Status:** Draft  
**Date:** 2026-06-23  
**Author:** Beau Roberts / Hermes  
**Related:** MT-01 (Hosting & RBAC), MT-04 (CxDB Integration)

---

## 1. Executive Summary

The AI Software Factory's `research` and `design` pipeline stages currently rely on ad-hoc file search (`grep`, `rg`) when subagents explore a codebase. This is slow, noisy, and lacks structural understanding — the subagent doesn't know module boundaries, dependency paths, or call graphs.

**Graphify** (safishamsi/graphify, YC S26) is an AI coding assistant skill that converts any folder of code, docs, or schemas into a queryable knowledge graph using tree-sitter AST parsing and LLM-based semantic extraction. It exposes an MCP server with `query_graph`, `get_node`, `get_neighbors`, and `shortest_path` tools.

Integrating Graphify into the factory gives every `research` and `design` node structured knowledge of the target codebase — dependency maps, call chains, module boundaries — instead of blind file search. The integration is lightweight: Graphify already runs as an MCP server, and our `HermesCodergenBackend` already delegates to subagents that can call MCP tools.

This spec covers how Graphify fits alongside CxDB (MT-04), the integration architecture, implementation phases, and acceptance criteria.

---

## 2. Background

### 2.1 Current Problem

When the factory runs a pipeline like MT-02 (credential broker), the `research` node receives a prompt like:

```
Research the codebase to understand the current credential management architecture.
```

The subagent then searches files with `grep`/`rg`, reads whatever it finds, and synthesizes an answer. This has three failure modes:

1. **No structural understanding** — the subagent doesn't know that `CredentialBroker` is a singleton or that `get_llm_credential()` already exists. It may re-discover or re-propose things that already exist.
2. **No dependency awareness** — the subagent can't trace "what depends on `RegistryClient`?" without reading every file. It misses coupling that a graph query would reveal instantly.
3. **No impact assessment** — the subagent can't answer "if I change this module, what breaks?" without a full codebase traversal.

### 2.2 What Graphify Provides

Graphify converts a codebase into a **property graph** stored as `graph.json`:

- **Nodes**: Files, classes, functions, variables, imports, with properties (language, type, line count, complexity)
- **Edges**: `CALLS`, `IMPORTS`, `CONTAINS`, `INHERITS`, `DEPENDS_ON`, with semantic labels from tree-sitter parsing
- **Extraction**: tree-sitter for 36 programming languages (local, no API needed), LLM APIs for docs/PDFs/images
- **Query**: Natural language (`graphify query "how does credential management work?"`), structural (`graphify path --from A --to B`), and neighbor traversal
- **Export**: Neo4j, FalkorDB, Cypher, GraphML, SVG, Mermaid
- **MCP Server**: stdio/HTTP transport exposing `query_graph`, `get_node`, `get_neighbors`, `shortest_path`, `list_prs`, `get_pr_impact`, `triage_prs`

### 2.3 Relationship to CxDB (MT-04)

CxDB and Graphify solve complementary problems:

| | CxDB (MT-04) | Graphify (MT-05) |
|---|---|---|
| **Stores** | Conversation histories, run context | Code structure, dependency knowledge |
| **Data model** | Immutable DAG (sequential turns) | Property graph (nodes + semantic edges) |
| **Query pattern** | "What happened at turn 42?" | "What depends on `CredentialBroker`?" |
| **When you'd use it** | Pipeline run history, crash recovery | Codebase understanding, impact analysis |
| **Update frequency** | Every pipeline run (append) | Every code change (rebuild or incremental) |
| **Persistence** | CxDB server (Rust sidecar) | `graph.json` file (+ optional Neo4j/FalkorDB) |

Together they form the factory's knowledge layer:

- **CxDB** = "what happened" (run history)
- **Graphify** = "what exists" (code structure)
- **Linear** = "what needs to happen" (task tracking)

---

## 3. Architecture

### 3.1 Target Architecture

```
.dot file → HermesPipelineRunner
                │
    ┌───────────┼───────────┐
    │           │           │
  CxDB       Graphify    Linear
(context)   (knowledge)  (tracking)
    │           │           │
  Turn DAG  graph.json   Issues +
  + blobs   + MCP       Comments
              server
                │
    ┌───────────┼───────────┐
    │           │           │
  research   design     implement
  (frontier) (frontier)  (fast)
    │           │           │
  "What does   "How does  "Which files
   this module  X depend   need to
   do?"         on Y?"     change?"
```

### 3.2 Integration Points

The integration has three surfaces:

1. **Pre-pipeline: Knowledge Graph Build** — Before running a pipeline, `graphify .` builds the knowledge graph for the target repo. This is a one-time cost per codebase version.

2. **During pipeline: MCP Query** — The `HermesCodergenBackend` passes the Graphify MCP endpoint to research and design subagents. They can call `query_graph`, `get_neighbors`, `shortest_path` as tools.

3. **Post-implementation: Impact Analysis** — After the `implement` stage, `graphify prs` or `get_pr_impact` shows which files and modules are affected by the changes. This feeds into the `review` stage.

### 3.3 Component Changes

| Component | Change | Size |
|-----------|--------|------|
| `HermesPipelineRunner` | Add `graphify_url` and `workdir` params; call `graphify extract` before pipeline start | ~50 lines |
| `HermesCodergenBackend` | Add Graphify MCP tools to `toolsets` for research/design nodes; include `graph.json` path in context | ~30 lines |
| `pipelines/*.dot` | Add `knowledge_graph: true` attribute to research/design nodes (optional, defaults to false) | ~10 lines per template |
| `docker-compose.yml` | Add Graphify MCP server service | ~20 lines |
| New: `core/graphify_setup.py` | Pre-pipeline script to build knowledge graph from workdir | ~80 lines |

---

## 4. Implementation Plan

### Phase 1: Stand Up Graphify (Day 1, ~4 hours)

**Goal:** Graphify MCP server running locally, knowledge graph built for mac-mcp-server.

| Step | Task | Details | Acceptance |
|------|------|---------|------------|
| 1.1 | Install Graphify | `pip install graphifyy` in the project venv | `graphify --version` returns version |
| 1.2 | Build knowledge graph for mac-mcp-server | `graphify /Users/beauroberts/Documents/GitHub/mac-mcp-server` | `graph.json` produced in working directory |
| 1.3 | Start MCP server | `python -m graphify.serve --transport stdio` (or HTTP) | MCP server responds to `query_graph` tool calls |
| 1.4 | Verify queries | Query: "How does CredentialBroker work?" / "What depends on RegistryClient?" | Returns structured results with nodes and edges |
| 1.5 | Add to docker-compose | Add Graphify service to `docker-compose.yml` alongside CxDB | `docker compose up graphify` starts the MCP server |

### Phase 2: Wire into Pipeline (Day 2, ~6 hours)

**Goal:** Research and design subagents can query the knowledge graph during pipeline execution.

| Step | Task | Details | Acceptance |
|------|------|---------|------------|
| 2.1 | Create `core/graphify_setup.py` | Script that runs `graphify extract` on a given workdir, produces `graph.json`, starts MCP server if not running | Running the script builds graph.json and confirms MCP server is available |
| 2.2 | Update `HermesPipelineRunner` | Add `knowledge_graph: bool = False` param; when True, call `graphify_setup.build_knowledge_graph(workdir)` before pipeline start | Pipeline with `knowledge_graph=True` builds graph.json before first node |
| 2.3 | Update `HermesCodergenBackend` | When `knowledge_graph=True` and node class is `.frontier`, add Graphify MCP tools to the delegation context. Include `graph.json` path and MCP endpoint in the goal prompt | Research/design subagents can call `query_graph`, `get_neighbors`, `shortest_path` |
| 2.4 | Update MT-02 template | Add `knowledge_graph=true` attribute to research and design nodes | Template validates and runs with knowledge graph enabled |
| 2.5 | End-to-end test | Run MT-02 pipeline in dry-run mode with `knowledge_graph=True` against mac-mcp-server repo | Pipeline completes, subagent goal prompts include Graphify context |

### Phase 3: Impact Analysis (Day 3, ~4 hours)

**Goal:** Post-implementation stage can assess impact of changes via Graphify.

| Step | Task | Details | Acceptance |
|------|------|---------|------------|
| 3.1 | Add impact analysis to `implement` node context | After implementation, run `graphify prs` or `get_pr_impact` to identify affected modules | Impact list included in `implement` node's output context |
| 3.2 | Wire into `review` node | Pass impact analysis to review subagent so it can verify no unintended side effects | Review subagent receives affected module list |
| 3.3 | Update CxDB integration | Store `graph.json` blob hash in CxDB as a pipeline artifact (links knowledge graph version to pipeline run) | CxDB turn includes `graph_artifact_hash` |

### Phase 4: Polish & Documentation (Day 4, ~3 hours)

| Step | Task | Details | Acceptance |
|------|------|---------|------------|
| 4.1 | Update `ai-factory` skill | Add Graphify integration section | Skill loads correctly |
| 4.2 | Update `docker-compose.yml` | Production config with Graphify MCP server + CxDB + health checks | `docker compose up` starts both services |
| 4.3 | Write Graphify README | Add `docs/graphify-setup.md` with install, config, and troubleshooting | README is comprehensive |
| 4.4 | Update this spec | Mark as Approved, add implementation notes | Decision record updated |

---

## 5. Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GRAPHIFY_ENABLED` | `false` | Enable Graphify integration |
| `GRAPHIFY_MCP_URL` | `http://localhost:8765` | Graphify MCP server URL (HTTP transport) |
| `GRAPHIFY_MCP_TRANSPORT` | `stdio` | MCP transport: `stdio` or `http` |
| `GRAPHIFY_LLM_PROVIDER` | `anthropic` | LLM provider for semantic extraction (anthropic, openai, ollama, etc.) |
| `GRAPHIFY_LLM_MODEL` | `claude-sonnet-4-5` | LLM model for semantic extraction |

### DOT Template Attributes

```dot
digraph MT02 {
    graph [goal="Implement credential broker",
           knowledge_graph=true];

    research [label="Research codebase", class="frontier",
              prompt="Research the current credential management architecture.",
              use_knowledge_graph=true];
    design   [label="Design solution", class="frontier",
              prompt="Design the credential broker implementation.",
              use_knowledge_graph=true];
    implement [label="Implement", class="fast",
               prompt="Implement the credential broker."];
    review   [label="Review", class="fast",
              prompt="Review the implementation."];
}
```

Nodes with `use_knowledge_graph=true` will have Graphify MCP tools available to their subagent and the `graph.json` path included in context.

### Docker Compose

```yaml
services:
  # ... CxDB service from MT-04 ...

  graphify:
    build:
      context: .
      dockerfile: Dockerfile.graphify
    ports:
      - "8765:8765"  # MCP HTTP transport
    environment:
      GRAPHIFY_LLM_PROVIDER: anthropic
      GRAPHIFY_LLM_MODEL: claude-sonnet-4-5
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
    volumes:
      - graphify_data:/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8765/health"]
      interval: 10s
      timeout: 5s
      retries: 5
```

---

## 6. DOT Template Changes

The Attractor DOT format supports arbitrary node attributes. We add two:

| Attribute | Scope | Type | Default | Description |
|-----------|-------|------|---------|-------------|
| `knowledge_graph` | graph | bool | `false` | Build knowledge graph before pipeline starts |
| `use_knowledge_graph` | node | bool | `false` | Make Graphify MCP tools available to this node's subagent |

These are custom attributes that `HermesPipelineRunner` reads from the parsed graph and passes to the appropriate handlers.

### Parser Extension

The runner already reads custom graph-level attributes (it extracts `goal`, `model_stylesheet`). We extend it to also extract `knowledge_graph`:

```python
# In HermesPipelineRunner.run()
knowledge_graph = graph_attrs.get("knowledge_graph", "false").lower() == "true"

if knowledge_graph and workdir:
    graph_path = graphify_setup.build_knowledge_graph(workdir)
    runner_context["graph_json_path"] = graph_path
    runner_context["graphify_mcp_url"] = os.environ.get("GRAPHIFY_MCP_URL", "http://localhost:8765")
```

And per-node attribute extraction already handles `class` and `prompt`. We add `use_knowledge_graph`:

```python
# In HermesCodergenBackend.handle()
use_kg = node_attrs.get("use_knowledge_graph", "false").lower() == "true"
if use_kg and context.get("graphify_mcp_url"):
    # Add Graphify context to the delegation prompt
    goal += f"\n\nGraphify MCP endpoint: {context['graphify_mcp_url']}"
    goal += f"\nKnowledge graph: {context.get('graph_json_path', 'graph.json')}"
    toolsets.append("web")  # MCP tools are available via web/browser
```

---

## 7. CxDB Integration (Cross-Reference with MT-04)

Graphify and CxDB complement each other. The integration points are:

### 7.1 Knowledge Graph as CxDB Artifact

When the pipeline builds a knowledge graph, it stores the `graph.json` blob in CxDB's content-addressed storage:

```python
# In HermesPipelineRunner.run()
if knowledge_graph:
    graph_blob_hash = cxdb_client.put_blob(graph_json_bytes)
    runner_context["graph_artifact_hash"] = graph_blob_hash
    # Also stored as a CxDB turn:
    cxdb_client.append_turn(
        context_id=context_id,
        type_id="com.beauroberts.ai-factory.KnowledgeGraphBuilt",
        version=1,
        payload={
            "repo_path": workdir,
            "blob_hash": graph_blob_hash,
            "node_count": graph_data["node_count"],
            "edge_count": graph_data["edge_count"],
        }
    )
```

### 7.2 Impact Analysis Turn

After the `implement` stage, Graphify's `get_pr_impact` results are stored as a CxDB turn:

```python
# Type: com.beauroberts.ai-factory.ImpactAnalysis
{
    "node_id": "implement",
    "affected_files": ["src/mac_mcp_server/services/credentials.py", ...],
    "affected_modules": ["CredentialBroker", "CompanyPackResolver", ...],
    "risk_level": "medium",
    "dependency_paths": [
        ["CredentialBroker", "get_llm_credential", "LLMProvider"],
        ...
    ]
}
```

### 7.3 CxDB Type Registry Addition

Add to the `com.beauroberts.ai-factory` type bundle:

```json
{
    "type_id": "com.beauroberts.ai-factory.KnowledgeGraphBuilt",
    "version": 1,
    "fields": [
        {"tag": 1, "name": "repo_path", "type": "string"},
        {"tag": 2, "name": "blob_hash", "type": "string"},
        {"tag": 3, "name": "node_count", "type": "u32"},
        {"tag": 4, "name": "edge_count", "type": "u32"},
        {"tag": 5, "name": "languages", "type": "array", "element_type": "string"},
        {"tag": 6, "name": "build_duration_ms", "type": "u64", "hint": "duration_ms"}
    ]
},
{
    "type_id": "com.beauroberts.ai-factory.ImpactAnalysis",
    "version": 1,
    "fields": [
        {"tag": 1, "name": "node_id", "type": "string"},
        {"tag": 2, "name": "affected_files", "type": "array", "element_type": "string"},
        {"tag": 3, "name": "affected_modules", "type": "array", "element_type": "string"},
        {"tag": 4, "name": "risk_level", "type": "string"},
        {"tag": 5, "name": "dependency_paths", "type": "string"},
        {"tag": 6, "name": "blob_hash", "type": "string"}
    ]
}
```

---

## 8. Acceptance Criteria

### Must Have (Phase 1-2)

- [ ] Graphify installed and running via `pip install graphifyy`
- [ ] `graphify extract` produces `graph.json` for mac-mcp-server repo
- [ ] Graphify MCP server responds to `query_graph`, `get_node`, `get_neighbors`, `shortest_path`
- [ ] `HermesPipelineRunner(knowledge_graph=True)` builds `graph.json` before pipeline start
- [ ] Research and design subagents receive Graphify MCP endpoint in their context
- [ ] MT-02 pipeline completes in dry-run mode with `knowledge_graph=True`

### Should Have (Phase 3)

- [ ] Post-implementation impact analysis via `get_pr_impact` or `graphify prs`
- [ ] Impact analysis results included in review subagent's context
- [ ] Knowledge graph blob stored in CxDB CAS (requires MT-04)

### Nice to Have (Phase 5+)

- [ ] Incremental graph updates (rebuild only changed files)
- [ ] Graphify dashboard showing knowledge graph visualization
- [ ] Neo4j or FalkorDB persistence for larger codebases
- [ ] Community detection (Leiden algorithm) for module boundary discovery

---

## 9. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Graphify LLM extraction requires API key | High | Low | Use Ollama for local extraction, or skip semantic extraction for code-only repos (tree-sitter works without LLM) |
| `graph.json` size for large repos | Medium | Medium | Start with tree-sitter only (no LLM semantic extraction) for code; add semantic extraction incrementally. For repos >10K files, use Neo4j/FalkorDB instead of in-memory JSON |
| Graphify MCP server stability | Low | Medium | Run as Docker container with health checks and restart policy; fall back to CLI if MCP is unavailable |
| Knowledge graph goes stale as code changes | High | Low | Rebuild graph before each pipeline run (incremental rebuild if available); store graph hash in CxDB to detect staleness |
| Tree-sitter grammar gaps | Low | Low | 36 languages supported; unsupported languages fall back to LLM-based extraction |
| Double-y package name (`graphifyy`) | Medium | Low | Just remember: `pip install graphifyy`, `import graphify` |

---

## 10. Alternatives Considered

### Ad-hoc grep/rg (Current Approach)
- **Pros:** No dependency, works everywhere
- **Cons:** No structural understanding, no dependency tracking, no impact analysis, noisy results
- **Decision:** Current baseline. Graphify adds structure on top of this.

### Sourcegraph
- **Pros:** Code intelligence at scale, supports many languages
- **Cons:** Heavy infrastructure (PostgreSQL, Redis, object storage), overkill for single-repo pipelines, not locally runnable
- **Decision:** Rejected. Too heavy for our use case.

### Glean / Code Search APIs
- **Pros:** Fast semantic code search
- **Cons:** SaaS-only, no local graph, no MCP integration, no impact analysis
- **Decision:** Rejected. Doesn't provide structural graph.

### Custom Tree-Sitter + Neo4j
- **Pros:** Full control, custom schema
- **Cons:** Building and maintaining a tree-sitter → Neo4j pipeline ourselves; Graphify already does this
- **Decision:** Rejected. Graphify already provides this and is Apache 2.0.

---

## 11. Open Questions

1. **Stdio vs HTTP MCP transport?** Graphify supports both. Stdio is simpler for local dev; HTTP is better for Docker. Recommendation: HTTP for Docker (`:8765`), stdio for local dev.

2. **When to rebuild the knowledge graph?** Rebuild before every pipeline run is safe but slow. Options: (a) always rebuild, (b) rebuild only if `graph.json` is older than the last commit, (c) incremental rebuild. Recommendation: start with (a), add (b) as an optimization.

3. **Should research/design nodes always use the knowledge graph, or only when explicitly opted in?** Recommendation: opt-in via `use_knowledge_graph=true` attribute on the DOT node. Some research tasks don't need code structure (e.g., "research competitor pricing models").

4. **LLM provider for semantic extraction?** Graphify supports OpenAI, Anthropic, Gemini, Ollama, DeepSeek, Kimi, Azure, and Bedrock. Recommendation: start with Ollama (local, free) for code-only repos; add Anthropic for docs/PDFs if needed.

---

## 12. File Structure

```
core/
├── graphify_setup.py              # NEW: Pre-pipeline knowledge graph builder
├── hermes_attractor/
│   ├── runner.py                   # UPDATE: Add knowledge_graph param, call graphify_setup
│   ├── backends.py                # UPDATE: Add Graphify context to frontier subagents
│   └── ...
├── decisions/
│   ├── MT-04-cxdb-integration.md  # EXISTING
│   └── MT-05-graphify-integration.md  # NEW: This document
docker-compose.yml                  # UPDATE: Add Graphify service
pipelines/
└── mt-02-credential-broker.dot    # UPDATE: Add knowledge_graph attributes
tests/
└── test_graphify_setup.py          # NEW: Graphify integration tests
```

---

## 13. Summary

| Aspect | Details |
|--------|---------|
| **What** | Integrate Graphify knowledge graph into the AI Software Factory's research and design pipeline stages |
| **Why** | Replace ad-hoc grep-based code search with structured, queryable knowledge of module boundaries, dependencies, and call graphs |
| **How** | Pre-pipeline `graphify extract` builds `graph.json`; MCP server exposes query tools; `HermesCodergenBackend` passes MCP endpoint to frontier subagents |
| **Effort** | ~4 days (1 day stand-up + wire, 1 day pipeline integration, 1 day impact analysis, 0.5 day polish) |
| **Dependencies** | Graphify (`pip install graphifyy`), MCP server transport; CxDB (MT-04) for artifact storage (optional, phase 3+) |
| **Risk** | Low — Graphify is additive, not required. Pipeline runs fine without it (just without structural code knowledge). |