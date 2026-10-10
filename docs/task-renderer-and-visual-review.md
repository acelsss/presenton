# Task renderer and PNG review

## Author self-review update (2026-10-10)

Truth-Seeker now resumes the same page-author SDK session for PNG inspection and local repairs, inheriting the selected model and thinking level. It no longer schedules an independent reviewer in ordinary generation. Presenton remains the deterministic compiler, renderer and evidence/version authority; the existing delivery acknowledgement, current-revision gate and two-repair limit remain in force.

`ElementCompositionErrors` preserves the first code/path/detail and additionally returns up to 20 independent text/bounds issues with a truncation flag. This reduces repeated validation rounds without moving, resizing or rewriting elements automatically. Tests verify multiple errors and unchanged input. Generated node IDs are exposed to the author through native element names and exact element paths.

The new local image `truth-presenton:ppt-author-self-review-20261010` was built from maintained source and installed at ports 17002 (isolated verification) and 15002 (local working instance). Source/bundle/runtime manifests were verified separately: 10,920 maintained inputs, 20,601 live entries, 496 installed Python copies, no mismatches. Prior container and a hash-verified 1,355-file backup remain available. No company deployment occurred. The host report is `docs/PPT_AUTHOR_SELF_REVIEW_ACCEPTANCE.md`; earlier measurements below describe the prior run.

Local implementation and verification: 2026-10-10. The maintained source is this checkout's `scripts/` and `servers/fastapi/`; `presentation-export/` is installed by the existing sync/build process. Do not patch the running application or maintain a second runner source there.

Each owner/generation task owns one Node/Chromium session, prepared while page authors work and reused for saved pages and repairs. The Python worker closes it on completion, cancellation, failure, expiry, deletion or shutdown. JSONL messages are bounded to 64 KiB. At most four task renderers exist; one renderer processes its requests sequentially. Containers use init so descendant processes are reaped after parent loss. Final Linux tests observed normal/shutdown/Node TERM/Python SIGKILL cleanup in 0.22–0.72 s with no live processes or zombies. Individual DB terminal-state tests do not substitute for the full process fault matrix.

Snapshots reference immutable resource files with a manifest and SHA-256. Resource freeze/hash/fsync run outside the SQLite write transaction; the publishing transaction rechecks versions. Node validates snapshot/resource paths, realpaths, sizes and hashes before reading, and the browser accepts only declared resources. Fonts and images are not expanded into the internal HTML/task JSON as Base64. The final LLM image protocol may still encode PNG at the Provider boundary.

`visual_read`, internal delivery acknowledgement and `visual_review` bind PNG evidence to owner/document/task, current pageRevision, theme/resource identity, renderer version and review context. A review-required task completes only when every latest page has a delivered pass. A current needs_changes permits a bounded single-page update; mutation invalidates old evidence. Review attempts and repairs remain bounded across recovery. Truth-Seeker enables this gate on every new generation; other clients must explicitly use the native review-required contract if they need the same guarantee.

PDF uses Chromium's already-ready DOM and fixed 1280×720 CSS pages. Printing resets the root height/overflow inherited from single-page HTML, so later sections are not clipped. PPTX retains export-core. A three-page regression failed with one page before the fix and passed after it; the same reviewed ten-page deck exported as ten PDF pages and ten editable PPTX slides. Completed task status alone is not file or visual acceptance.

## Verification commands

Run Node input checks from the repository root:

```sh
node --test scripts/tests/snapshot-input.test.mjs
```

Run the PDF integration test with the installed module inside the built image:

```sh
PRESENTON_RENDER_MODULE=/app/presentation-export/snapshot-export.mjs node --test /tmp/snapshot-pdf.integration.mjs
```

Copy only test helpers to `/tmp`; do not replace application files. The lifecycle helper `scripts/tests/render-session-lifecycle.py` exercises real Linux processes in an independent test container. Python unit checks cover `test_ppt_workflow_export.py`, `test_ppt_workflow_visual.py` and `test_generation_renderer_lifecycle.py`; use independent app-data/SQLite, and a dedicated PostgreSQL variable for its separate migration/concurrency checks.

The build emits Python/runtime/frontend-source/frontend-bundle manifests. Verify live files with `scripts/verify-runtime-manifest.py`; compare maintained inputs to the emitted source manifests, map installed runner names back to `scripts/`, and list installer-generated egg-info separately. Frontend source and compiled JavaScript do not have identical hashes; they have separate manifests from the same build. The final local image checked 20,601 live entries and 496 Python copies without differences; the host source check covered 10,920 maintained inputs.

## Measured limits

Twenty alternating Chromium comparisons: cold p50 2,030.365 ms, warm p50 533.115 ms/p95 631.11 ms, identical PNG hashes. Startup and prime are separate; this does not measure LLM/queue/deck latency. SQLite twenty-page/four-writer plus four resource-freeze sample: write transaction p95 30.6225 ms, heartbeat max 17.0229 ms. This is not a full large-image render load test.

The real ten-page simple-information sample used Flash image review and required repairs; a separate complex deck stopped with seven passes and three pages at the review limit. V4 Pro blind testing could not see the image despite catalog capabilities, so its earlier claimed passes were excluded. Model credentials were not changed. Do not infer general complex-diagram quality from the simple sample.

Local port 15002 now uses the candidate-built image with init. Existing authenticated eleven-page manuscript revision 15 was readable after replacement. A complete 3,003,188,400-byte/1,250-file storage backup and the original stopped container are retained by Truth-Seeker's local acceptance directory. Four legacy waiting drafts without deadlines were preserved. Company deployment still uses the host project's release gates. Full API tests failed and stalled; full service crash recovery and all complex-media cases remain outside verified acceptance.

The detailed A1–A16 report and private-evidence inventory are in the host checkout's `docs/PPT_RENDER_AND_AGENT_VISUAL_REVIEW_ACCEPTANCE.md`.
