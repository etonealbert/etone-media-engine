"""
OpenClaw pipeline integration service.

This module runs the first 3 LLM stages through real OpenClaw agents
(analyst -> scriptwriter -> prompt_engineer) and keeps tool stages as
status-tracked placeholders unless external tool runners are attached.
"""

import asyncio
import json
import logging
import os
import re
import shlex
import subprocess
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
    """
    Extract first valid JSON object from model output.
    Handles cases where model wraps JSON in prose/code fences.
    """
    text = text.strip()

    # Fast path
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    # Strip fenced blocks
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))

    # Brute-force first balanced object
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
    """
    Run one OpenClaw agent turn via CLI and return assistant text.
    Requires local gateway running.
    """
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
        # Fallback: return raw; caller will attempt JSON extraction
        return out

    # Best-effort output extraction across possible CLI payload shapes
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

    # Last resort
    return out


def _build_pipeline_payload(task_data: dict[str, Any]) -> dict[str, Any]:
    target_length = int(task_data.get("target_length_seconds", 15))
    num_segments = max(1, (target_length + 14) // 15)

    return {
        "job_id": str(uuid.uuid4()),
        "created_at": _now_iso(),
        "created_by": "telegram_bot",
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
    else:
        spec = (
            "You are prompt_engineer. Produce JSON: {segments:[...]} where each segment has segment_id,kling_prompt,"
            "elements_config,generation_params. kling_prompt.prompt_structure_verified must be exactly"
            " 'Scene:... -> Character:... -> Action:... -> Camera:... -> Style:...'."
        )

    return "\n".join([spec, *base])


async def run_openclaw_agents(task_data: dict, status_message: Message, bot: Bot) -> Path | None:
    """
    Execute orchestration pipeline connected to OpenClaw agents.

    LLM stages are real OpenClaw calls; video/tool stages currently represented
    as status placeholders unless external tool runners are wired.
    """
    payload = _build_pipeline_payload(task_data)
    job_id = payload["job_id"]
    session_id = f"video-pipeline:{job_id}"

    job_dir = _jobs_dir() / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    job_path = job_dir / "job_payload.json"
    analyst_path = job_dir / "analyst_output.json"
    script_path = job_dir / "scriptwriter_output.json"
    prompt_path = job_dir / "prompt_engineer_output.json"

    job_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    try:
        # Stage 1: Analyst
        await status_message.edit_text("🎬 <b>Pipeline</b>\n\n🔍 Analyst running...")
        analyst_raw = await _run_openclaw_agent(
            "analyst",
            session_id,
            _stage_prompt("analyst", job_path=job_path, in_path=None, out_path=analyst_path),
        )
        analyst_json = _extract_first_json_object(analyst_raw)
        analyst_path.write_text(json.dumps(analyst_json, indent=2, ensure_ascii=False))

        # Stage 2: Scriptwriter
        await status_message.edit_text("🎬 <b>Pipeline</b>\n\n✍️ Scriptwriter running...")
        script_raw = await _run_openclaw_agent(
            "scriptwriter",
            session_id,
            _stage_prompt("scriptwriter", job_path=job_path, in_path=analyst_path, out_path=script_path),
        )
        script_json = _extract_first_json_object(script_raw)
        script_path.write_text(json.dumps(script_json, indent=2, ensure_ascii=False))

        # Stage 3: Prompt Engineer
        await status_message.edit_text("🎬 <b>Pipeline</b>\n\n🎨 Prompt Engineer running...")
        prompt_raw = await _run_openclaw_agent(
            "prompt_engineer",
            session_id,
            _stage_prompt("prompt_engineer", job_path=job_path, in_path=script_path, out_path=prompt_path),
        )
        prompt_json = _extract_first_json_object(prompt_raw)
        prompt_path.write_text(json.dumps(prompt_json, indent=2, ensure_ascii=False))

        # Tool stages: connected as placeholders until wrappers are bound
        await status_message.edit_text(
            "🎬 <b>Pipeline</b>\n\n📹 Video Generator stage prepared (Kling wrapper pending)..."
        )
        await asyncio.sleep(1)
        await status_message.edit_text(
            "🎬 <b>Pipeline</b>\n\n🎞️ Assembly Manager stage prepared (ffmpeg wrapper pending)..."
        )
        await asyncio.sleep(1)

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
        return None

    # Existing delivery behavior: latest output video if present
    video_path = await get_latest_video()

    if video_path:
        try:
            await status_message.edit_text(
                f"✅ <b>Pipeline complete</b>\n\nOutput: <code>{video_path.name}</code>\n"
                f"Job: <code>{job_id}</code>"
            )
        except Exception:
            pass
    else:
        try:
            await status_message.edit_text(
                "✅ <b>LLM stages complete</b>\n\n"
                f"Saved artifacts in <code>{job_dir}</code>\n"
                "No final video found yet (tool stages are scaffolded)."
            )
        except Exception:
            pass

    return video_path
