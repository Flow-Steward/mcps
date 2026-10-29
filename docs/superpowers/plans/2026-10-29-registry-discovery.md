# Registry discovery implementation plan

**Goal:** Extend the daily refresh to propose new vendor MCP servers and detect duplicate services before publication.

**Architecture:** Read every latest Registry page before changing discovery files. Keep unreviewed proposals under `discovery/candidates/`, outside the published catalog. Reuse a single open review PR, with bounded proposals and explicit duplicate reasons. Namespace ownership is a signal; the Official badge still requires vendor documentation naming the endpoint.

**Constraints:** Remote HTTPS Streamable HTTP only; no credentialed probes; preserve existing refresh and human review; no automatic merge.

## Tasks

- [x] Add failing tests for opaque cursors, repeated cursors and failed pagination; implement full Registry listing.
- [x] Add failing tests for name, normalized product title, curated and upstream endpoint duplicates, template separation, inactive/aggregator filtering, idempotence and bounded candidate queues; implement discovery and draft staging.
- [x] Integrate optional `sync --discover`, fail on incomplete refresh, and check duplicates in manual `add` before probes or writes.
- [x] Update daily workflow to stage new files, validate proposals and update one PR. Document review, rejection and promotion.
- [x] Run the full catalog tests and validator in a disposable container; inspect the final diff and publish a draft implementation PR.

## Review focus

Pagination must not silently truncate; endpoint matching must preserve path case and query semantics; templates must not identify different customer installations as duplicates; failed fetches must not delete pending proposals; repeated runs must not open repeated PRs or change identical files. Tests belong to the owning task above.
