# RedisVL MCP connection

Run the official [RedisVL MCP server](https://redis.io/docs/latest/develop/ai/redisvl/user_guide/how_to_guides/mcp/) in `streamable-http` mode against existing Redis Search indexes. Configure [JWT authentication](https://redis.io/docs/latest/develop/ai/redisvl/user_guide/how_to_guides/mcp_authentication/) and place the HTTP service behind HTTPS. Keep Redis and embedding-provider credentials on that server.

In Flow Steward enter the MCP service hostname and `Bearer <access-token>` in Authorization. The token must have the configured audience and connection/read/write scopes. This header profile does not refresh expiring tokens. Configure RedisVL's allowed hosts/origins for the public hostname used by clients and the headers forwarded by your TLS proxy.

The server exposes index discovery, configured search tools and record upserts when writes are enabled. Depending on index configuration, search can be full-text, vector or hybrid; embedding support requires its provider dependencies and credentials. Document deletion and general Redis key commands are outside this MCP surface. This connection is not a replacement for the separate Redis workflow extension's key/hash/list operations.

Configuration was reviewed against official documentation; no authenticated customer deployment was available for an end-to-end check.
