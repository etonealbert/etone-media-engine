# OpenClaw Integration (Telegram Orchestration Bot)

This doc explains how `etone-media-engine` orchestrates subagents with a Human-in-the-Loop (HITL) generation stage.

## Connected Agents

Configured in OpenClaw runtime:
- `analyst`
- `scriptwriter`
- `prompt_engineer`
- `video_generator` (currently bypassed in HITL mode)
- `assembly_manager`
- orchestrator: `main`

## Current `/generator` flow (HITL)

1. Telegram wizard collects assets + task + length + context.
2. User selects generation mode:
   - **Fully Autonomous** → visible but locked (coming soon)
   - **Human-in-the-Loop (HITL)** → active
3. Bot runs subagents sequentially:
   - `analyst`
   - `scriptwriter`
   - `prompt_engineer`
4. Bot pauses pipeline and creates HITL handoff artifacts.
5. Bot routes Planka card to **"08 Ready for Kling (Albert)"** (if Planka env is configured).
6. Albert generates video manually in Kling and sends file/link back in Telegram.
7. Bot resumes pipeline with `assembly_manager` using Albert’s provided input.

## Runtime artifact layout

For each job (`data/jobs/<job_id>/`):
- `job_payload.json`
- `analyst_output.json`
- `scriptwriter_output.json`
- `prompt_engineer_output.json`
- `hitl_handoff.json` (approved generation payload + waiting state)
- `human_kling_output.json` (file path or URL provided by Albert)
- `assembly_output.json`
- `error.txt` (if failed)

## Subagent integration details

Primary integration file: `bot/services/openclaw.py`

- `run_openclaw_agents(...)`:
  - runs first 3 LLM subagents
  - analyst stage also consumes optional strategic brief at `docs/context/analyst_market_spec_2026-02-22.txt` when present
  - marks `video_generator` as `bypassed_hitl`
  - sets `assembly_manager` to `waiting_human_input`
  - writes handoff payload and returns `waiting_human`

- `resume_pipeline_with_human_output(...)`:
  - accepts manual Kling file path or URL
  - writes `human_kling_output.json`
  - calls `assembly_manager` subagent
  - resolves final output path (agent-provided path or fallback latest output)

## Planka routing configuration (optional)

Set these in `.env` to enable routing at HITL handoff:

```env
PLANKA_BASE_URL=...
PLANKA_TOKEN=...
PLANKA_AUTH_HEADER=Authorization
PLANKA_AUTH_PREFIX=Bearer
PLANKA_BOARD_ID=...
PLANKA_READY_FOR_KLING_LIST_ID=...

# temporary/testing mapping used by current implementation
PLANKA_CARD_ID_FOR_TESTING=...
```

If Planka env is not set, bot continues normally and only performs Telegram-based HITL handoff.

## Prerequisites

- OpenClaw gateway running locally.
- Agents present in `~/.openclaw/openclaw.json`.
- Telegram bot process running from repo root.

## Quick verification

1. Start bot:
```bash
uv run python -m bot
```

2. In Telegram chat:
- run `/generator`
- complete wizard
- choose HITL mode
- launch
- upload generated Kling video (or send URL)

3. Verify job artifacts under `data/jobs/<job_id>/`.

## Troubleshooting

### `openclaw agent failed`
- Ensure gateway is up: `openclaw gateway status`
- Ensure agent exists and can run in CLI

### Invalid JSON from stage
- Check `error.txt` and stage output files
- Stage prompts enforce JSON-only outputs

### No final video delivered
- Check `assembly_output.json`
- Verify `final_video_path` exists or fallback output is produced
