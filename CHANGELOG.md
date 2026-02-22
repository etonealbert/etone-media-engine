# Changelog

All notable changes to etone-media-engine are documented here.

## [0.2.0] - 2026-02-22

### Changed

- **All /generate wizard steps are now optional.** Every step (avatar, location, product, task, length) can be skipped with a "Skip" button. The bot will launch agents with whatever data was provided — even with nothing selected at all.
- Wizard step count changed from 5 to 6. Step numbering updated throughout the UI.
- Summary screen now shows "skipped" for steps that were not filled, instead of requiring all fields.
- Length step defaults to 15s when skipped.
- Progress header in each step shows completed selections with checkmarks and skipped steps with a skip icon.

### Added

- **Step 6: Creative Context.** New optional free-text step where the user can describe creative wishes for the video — specific dances, movements, sound effects, music style, mood, visual effects, or CTA preferences. This context is passed to the agent pipeline as `creative_context` in the task payload.
- `SkipCB` callback factory (`prefix="sk"`) for skip buttons across all wizard steps.
- `GenerateStates.enter_context` FSM state for the new creative context step.
- Skip buttons added to asset list keyboards, length picker, task input, and context input.
- `_build_progress()` helper that renders a dynamic progress header showing completed vs skipped steps.
- `_show_confirm()` helper that builds the summary from whatever data was collected.
- Transition helpers (`_go_to_step_2` through `_go_to_step_6`) for clean step routing from both selection and skip paths.

### Fixed

- Bot no longer blocks generation when the asset library is empty. Previously, the wizard could not proceed past step 1 without indexed avatars. Now every step can be skipped.

## [0.1.0] - 2026-02-22

### Added

- Initial release of the Telegram bot (`@etoneMediaEngine_bot`).
- Security: `AdminOnlyMiddleware` blocks all non-admin users globally.
- Asset management: file upload with category picker, subfolder routing, timestamp naming.
- `/index` command: scans asset directories and rebuilds SQLite index.
- `/create_asset` command: creates structured asset folders with placeholder files.
- `/generate` wizard: 5-step FSM for selecting avatar, location, product, task, and video length.
- OpenClaw agent mock with Kling 3.0 stubs and real-time status streaming.
- Video delivery via `FSInputFile` from `output/videos/`.
- SQLite asset index with `aiosqlite` for fast keyboard queries.
- CallbackData factories with short prefixes (all under 64 bytes).
- Paginated inline keyboards for asset browsing.
- Planka sync worker for Kanban board notifications.
- `.projectrules` for LLM-assisted development context.
- `docs/AGENTS.md` with full 5-agent pipeline specification.
