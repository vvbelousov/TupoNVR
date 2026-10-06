# TupoNVR UX audit and interface refactor

The product remains a small, dark, self-hosted appliance. The work changes presentation and frontend interaction, not recording, storage, authentication, retention, notifications, or API semantics.

## Prioritized audit

| Priority | Problem | Change and benefit | Impact |
| --- | --- | --- | --- |
| Critical | Initial loading resembles an empty or failed system; pending storage checks are red. | Explicit loading states and neutral pending health prevent false incident interpretation. | Frontend state and badges only. |
| Critical | Action errors appear far from the affected object; retry can repeat the wrong request. | Camera/form/destination errors stay local; storage and layout retries repeat their own operations. | Existing API calls, no extra polling. |
| Critical | Login relies on placeholders; live links and timeline are inaccessible by keyboard. | Persistent login labels, actual buttons, visible focus, timeline keyboard controls, native modal focus/escape behavior. | Small native components. |
| Critical | Generic deletion confirmation fails to identify the target. | Camera name and recording-retention consequence appear in the confirmation. | Backend deletion behavior unchanged. |
| High | Camera connection, recording, schedule and advanced fields compete for attention. | Grouped fields, expandable schedules and advanced settings, current values visible on edit, per-camera busy feedback. | Same create/edit workflow and fields. |
| High | Multiview calculates width from the window and can waste space or overflow. | Observe the actual container; stack narrow-screen tiles without overwriting desktop columns/layout. | Browser ResizeObserver; same persisted layout. |
| High | Archive emphasizes a file list, lacks an absolute playback clock and keyboard time seeking. | Playback first, UTC clock, readable timeline ticks and cursor, labeled search controls, smaller secondary list. Expected gaps are informational rather than global errors. | Same timeline/at/adjacent/download APIs. |
| High | Storage does not clearly distinguish protection configuration, readiness and mount detection. | Independent text badges, capacity meter, actionable failure guidance, inline protection feedback. | Same create/adopt/manual safety confirmations. |
| Polish | Controls, spacing, contrast, surfaces and responsive treatment vary between pages. | Shared CSS tokens and feedback/empty-state/modal primitives; restrained surfaces and consistent controls. | No dependency or component-library additions. |

## Page decisions

- **Overview:** retain configured-camera, online, progressing-recording and root-free-space metrics; preserve stable camera columns and independent connectivity/recording colors. Summarize per-destination health from the existing dashboard response with a direct Storage action. Make failures visible without treating intentional pauses as incidents. Provide an actionable no-camera state.
- **Cameras:** retain the established form/list workflow. Separate connection and recording, disclose advanced fields progressively, preserve write-only secrets and partial-update behavior, and summarize diagnostics with a codec-compatibility hint when relevant. Confirm discarding edits only when draft values differ.
- **Multiview:** prioritize video, minimize controls, explain empty views and connecting/reconnecting streams, use semantic remove/open controls and a native dialog. On narrow screens, disable dragging/resizing and adapt visual columns while retaining the saved desktop layout.
- **Archive:** use persistent camera/date/time labels and UTC language; emphasize playback and chronological recording intervals. Support keyboard arrows/Home/End to select a timeline point and Enter/Space to seek. Keep pagination, download, previous/next, sequential playback, midnight transitions and recording gaps.
- **Storage:** preserve explicit enrollment and mismatch handling. Pending checks are neutral; failures include corrective guidance. Protection configuration and mount detection are distinct. Capacity is shown without revealing server paths.
- **Authentication:** persistent labels, password-manager autocomplete and busy/error feedback. Existing login API, sessions and optional authentication remain unchanged.

## Shared design primitives

`ui.tsx` contains Feedback, EmptyState and Modal. CSS defines surfaces, borders, text hierarchy, semantic colors, compact spacing, controls, focus states, forms, disclosures, rows and responsive layouts. Text accompanies all health colors. English and Russian remain supported.

## Recommendations requiring approval

- Move webhook administration into an appliance-settings location only if a new top-level section is approved. For now its existing Storage status panel remains accessible but expandable, with an explanation of server-managed configuration. No new settings or webhook API are introduced.
- Any change to recording-date semantics, archive APIs, authentication, storage enrollment, retention or database structure is outside this interface refactor.

## Existing limitations observed

- The legacy `date=` recording API still filters UTC segment start dates for compatibility. The current interface uses timezone-aware absolute overlap ranges; see the subsequent time/archive feature documentation.
- This audit originally preserved UTC archive times. The subsequent [installation timezone and synchronized archive feature](time-and-archive.md) supersedes that restriction and includes cross-midnight segments through absolute overlap queries.
- Browser live playback still depends on camera codec, WebRTC connectivity and platform support. Camera connectivity and recording health remain independent of live-view success.
- Browser screenshots use deterministic fixtures and synthetic archive video. They demonstrate layout and workflow behavior, not real-camera WebRTC network compatibility.

## Verification

Use the existing TypeScript/Vite build, Node reader tests, Ruff correctness checks, backend unit/RTSP integration tests and opt-in Chromium suite. The browser suite covers both languages, named deletion cancellation, camera creation/editing, neutral paused/pending states, storage feedback, sequential archive playback, timeline keyboard behavior, modal escape/focus restoration, and narrow-screen overflow. Optional `NVR_SCREENSHOT_DIR` captures page screenshots for visual inspection.

Validation completed: 91 backend/integration tests and 11 Chromium tests passed. Both language workflows passed again after the final responsive help-text change. Node tests, TypeScript/Vite production build, Ruff correctness checks, dependency checks, Docker production build/startup/static-asset smoke tests and `git diff --check` passed. Desktop (1280 px), tablet (900 px) and mobile (390 px) screenshots were inspected. Only upstream deprecation warnings were reported by backend checks.
