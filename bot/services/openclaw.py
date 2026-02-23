"""
OpenClaw pipeline integration service.

This module runs the first 3 LLM stages through real OpenClaw agents
(analyst -> scriptwriter -> prompt_engineer) and supports HITL handoff
between prompt_engineer and assembly_manager.
"""

import asyncio
import json
import logging
import re
import shlex
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.types import Message

from bot.config import settings
from bot.services.file_manager import get_latest_video

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _jobs_dir() -> Path:
    d = settings.base_dir / "data" / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _extract_first_json_object(text: str) -> dict[str, Any]:
    text = text.strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            # OpenClaw sometimes returns wrapper payloads like:
            # {"payloads":[{"text":"{...real json...}"}],"meta":{...}}
            payloads = parsed.get("payloads")
            if isinstance(payloads, list) and payloads:
                first = payloads[0]
                if isinstance(first, dict):
                    payload_text = first.get("text")
                    if isinstance(payload_text, str) and payload_text.strip().startswith("{"):
                        try:
                            inner = json.loads(payload_text)
                            if isinstance(inner, dict):
                                return inner
                        except Exception:
                            pass
            return parsed
    except Exception:
        pass

    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))

    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        obj = json.loads(candidate)
                        if isinstance(obj, dict):
                            return obj
                    except Exception:
                        pass
                    break
        start = text.find("{", start + 1)

    raise ValueError("No valid JSON object found in agent output")


async def _run_openclaw_agent(
    agent_id: str, session_id: str, prompt: str, timeout: int = 600
) -> str:
    cmd = (
        f"openclaw agent --agent {shlex.quote(agent_id)} "
        f"--session-id {shlex.quote(session_id)} "
        f"--message {shlex.quote(prompt)} --json --timeout {int(timeout)}"
    )

    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out_b, err_b = await proc.communicate()
    out = out_b.decode("utf-8", errors="replace")
    err = err_b.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        raise RuntimeError(f"openclaw agent failed ({agent_id}): {err or out}")

    try:
        payload = json.loads(out)
    except Exception:
        return out

    for path in [
        ("result", "output"),
        ("result", "message"),
        ("output",),
        ("message",),
        ("text",),
    ]:
        cur: Any = payload
        ok = True
        for key in path:
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                ok = False
                break
        if ok and isinstance(cur, str) and cur.strip():
            return cur

    return out


def _build_pipeline_payload(task_data: dict[str, Any]) -> dict[str, Any]:
    target_length = int(task_data.get("target_length_seconds", 15))
    num_segments = max(1, (target_length + 14) // 15)

    return {
        "job_id": str(uuid.uuid4()),
        "created_at": _now_iso(),
        "created_by": "telegram_bot",
        "generation_mode": task_data.get("generation_mode", "hitl"),
        "assets": {
            "avatar": {
                "folder": task_data.get("avatar_folder", ""),
                "path": task_data.get("avatar_path", ""),
                "face_reference": f"{task_data.get('avatar_path', '').rstrip('/')}/front_medium.png",
                "portrait_reference": f"{task_data.get('avatar_path', '').rstrip('/')}/portrait/headshot_01.png",
                "character_profile": f"{task_data.get('avatar_path', '').rstrip('/')}/character.json",
                "voice_reference": f"{task_data.get('avatar_path', '').rstrip('/')}/voice/voice_ref.wav",
                "voice_profile": f"{task_data.get('avatar_path', '').rstrip('/')}/voice/voice_profile.json",
            },
            "location": {
                "folder": task_data.get("location_folder", ""),
                "path": task_data.get("location_path", ""),
                "background": f"{task_data.get('location_path', '').rstrip('/')}/medium_shot.png",
            },
            "product": {
                "folder": task_data.get("product_folder", ""),
                "path": task_data.get("product_path", ""),
                "hero_image": f"{task_data.get('product_path', '').rstrip('/')}/product_front.png",
            },
        },
        "task": {
            "description": task_data.get("task_description", ""),
            "target_length_seconds": target_length,
            "num_segments": num_segments,
        },
        "kling_config": {
            "use_elements_locking": True,
            "prompt_structure": "Scene -> Character -> Action -> Camera -> Style",
            "max_segment_seconds": 15,
            "chain_segments": target_length > 15,
            "audio_source": "voiceovers",
            "aspect_ratio": "9:16",
            "resolution": "1080x1920",
        },
        "pipeline": [
            {"agent": "analyst", "status": "pending"},
            {"agent": "scriptwriter", "status": "pending"},
            {"agent": "prompt_engineer", "status": "pending"},
            {"agent": "video_generator", "status": "pending"},
            {"agent": "assembly_manager", "status": "pending"},
        ],
    }


def _stage_prompt(stage: str, job_path: Path, in_path: Path | None, out_path: Path) -> str:
    base = [
        f"Read job payload at: {job_path}",
        "Return JSON only. No markdown. No prose.",
        f"Write final JSON to: {out_path}",
    ]

    analyst_brief = settings.base_dir / "docs" / "context" / "analyst_market_spec_2026-02-22.txt"
    if stage == "analyst" and analyst_brief.exists():
        base.insert(1, f"Read additional strategic brief at: {analyst_brief}")
    if in_path:
        base.insert(1, f"Read previous stage output at: {in_path}")

    if stage == "analyst":
        spec = (
            "You are analyst. Produce JSON with keys: product_name,target_audience,pain_points,key_hooks,"
            "competitive_angle,cta_suggestion. pain_points 3-5 items."
        )
    elif stage == "scriptwriter":
        spec = (
            "You are scriptwriter. Produce timed UGC JSON with keys: title,total_duration_seconds,segments,"
            "voiceover_instructions. Each segment <=15 seconds and has segment_id,duration_seconds,spoken_text,visual_direction,emotion."
        )
    elif stage == "prompt_engineer":
        spec = (
            "You are prompt_engineer. Produce JSON: {segments:[...]} where each segment has segment_id,kling_prompt,"
            "elements_config,generation_params. kling_prompt.prompt_structure_verified must be exactly"
            " 'Scene:... -> Character:... -> Action:... -> Camera:... -> Style:...'."
        )
    else:
        spec = (
            "You are assembly_manager. Produce JSON with keys: final_video_path,steps_executed,"
            "audio_mix_notes,status. Use supplied human Kling output as source footage."
        )

    return "\n".join([spec, *base])


def _find_pipeline_stage(payload: dict[str, Any], agent: str) -> dict[str, Any] | None:
    for stage in payload.get("pipeline", []):
        if stage.get("agent") == agent:
            return stage
    return None


def _set_pipeline_status(payload: dict[str, Any], agent: str, status: str) -> None:
    """Set the status of a pipeline stage, silently skipping if not found."""
    stage = _find_pipeline_stage(payload, agent)
    if stage is not None:
        stage["status"] = status


def _planka_request(path: str, method: str = "GET", body: dict[str, Any] | None = None) -> Any:
    if not settings.planka_base_url or not settings.planka_token:
        return None

    url = f"{settings.planka_base_url.rstrip('/')}{path}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    auth_prefix = f"{settings.planka_auth_prefix} " if settings.planka_auth_prefix else ""
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            settings.planka_auth_header: f"{auth_prefix}{settings.planka_token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # nosec - controlled URL from env
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else None


def _resolve_hitl_asset_files(assets: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Resolve concrete asset file refs into existing and missing file lists."""
    refs: list[str] = []

    avatar = assets.get("avatar", {})
    location = assets.get("location", {})
    product = assets.get("product", {})

    if isinstance(avatar, dict):
        refs.extend(
            [
                avatar.get("face_reference", ""),
                avatar.get("portrait_reference", ""),
                avatar.get("character_profile", ""),
                avatar.get("voice_reference", ""),
                avatar.get("voice_profile", ""),
            ]
        )
    if isinstance(location, dict):
        refs.append(location.get("background", ""))
    if isinstance(product, dict):
        refs.append(product.get("hero_image", ""))

    existing: list[str] = []
    missing: list[str] = []

    for ref in refs:
        if not isinstance(ref, str) or not ref.strip():
            continue
        path = Path(ref.strip())
        if not path.is_absolute():
            path = settings.base_dir / path
        resolved = str(path)
        if path.exists():
            existing.append(resolved)
        else:
            missing.append(resolved)

    return existing, missing


def _build_planka_card_description(
    job_id: str,
    approved_payload: dict[str, Any],
    assets: dict[str, Any],
) -> str:
    """Build a concise Planka card body for HITL handoff."""
    # Extract concise prompt summaries from approved payload
    prompt_lines: list[str] = []
    segments = approved_payload.get("segments", [])
    for seg in segments[:6]:  # cap at 6 to keep card readable
        kling = seg.get("kling_prompt", {})
        prompt_text = (
            kling if isinstance(kling, str) else kling.get("prompt_structure_verified", "")
        )
        if prompt_text:
            sid = seg.get("segment_id", "?")
            prompt_lines.append(f"  Seg {sid}: {prompt_text[:200]}")
    prompt_block = (
        "\n".join(prompt_lines)
        if prompt_lines
        else json.dumps(approved_payload, indent=2, ensure_ascii=False)[:600]
    )

    # Asset input paths
    avatar_path = assets.get("avatar", {}).get("path", "n/a")
    avatar_face = assets.get("avatar", {}).get("face_reference", "")
    avatar_portrait = assets.get("avatar", {}).get("portrait_reference", "")
    avatar_character = assets.get("avatar", {}).get("character_profile", "")
    avatar_voice = assets.get("avatar", {}).get("voice_reference", "")
    avatar_voice_profile = assets.get("avatar", {}).get("voice_profile", "")
    location_path = assets.get("location", {}).get("path", "n/a")
    location_bg = assets.get("location", {}).get("background", "")
    product_path = assets.get("product", {}).get("path", "n/a")
    product_hero = assets.get("product", {}).get("hero_image", "")
    asset_files, missing_files = _resolve_hitl_asset_files(assets)
    asset_files_block = "\n".join(f"- `{p}`" for p in asset_files) or "- (none)"
    missing_files_block = "\n".join(f"- `{p}`" for p in missing_files) or "- (none)"

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        f"## HITL Kling Handoff — {today}\n\n"
        f"**Job ID:** `{job_id}`\n\n"
        f"### Approved Prompts\n{prompt_block}\n\n"
        f"### Asset Inputs\n"
        f"- Avatar folder: `{avatar_path}`\n"
        f"- Avatar face ref: `{avatar_face}`\n"
        f"- Avatar portrait ref: `{avatar_portrait}`\n"
        f"- Avatar character profile: `{avatar_character}`\n"
        f"- Avatar voice ref: `{avatar_voice}`\n"
        f"- Avatar voice profile: `{avatar_voice_profile}`\n"
        f"- Location folder: `{location_path}`\n"
        f"- Location background: `{location_bg}`\n"
        f"- Product folder: `{product_path}`\n"
        f"- Product hero image: `{product_hero}`\n\n"
        f"### Resolved Asset Files\n{asset_files_block}\n\n"
        f"### Missing Asset Files\n{missing_files_block}\n\n"
        f"### Instructions for Albert\n"
        f"1. Use the prompts above to generate video segments in **Kling 3.0**.\n"
        f"2. Use avatar image refs + portrait + character + voice files to keep identity/voice consistent.\n"
        f"3. Each segment ≤ 15 s; chain if needed.\n"
        f"4. When done, return output in Telegram bot chat for job `{job_id}` by either:\n"
        f"   - uploading the generated video file, or\n"
        f"   - sending a direct downloadable URL.\n"
    )


def _planka_create_card(name: str, list_id: str, description: str) -> Any:
    """Try common Planka API endpoint patterns to create a card."""
    body = {"name": name, "listId": list_id, "description": description}
    # Planka >=1.x nests under /api/cards; older versions use /api/lists/:id/cards
    endpoints = [
        ("/api/cards", "POST"),
        (f"/api/lists/{list_id}/cards", "POST"),
    ]
    last_err: Exception | None = None
    for path, method in endpoints:
        try:
            result = _planka_request(path, method=method, body=body)
            return result
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            last_err = exc
            logger.debug("Planka create-card endpoint %s failed: %s", path, exc)
            continue
    raise RuntimeError(f"All Planka create-card endpoints failed: {last_err}")


async def _move_planka_card_to_ready_for_kling(
    job_id: str,
    approved_payload: dict[str, Any],
    assets: dict[str, Any],
) -> str:
    """Route a HITL card to the 'Ready for Kling' list in Planka.

    If PLANKA_CARD_ID_FOR_TESTING is set, update that card (move + description).
    Otherwise create a new card in PLANKA_READY_FOR_KLING_LIST_ID.
    Never raises — logs warnings on failure so the pipeline continues.
    """
    if not settings.planka_base_url or not settings.planka_token:
        logger.info(
            "Planka not configured (no base_url/token) — skipping HITL card routing for job %s",
            job_id,
        )
        return "planka: skipped missing config"
    if not settings.planka_ready_for_kling_list_id:
        logger.info(
            "PLANKA_READY_FOR_KLING_LIST_ID not set — skipping card routing for job %s", job_id
        )
        return "planka: skipped missing list id"

    list_id = settings.planka_ready_for_kling_list_id  # narrowed to str above
    try:
        description = _build_planka_card_description(job_id, approved_payload, assets)
    except Exception as exc:
        logger.warning("Planka card description build failed for job %s: %s", job_id, exc)
        return "planka: failed description"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _sync_calls() -> str:
        card_id = settings.planka_card_id_for_testing
        if card_id:
            # Update existing card: move to target list and set description
            try:
                _planka_request(
                    f"/api/cards/{card_id}",
                    method="PATCH",
                    body={
                        "listId": list_id,
                        "description": description,
                    },
                )
                logger.info("Planka HITL handoff OK — updated card %s for job %s", card_id, job_id)
                return "planka: updated card"
            except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
                logger.warning(
                    "Planka card update failed for job %s (card %s): %s", job_id, card_id, exc
                )
                return "planka: failed update"
        else:
            # Create a new card in the target list
            card_name = f"Kling HITL — {today} — {job_id[:8]}"
            try:
                result = _planka_create_card(
                    name=card_name,
                    list_id=list_id,
                    description=description,
                )
                new_id = ""
                if isinstance(result, dict):
                    new_id = result.get("item", {}).get("id", "") or result.get("id", "")
                logger.info(
                    "Planka HITL handoff OK — created card %s for job %s",
                    new_id or "(unknown id)",
                    job_id,
                )
                return "planka: created card"
            except Exception as exc:
                logger.warning("Planka card creation failed for job %s: %s", job_id, exc)
                return "planka: failed create"

    try:
        return await asyncio.to_thread(_sync_calls)
    except Exception as exc:
        # Absolute safety net — never crash the pipeline over Planka
        logger.error("Unexpected Planka error for job %s: %s", job_id, exc, exc_info=True)
        return "planka: failed unexpected"


async def run_openclaw_agents(
    task_data: dict,
    status_message: Message,
    bot: Bot,
    generation_mode: str = "hitl",
) -> dict[str, Any]:
    payload = _build_pipeline_payload({**task_data, "generation_mode": generation_mode})
    job_id = payload["job_id"]
    session_id = f"video-pipeline_{job_id}"

    job_dir = _jobs_dir() / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    job_path = job_dir / "job_payload.json"
    analyst_path = job_dir / "analyst_output.json"
    script_path = job_dir / "scriptwriter_output.json"
    prompt_path = job_dir / "prompt_engineer_output.json"
    hitl_path = job_dir / "hitl_handoff.json"

    job_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    try:
        await status_message.edit_text("🎬 <b>Pipeline</b>\n\n🔍 Analyst running...")
        analyst_raw = await _run_openclaw_agent(
            "analyst",
            session_id,
            _stage_prompt("analyst", job_path=job_path, in_path=None, out_path=analyst_path),
        )
        analyst_json = _extract_first_json_object(analyst_raw)
        analyst_path.write_text(json.dumps(analyst_json, indent=2, ensure_ascii=False))
        _set_pipeline_status(payload, "analyst", "done")

        await status_message.edit_text("🎬 <b>Pipeline</b>\n\n✍️ Scriptwriter running...")
        script_raw = await _run_openclaw_agent(
            "scriptwriter",
            session_id,
            _stage_prompt(
                "scriptwriter", job_path=job_path, in_path=analyst_path, out_path=script_path
            ),
        )
        script_json = _extract_first_json_object(script_raw)
        script_path.write_text(json.dumps(script_json, indent=2, ensure_ascii=False))
        _set_pipeline_status(payload, "scriptwriter", "done")

        await status_message.edit_text("🎬 <b>Pipeline</b>\n\n🎨 Prompt Engineer running...")
        prompt_raw = await _run_openclaw_agent(
            "prompt_engineer",
            session_id,
            _stage_prompt(
                "prompt_engineer", job_path=job_path, in_path=script_path, out_path=prompt_path
            ),
        )
        prompt_json = _extract_first_json_object(prompt_raw)
        prompt_path.write_text(json.dumps(prompt_json, indent=2, ensure_ascii=False))
        _set_pipeline_status(payload, "prompt_engineer", "done")

        if generation_mode == "hitl":
            _set_pipeline_status(payload, "video_generator", "bypassed_hitl")
            _set_pipeline_status(payload, "assembly_manager", "waiting_human_input")
            assets = payload.get("assets", {})
            asset_files, missing_files = _resolve_hitl_asset_files(assets)

            planka_status = await _move_planka_card_to_ready_for_kling(job_id, prompt_json, assets)

            handoff = {
                "job_id": job_id,
                "status": "waiting_for_human_kling_output",
                "ready_for_kling_column": "08 Ready for Kling (Albert)",
                "approved_generation_input": prompt_json,
                "asset_files": asset_files,
                "missing_files": missing_files,
                "planka_status": planka_status,
                "task_summary": payload.get("task", {}),
                "created_at": _now_iso(),
            }
            hitl_path.write_text(json.dumps(handoff, indent=2, ensure_ascii=False))
            job_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

            segments = prompt_json.get("segments", []) if isinstance(prompt_json, dict) else []
            await status_message.edit_text(
                "⏸️ <b>Pipeline paused for HITL</b>\n\n"
                f"Job: <code>{job_id}</code>\n"
                f"Prompt package ready: <code>{len(segments)}</code> segment(s).\n"
                f"Planka: <code>{planka_status}</code>\n"
                f"Handoff file: <code>{hitl_path}</code>\n"
                "Please generate video manually in Kling and upload the result here."
            )
            return {
                "status": "waiting_human",
                "job_id": job_id,
                "job_dir": str(job_dir),
                "hitl_handoff_path": str(hitl_path),
                "approved_generation_input": prompt_json,
            }

        await status_message.edit_text("🚧 Autonomous mode is currently locked.")
        return {"status": "locked", "job_id": job_id, "job_dir": str(job_dir)}

    except Exception as e:
        logger.exception("OpenClaw pipeline failed")
        (job_dir / "error.txt").write_text(str(e))
        try:
            await status_message.edit_text(
                f"❌ <b>Pipeline failed</b>\n\n<code>{str(e)[:1200]}</code>\n\n"
                f"Job folder: <code>{job_dir}</code>"
            )
        except Exception:
            pass
        return {"status": "failed", "error": str(e), "job_id": job_id, "job_dir": str(job_dir)}


async def resume_pipeline_with_human_output(
    job_id: str,
    human_video_path: str | None,
    human_video_url: str | None,
    status_message: Message,
) -> Path | None:
    job_dir = _jobs_dir() / job_id
    job_path = job_dir / "job_payload.json"
    prompt_path = job_dir / "prompt_engineer_output.json"
    assembly_out = job_dir / "assembly_output.json"
    human_input_manifest = job_dir / "human_kling_output.json"

    if not job_path.exists() or not prompt_path.exists():
        raise RuntimeError(f"Job artifacts missing for {job_id}")

    payload = json.loads(job_path.read_text())
    prompt_json = json.loads(prompt_path.read_text())

    human_input_manifest.write_text(
        json.dumps(
            {
                "job_id": job_id,
                "submitted_at": _now_iso(),
                "video_path": human_video_path,
                "video_url": human_video_url,
            },
            indent=2,
            ensure_ascii=False,
        )
    )

    await status_message.edit_text("🎞️ <b>Assembly Manager running...</b>")

    stage_prompt = (
        _stage_prompt("assembly_manager", job_path, prompt_path, assembly_out)
        + "\n"
        + f"Read human Kling output manifest at: {human_input_manifest}\n"
        + "Treat video_path or video_url as the source generated footage and continue pipeline."
    )
    session_id = f"video-pipeline_{job_id}_resume"
    assembly_raw = await _run_openclaw_agent("assembly_manager", session_id, stage_prompt)
    try:
        assembly_json = _extract_first_json_object(assembly_raw)
    except Exception:
        assembly_json = {"raw": assembly_raw}
    assembly_out.write_text(json.dumps(assembly_json, indent=2, ensure_ascii=False))

    stage = _find_pipeline_stage(payload, "assembly_manager")
    if stage:
        stage["status"] = "done"
    payload["status"] = "completed"
    job_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    video_path = None
    final_from_agent = (
        assembly_json.get("final_video_path") if isinstance(assembly_json, dict) else None
    )
    if isinstance(final_from_agent, str) and final_from_agent.strip():
        candidate = Path(final_from_agent.strip())
        if not candidate.is_absolute():
            candidate = settings.base_dir / candidate
        if candidate.exists():
            video_path = candidate

    if video_path is None:
        video_path = await get_latest_video()

    if video_path:
        await status_message.edit_text(
            f"✅ <b>Pipeline complete</b>\n\nOutput: <code>{video_path.name}</code>\nJob: <code>{job_id}</code>"
        )
    else:
        await status_message.edit_text(
            "✅ <b>Assembly stage completed</b>\n\n"
            "No final MP4 found yet. Check job artifacts for details."
        )

    return video_path
