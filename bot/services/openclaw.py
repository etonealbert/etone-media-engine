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


async def _run_openclaw_agent(agent_id: str, session_id: str, prompt: str, timeout: int = 600) -> str:
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


async def _move_planka_card_to_ready_for_kling(job_id: str, approved_payload: dict[str, Any]) -> None:
    if not settings.planka_board_id or not settings.planka_ready_for_kling_list_id:
        return

    # card id can be provided by orchestrator callback data later; for now use explicit env mapping
    if not settings.planka_card_id_for_testing:
        return

    card_id = settings.planka_card_id_for_testing

    description = (
        f"HITL handoff for job {job_id}\n\n"
        "Approved generation input (prompt_engineer):\n"
        f"```json\n{json.dumps(approved_payload, ensure_ascii=False, indent=2)}\n```"
    )

    def _sync_calls() -> None:
        try:
            _planka_request(
                f"/api/cards/{card_id}",
                method="PATCH",
                body={"listId": settings.planka_ready_for_kling_list_id, "description": description},
            )
        except urllib.error.HTTPError as e:
            logger.warning("Planka routing failed for job %s: %s", job_id, e)

    await asyncio.to_thread(_sync_calls)


async def run_openclaw_agents(
    task_data: dict,
    status_message: Message,
    bot: Bot,
    generation_mode: str = "hitl",
) -> dict[str, Any]:
    payload = _build_pipeline_payload({**task_data, "generation_mode": generation_mode})
    job_id = payload["job_id"]
    session_id = f"video-pipeline:{job_id}"

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
        _find_pipeline_stage(payload, "analyst")["status"] = "done"

        await status_message.edit_text("🎬 <b>Pipeline</b>\n\n✍️ Scriptwriter running...")
        script_raw = await _run_openclaw_agent(
            "scriptwriter",
            session_id,
            _stage_prompt("scriptwriter", job_path=job_path, in_path=analyst_path, out_path=script_path),
        )
        script_json = _extract_first_json_object(script_raw)
        script_path.write_text(json.dumps(script_json, indent=2, ensure_ascii=False))
        _find_pipeline_stage(payload, "scriptwriter")["status"] = "done"

        await status_message.edit_text("🎬 <b>Pipeline</b>\n\n🎨 Prompt Engineer running...")
        prompt_raw = await _run_openclaw_agent(
            "prompt_engineer",
            session_id,
            _stage_prompt("prompt_engineer", job_path=job_path, in_path=script_path, out_path=prompt_path),
        )
        prompt_json = _extract_first_json_object(prompt_raw)
        prompt_path.write_text(json.dumps(prompt_json, indent=2, ensure_ascii=False))
        _find_pipeline_stage(payload, "prompt_engineer")["status"] = "done"

        if generation_mode == "hitl":
            _find_pipeline_stage(payload, "video_generator")["status"] = "bypassed_hitl"
            _find_pipeline_stage(payload, "assembly_manager")["status"] = "waiting_human_input"

            handoff = {
                "job_id": job_id,
                "status": "waiting_for_human_kling_output",
                "ready_for_kling_column": "08 Ready for Kling (Albert)",
                "approved_generation_input": prompt_json,
                "task_summary": payload.get("task", {}),
                "created_at": _now_iso(),
            }
            hitl_path.write_text(json.dumps(handoff, indent=2, ensure_ascii=False))
            job_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

            await _move_planka_card_to_ready_for_kling(job_id, prompt_json)

            await status_message.edit_text(
                "⏸️ <b>Pipeline paused for HITL</b>\n\n"
                f"Job: <code>{job_id}</code>\n"
                "Prompt package is ready.\n"
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
    session_id = f"video-pipeline:{job_id}:resume"
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
    final_from_agent = assembly_json.get("final_video_path") if isinstance(assembly_json, dict) else None
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
