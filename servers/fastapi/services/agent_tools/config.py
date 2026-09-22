import os


def external_agent_mode() -> bool:
    return os.getenv("PRESENTON_EXTERNAL_AGENT", "").lower() in {"1", "true"}


AGENT_INSTRUCTIONS = """Presenton stores and edits presentations; this mode never runs an internal agent
or Mem0 memory. The external caller owns reasoning and conversation memory; Presenton persists
the manuscript UI, speaker notes, revisions and operation receipts without a memory model.
Call capabilities to discover the current tools and native JSON schemas. Open a separate caller
session for each agent or browser tab; keep its token private and send X-Presenton-Session.
Create with an operationId chosen BEFORE the request. Supply outlines using native tools,
obtain the user's outline confirmation, call confirmOutline, then list/select a V2 template.
For multi-page work, prefer agent_mutate_document_batch: submit all prepared outlines plus
confirmOutline in ONE atomic batch after user approval; after template selection, submit all
prepared saveSlide operations plus completeDocument in ONE batch. Limit 20 operations; split
larger decks into bounded batches. One changed batch advances revision once, not per item.
Any rejected item rolls back the whole batch; use failedIndex to fix it and a new operationId.
Cache capabilities and the returned revision. Read selected layout schemas in ONE agent_read_tools
request; avoid exploring every layout or rereading the entire manuscript between successful writes.
Read tools (mutates=false) go to agent_read_tool(s); writes go to agent_mutate_document(_batch).
saveSlide.content must be a JSON string; replacing an existing page uses replaceOldSlideAtIndex=true.
Read the selected layout schema before saveSlide. Supply existing owned asset URLs, never
image-generation prompts alone. Indices are strictly zero-based. Current persisted UI and
speaker notes are authoritative; layout text lengths are hints, structural constraints remain.
Read the document, then send expectedRevision and a unique operationId with each mutation.
There are no writer leases, epochs or acquire/renew/release operations. Keep arguments and
operationId unchanged when retrying an uncertain response; check its receipt first.
On a revision conflict reread and replan; do not silently overwrite newer content.
Responses distinguish applied, noop and rejected; rejected is not saved.
Complete only after every outline page has a persisted nonblank slide. This API currently
supports controlled server editing. Browser handoff, durable generation jobs, fixed-revision
export and host delivery are not exposed yet; do not report them as completed.
"""
