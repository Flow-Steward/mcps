# MongoDB MCP connection

This entry connects to a self-hosted MongoDB MCP HTTP service. It is separate from the Atlas Managed MCP service, whose service-account token flow requires different client support.

Run the official [MongoDB MCP server in HTTP mode](https://www.mongodb.com/docs/mcp-server/local-mcp/configuration/standalone-service/) on a private interface. Put an HTTPS reverse proxy in front of it and configure that proxy to validate bearer access tokens. The MongoDB MCP HTTP process does not itself implement this gateway authentication. Keep database connection strings and Atlas API credentials on that server.

In Flow Steward supply the gateway hostname and `Bearer <gateway-token>` as Authorization. Forward `/mcp` without changing its MCP transport. This header profile does not refresh expiring tokens automatically. The MCP server must be able to reach the intended MongoDB deployment; never substitute a MongoDB database hostname into the MCP address field.

MongoDB roles and MCP read-only settings determine the exposed operations. For read/write use, grant the required MongoDB role and disable read-only mode intentionally; changing this catalog entry cannot grant database permissions.

Configuration was reviewed against official documentation. No authenticated customer MongoDB MCP deployment was available for an end-to-end check.
