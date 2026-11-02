# Apache Solr MCP connection

Build and deploy the upstream [Apache Solr MCP server](https://github.com/apache/solr-mcp) in HTTP mode (`PROFILES=http`). The upstream project currently documents a source build, rather than a published production container. Put it behind HTTPS and configure its identity provider with `HTTP_SECURITY_ENABLED=true` and `OAUTH2_ISSUER_URI`.

In Flow Steward enter the MCP hostname and `Bearer <access-token>` in Authorization. This is the MCP service at `/mcp`, not the Solr database port. The JWT audience must match the public MCP URL. Tokens are supplied by the operator; this header profile does not perform OAuth login or refresh tokens automatically.

Search and indexing are available. Upstream does not expose document deletion or vector/KNN search, so this route alone does not provide a full document RAG lifecycle. See [HTTP security](https://github.com/apache/solr-mcp/blob/main/docs/security/http.md) and [tool boundaries](https://github.com/apache/solr-mcp/blob/main/src/main/resources/application.properties).

Endpoint, tool and authentication configuration were checked against upstream documentation and source. No authenticated customer deployment was available for an end-to-end check.
