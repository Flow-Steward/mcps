# Agent memory over MCP

Review date: 2026-11-08. This catalog supplies external memory tools; it does
not give every Flow Steward agent automatic memory or change a saved workflow.
An agent or deterministic connector step must call the configured tools, with
the appropriate bank, session, user or project scope.

The Core JSON Schema fix preserves vendor tool contracts, including nested
objects, arrays, nullable values and references. Protocol compatibility and
successful persistent-memory operations are different verification scopes.

## Selected popular services

| Service | Supported entry | Verification so far | Live acceptance |
| --- | --- | --- | --- |
| Mem0 | Hosted Streamable HTTP, Bearer API key | Official endpoint/auth reviewed | Requires cloud account; no cloud keys used |
| Hindsight | Hosted bank URL with Bearer key; selfhost HTTP implementation | Selfhost 1.2.0, local LLM and embeddings; actual schema preserved | Both fresh-job routes and deletion/absence passed; hosted authentication not tested |
| Graphiti | Selfhost Streamable HTTP reference server | MCP 1.1.3 / Core 0.31.2, real FalkorDB, local LLM and embeddings; actual schema preserved | Both fresh-job routes and episode deletion/absence passed |
| Cognee | Selfhost Streamable HTTP server | Cognee 1.6.7 / MCP 0.5.8; actual schema preserved | Write/read across fresh agent-tool and deterministic jobs passed; real local LLM agent loop passed; user-wide deletion and absence passed |
| Supermemory | Hosted Streamable HTTP, OAuth DCR | Official endpoint and live OAuth metadata reviewed | Requires cloud account; no cloud keys used |

Acceptance uses the MCP runtime shipped in Core 0.9.12 (the checked
runtime files match the release at `e42a955cec584825be3b4798d990430b7bd371f1`).
The test checkout is `8025410aaeb86f377793337fe43d39904a6d2c0f`; registry selection
and the local LLM provider selection are controlled fixtures.
Local services run on Linux ARM64 behind a private HTTPS proxy, with private
remote-address access enabled for the disposable test network. The keyless
model runtime is Ollama 0.40.15 with `qwen2.5:3b` and
`nomic-embed-text:v1.5`; this is not a cloud-provider acceptance result.

Local acceptance uses real vendor servers and storage: write a unique fact,
close the Core execution/MCP client, retrieve it from a new job with no original
fact in its input, then delete it and verify absence. It checks both the
agent-tool path and the deterministic workflow connector path. Hindsight uses
its native asynchronous `retain`, polls `get_operation` to completion, then
recalls the stored fact. Dependent reads must wait for asynchronous writes to
finish. A real local LLM
case also exercises the Core agent tool loop; its model selection is a test
fixture, not acceptance of an installed LLM extension's control plane.

Cognee's keyless session-memory scenario, with `self_improvement=false`,
verifies its persistent SQLite cache
with explicit session identifiers and keyword recall. It does not establish
semantic extraction, automatic graph consolidation or every vendor feature.
In this tested version, `forget(dataset=...)` reported success but the session
still returned the fact. `forget(everything=true)` on the dedicated disposable
user removed it and fresh recall confirmed absence. Dataset deletion must not
be treated as a verified session-memory deletion guarantee.

The Graphiti MCP 1.1.3 image serves `/mcp`; `/mcp/` redirects, which Core
rejects. The catalog uses the canonical path. New episodes omit `uuid`: in the
tested Graphiti Core 0.31.2, a supplied UUID refers to an existing episode.
Acceptance polls `get_episodes` for the new episode before searching its facts.
Deletion uses the actual episode ID returned by that tool.

## Additional catalog entries

| Service | Entry and prerequisites |
| --- | --- |
| Zep | Hosted MCP with OAuth, organization identity provider and MCP seats; documentation/metadata reviewed, no live acceptance |
| Letta Agents | Hosted HTTP with Bearer API key; delegates to stateful Letta agents, rather than exposing direct memory CRUD. Documentation/auth reviewed only; long runs need asynchronous reply handling; no live acceptance |
| Honcho | Hosted MCP with API key and workspace; documented conclusion storage and retrieval |
| Memori | Hosted MCP with API key/entity headers; documented store/recall tools |
| OpenViking | Own HTTPS deployment of native MCP with API key; configure its model/storage runtime |
| MemOS | Own HTTPS deployment of the official HTTP reference example; no native authentication or delete tool in that example |
| EverOS | Own HTTPS MCP deployment and backend credentials; deletion depends on backend capability |

These additional entries have endpoint, transport and authentication reviewed
from the vendor's documentation or source. No live-memory acceptance is claimed
for them. Selfhost entries describe a customer-operated HTTPS deployment; the
catalog does not host these services or provide their credentials.

## Not listed as direct memory servers

| Project | Reason |
| --- | --- |
| TencentDB Agent Memory | The verified official MCP implementation is stdio; the documented local HTTP backend is REST |
| LangMem | Python/LangGraph SDK tools; no official remote MCP endpoint verified |
| Redis Agent Memory Server | HTTP MCP exists in historical V0, explicitly retained as a research artifact; current supported cloud surface is SDK/REST |

A stdio bridge, backend wrapper, or different agent runtime is a separate
integration and is not introduced by the Core JSON Schema correction.

## Sources

- [Hindsight MCP](https://docs.hindsight.vectorize.io/cloud/mcp/)
- [Mem0 MCP](https://docs.mem0.ai/platform/mem0-mcp)
- [Graphiti MCP](https://github.com/getzep/graphiti/blob/mcp-v1.1.3/mcp_server/README.md)
- [Cognee MCP](https://github.com/topoteretes/cognee/blob/8853378eae5366addb064e758e560a456146ae1a/cognee-mcp/README.md)
- [Supermemory MCP](https://supermemory.ai/mcp/)
- [Zep MCP](https://help.getzep.com/context-mcp-server)
- [Honcho MCP](https://honcho.dev/docs/v3/guides/integrations/mcp)
- [Memori MCP](https://github.com/MemoriLabs/memori-mcp)
- [OpenViking MCP](https://github.com/volcengine/OpenViking/blob/main/docs/en/guides/06-mcp-integration.md)
- [MemOS HTTP example](https://github.com/MemTensor/MemOS/blob/main/examples/mem_mcp/simple_fastmcp_serve.py)
- [EverOS MCP](https://github.com/EverMind-AI/EverOS-MCP)
- [Tencent MCP source](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/feat/server_team/MemoryKnowledge/src/mcp/server.ts)
- [LangMem](https://github.com/langchain-ai/langmem)
- [Redis current and V0 scope](https://github.com/redis/agent-memory-server)
- [Letta hosted MCP](https://docs.letta.com/platform/hosted-mcp)
