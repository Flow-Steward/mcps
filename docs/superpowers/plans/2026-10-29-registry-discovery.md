# Registry discovery implementation plan

**Goal:** Extend the daily refresh to propose new vendor MCP servers and detect duplicate services before publication.

**Architecture:** Read every latest Registry page before changing discovery files. Keep unreviewed proposals under `discovery/candidates/`, outside the published catalog. Extend the fresh-main rolling publisher in `scripts/registry_sync_pr.py` to compare and stage discovery files alongside server changes. Namespace ownership is a signal; the Official badge still requires vendor documentation naming the endpoint.

**Constraints:** Remote HTTPS Streamable HTTP only; no credentialed probes; preserve existing refresh and human review; no automatic merge.

## Tasks

- [x] Add failing tests for opaque cursors, repeated cursors and failed pagination; implement full Registry listing.
- [x] Add failing tests for name, normalized product title, curated and upstream endpoint duplicates, template separation, inactive/aggregator filtering, idempotence and bounded candidate queues; implement discovery and draft staging.
- [x] Integrate optional `sync --discover`, fail on incomplete refresh, and check duplicates in manual `add` before probes or writes.
- [x] Update daily workflow and rolling publisher to include new files and update one PR. Document review, rejection and promotion. Preserve the automation-owned branch and reviewer-notification rules merged into main during implementation.
- [x] Run the full catalog tests and validator in a disposable container; inspect the final diff and publish a draft implementation PR.

## Review focus

Pagination must not silently truncate; endpoint matching must preserve path case and query semantics; templates must not identify different customer installations as duplicates; failed fetches must not delete pending proposals; repeated runs must not open repeated PRs or change identical files. Tests belong to the owning task above.

## Market-priority follow-up

- Match the full `coverage/services.json` scope by exact normalized names/IDs and reviewed aliases; ambiguous product editions get no boost.
- Rank reviewed vendor endpoints before namespace/domain candidates, other scoped matches and general candidates. Within groups prioritize uncovered services over reported indirect routes and current catalog/extension presence.
- Keep provenance separate from publication approval. Record vendor evidence, access caveats and coverage rationale in review metadata and PR text; list reviewed vendor URLs absent from Registry for manual onboarding.
- Bound active proposals to 25 while retaining deferred draft edits, so new market candidates cannot be starved by an earlier general batch.
- Verify spoofed/mixed endpoints, aliases, current-versus-reported coverage, ambiguity, pending displacement, idempotence and the saved complete Registry snapshot.
