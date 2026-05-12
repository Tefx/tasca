# Foundation conformance/wiring dispositions

Source step: `fix-foundation-conformance-wiring`.

## Patron identity and idempotency split

Authoritative source: `docs/tasca-mcp-interface-v0.1.md`, section 3 requires
write tools to accept `dedup_id` and return the original successful response on
dedup hits. Existing MCP runtime compatibility also treats `display_name`/`name`
as a stable patron identity key. Foundation ownership therefore implements both:
explicit `dedup_id` retry idempotency and display-name return-existing behavior.
This preserves original kind/timestamp/patron_id for same-name registration while
still avoiding silent retry duplication when a client supplies `dedup_id`.

## Table create transport-only fields

Authoritative source: `docs/tasca-mcp-interface-v0.1.md`, section 5.2
`tasca.table.create`, names `created_by`, `title`, `host_ids`, `metadata`,
`policy`, `board`, `dedup_id`, `invite_code`, and `web_url`.

Foundation ownership now covers shared creation defaults and MCP response shape:
`create_discussion_table` accepts the spec aliases/default objects and MCP returns
the spec-visible aliases alongside legacy compatibility fields. Persistence of
`metadata`, `policy`, `board`, invite URL routing, and distinct invite-code
generation remains non-intersecting with this foundation gate because the
current table schema has no columns for those surfaces and runtime update logic
only logs metadata/policy/board patches. Downstream owner: `dup_adoption` table
metadata persistence/API adoption, gated after `retest-foundation-conformance-wiring`.

## Batch-delete spec ambiguity

Authoritative MCP spec v0.1 lists the tool metadata in the runtime contract but
does not define a normative `tasca.table.delete_batch` tool section. Foundation
closure is therefore limited to wiring the existing shared operation into MCP so
no test-only/dead-export operation remains. Normative input/output contract
clarification is a non-intersecting product/spec task for downstream owner
`dup_adoption.batch-delete-spec-clarification`; it does not block this gate
because no current blocker asserts a missing batch-delete wire after adoption.
