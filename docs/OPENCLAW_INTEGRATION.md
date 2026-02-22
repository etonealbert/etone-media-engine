# OpenClaw Integration (Telegram Orchestration Bot)

This doc explains how the `etone-media-engine` bot is connected to your OpenClaw agents.

## Connected Agents

Configured in your OpenClaw runtime:
- `analyst` (model: `google-antigravity/claude-opus-4-6-thinking`)
- `scriptwriter` (model: `google-antigravity/claude-opus-4-6-thinking`)
- `prompt_engineer` (model: `google-antigravity/claude-sonnet-4-6`)
- `video_generator`
- `assembly_manager`
- orchestrator agent: `main`

## How the bot calls agents

File: `bot/services/openclaw.py`

At `/generate` launch, the bot:
1. Builds canonical job payload JSON
2. Writes artifacts to `data/jobs/<job_id>/`
3. Calls OpenClaw CLI sequentially:
   - `openclaw agent --agent analyst ...`
   - `openclaw agent --agent scriptwriter ...`
   - `openclaw agent --agent prompt_engineer ...`
4. Parses JSON output from each stage and writes:
   - `analyst_output.json`
   - `scriptwriter_output.json`
   - `prompt_engineer_output.json`

## Runtime artifact layout

- `data/jobs/<job_id>/job_payload.json`
- `data/jobs/<job_id>/analyst_output.json`
- `data/jobs/<job_id>/scriptwriter_output.json`
- `data/jobs/<job_id>/prompt_engineer_output.json`
- `data/jobs/<job_id>/error.txt` (if failed)

## Current status of tool stages

- `video_generator` and `assembly_manager` are represented in bot status flow,
  but actual Kling/ffmpeg execution is still scaffolded in this repo.
- Existing delivery behavior still checks `output/videos/` for latest `.mp4`.

## Prerequisites

- OpenClaw gateway running locally.
- Agents present in `~/.openclaw/openclaw.json`.
- Telegram bot process running from repo root.

## Quick verification

1. Start bot:
```bash
uv run python -m bot
```

2. In Telegram bot chat:
- run `/generate`
- select avatar/location/product
- provide task and length
- launch

3. Verify artifacts appear under `data/jobs/<job_id>/`.

## Troubleshooting

### "openclaw agent failed"
- Ensure gateway is up: `openclaw gateway status`
- Ensure agent exists: `openclaw agents list --bindings`

### Invalid JSON from stage
- Check stage output in logs and `error.txt`
- Re-run pipeline; prompts enforce JSON-only output

### No final video delivered
- This is expected until Kling + assembly runners are fully wired.
- Put test `.mp4` in `output/videos/` to verify send flow.

## Next wiring step (recommended)

Replace tool-stage placeholders with real calls:
- `integrations/kling_client.py` for generation/lip-sync/download
- `integrations/ffmpeg_assembly.py` for concat/mix/export

Then let bot use generated final path from manifest instead of `get_latest_video()` fallback.
