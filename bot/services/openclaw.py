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
import shutil
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


def _safe_write_text(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to write text file %s: %s", path, exc)


def _safe_write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to write json file %s: %s", path, exc)


def _trace_stage_start(trace_path: Path, stage: str) -> None:
    trace = {"updated_at": _now_iso(), "stages": {}}
    try:
        if trace_path.exists():
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            if not isinstance(trace, dict):
                trace = {"updated_at": _now_iso(), "stages": {}}
    except Exception:
        trace = {"updated_at": _now_iso(), "stages": {}}

    stages = trace.setdefault("stages", {})
    stage_obj = stages.setdefault(stage, {})
    stage_obj["started_at"] = _now_iso()
    stage_obj["status"] = "running"
    stage_obj.pop("error", None)
    trace["updated_at"] = _now_iso()
    _safe_write_json(trace_path, trace)


def _trace_stage_end(trace_path: Path, stage: str, status: str, error: str | None = None) -> None:
    trace = {"updated_at": _now_iso(), "stages": {}}
    try:
        if trace_path.exists():
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            if not isinstance(trace, dict):
                trace = {"updated_at": _now_iso(), "stages": {}}
    except Exception:
        trace = {"updated_at": _now_iso(), "stages": {}}

    stages = trace.setdefault("stages", {})
    stage_obj = stages.setdefault(stage, {})
    if "started_at" not in stage_obj:
        stage_obj["started_at"] = _now_iso()
    stage_obj["ended_at"] = _now_iso()
    stage_obj["status"] = status
    if error:
        stage_obj["error"] = error[:1000]
    trace["updated_at"] = _now_iso()
    _safe_write_json(trace_path, trace)


def _write_stage_logs(
    stage_logs_dir: Path, stage: str, raw_text: str, parsed: dict[str, Any] | None
) -> None:
    _safe_write_text(stage_logs_dir / f"{stage}.raw.txt", raw_text)
    if parsed is not None:
        _safe_write_json(stage_logs_dir / f"{stage}.parsed.json", parsed)


def _ensure_multishot_prompt_package(prompt_json: dict[str, Any]) -> dict[str, Any]:
    """Normalize prompt_engineer output so each segment has explicit per-shot prompts.

    If a segment has no `shots`, synthesize a single shot from kling prompt text.
    """
    segments = prompt_json.get("segments")
    if not isinstance(segments, list):
        return prompt_json

    for seg in segments:
        if not isinstance(seg, dict):
            continue
        seg_duration = int(seg.get("duration_seconds", 15) or 15)
        shots = seg.get("shots")
        if isinstance(shots, list) and shots:
            # ensure basic timing fields exist
            cursor = 0
            for i, shot in enumerate(shots, start=1):
                if not isinstance(shot, dict):
                    continue
                dur = int(shot.get("duration_seconds", 0) or 0)
                if dur <= 0:
                    dur = 3 if i < len(shots) else max(3, seg_duration - cursor)
                shot["duration_seconds"] = dur
                shot.setdefault("shot_id", i)
                shot.setdefault("start_s", cursor)
                cursor += dur
                shot.setdefault("end_s", cursor)
            continue

        kling = seg.get("kling_prompt", {})
        base_prompt = kling if isinstance(kling, str) else kling.get("prompt_structure_verified", "")
        seg["shots"] = [
            {
                "shot_id": 1,
                "start_s": 0,
                "end_s": seg_duration,
                "duration_seconds": seg_duration,
                "transition": "hard_cut",
                "camera": "medium selfie style",
                "prompt": base_prompt or "Product-focused shot with clear avatar consistency",
            }
        ]
        seg["multi_shot"] = False

    return prompt_json


# Forbidden service/agency language patterns (case-insensitive).
# These indicate agent drift toward promoting a UGC agency/service
# instead of the selected consumer product.
_FORBIDDEN_SERVICE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bstudio\b",
        r"\bwe build creatives\b",
        r"\b(?:CTR|CPA)\b",
        r"\bvariants?\b",
        r"\bcampaign services?\b",
        r"\bagency\b",
        r"\bwe (?:build|create|deliver|offer|provide)\b",
        r"\bour (?:team|service|platform)\b",
        r"\bcreative services?\b",
    ]
]


def _validate_stage_output(stage: str, parsed: dict[str, Any]) -> None:
    """Check stage output for forbidden service/agency language.

    Raises ValueError if forbidden terms are found, so the pipeline
    records the failure in trace and stops before the next stage.
    """
    texts_to_check: list[tuple[str, str]] = []  # (field_label, value)

    if stage == "analyst":
        pn = parsed.get("product_name")
        if isinstance(pn, str):
            texts_to_check.append(("analyst.product_name", pn))
    elif stage == "scriptwriter":
        title = parsed.get("title")
        if isinstance(title, str):
            texts_to_check.append(("scriptwriter.title", title))
        for seg in parsed.get("segments", []):
            if isinstance(seg, dict):
                st = seg.get("spoken_text")
                sid = seg.get("segment_id", "?")
                if isinstance(st, str):
                    texts_to_check.append((f"scriptwriter.segment[{sid}].spoken_text", st))

    violations: list[str] = []
    for label, text in texts_to_check:
        for pat in _FORBIDDEN_SERVICE_PATTERNS:
            match = pat.search(text)
            if match:
                violations.append(f"{label}: matched '{match.group()}' (pattern: {pat.pattern})")

    if violations:
        detail = "; ".join(violations)
        raise ValueError(
            f"Agent drift detected in {stage} output — forbidden service/agency language found: {detail}"
        )


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

    # Optional strategy research context for scriptwriter/prompt_engineer
    if stage in {"scriptwriter", "prompt_engineer"}:
        _research_paths = [
            Path("/home/etonealbert/.openclaw/subagents/Short-Form Content Strategy Framework.txt"),
            settings.base_dir / "docs" / "context" / "short_form_content_strategy_framework.txt",
            Path("/home/etonealbert/Downloads/Kling 3.0 Multishot Ad Guide.txt"),
            settings.base_dir / "docs" / "context" / "kling_3_multishot_ad_guide.txt",
        ]
        for rp in _research_paths:
            if rp.exists():
                base.insert(
                    1,
                    f"Read research context for hooks/retention and multishot prompt quality at: {rp}\n"
                    "Use techniques for structure and retention while keeping ALL content strictly product-centric.",
                )
                break

    if stage == "analyst":
        spec = (
            "You are analyst. Your ONLY job is to analyze the CONSUMER PRODUCT selected by the user "
            "and produce a consumer-facing ad brief for a short-form UGC video ad.\n"
            "Produce JSON with keys: product_name,target_audience,pain_points,key_hooks,"
            "competitive_angle,cta_suggestion. pain_points 3-5 items.\n"
            "CRITICAL CONSTRAINTS:\n"
            "- product_name MUST be the actual consumer product name from the job payload, never an agency or service name.\n"
            "- ALL output must be consumer-facing ad copy perspective (viewer = potential buyer).\n"
            "- NEVER use agency/service language: 'studio', 'we build creatives', 'CTR', 'CPA', "
            "'variants', 'campaign services', 'agency', 'our team', 'creative services'.\n"
            "- You are writing about a PRODUCT the viewer might buy, not a service a brand might hire."
        )
    elif stage == "scriptwriter":
        spec = (
            "You are scriptwriter. Your ONLY job is to write a short-form UGC video script that "
            "advertises the CONSUMER PRODUCT from the analyst output to end consumers.\n"
            "Produce timed UGC JSON with keys: title,total_duration_seconds,segments,"
            "voiceover_instructions. Each segment <=15 seconds and has segment_id,duration_seconds,"
            "spoken_text,visual_direction,emotion.\n"
            "CRITICAL CONSTRAINTS:\n"
            "- The script is a consumer-facing product ad (TikTok/Reels/Shorts style).\n"
            "- spoken_text must sound like a real person recommending the PRODUCT to viewers.\n"
            "- title must reference the product, never an agency or service.\n"
            "- NEVER use agency/service language: 'studio', 'we build creatives', 'CTR', 'CPA', "
            "'variants', 'campaign services', 'agency', 'our team', 'creative services'.\n"
            "- You are writing ad copy for consumers, NOT a pitch deck for brand clients."
        )
    elif stage == "prompt_engineer":
        spec = (
            "You are prompt_engineer. Produce multishot-ready Kling package JSON: {segments:[...]}\n"
            "Each segment must include: segment_id,duration_seconds,multi_shot,shots,kling_prompt,elements_config,generation_params,negative_prompt.\n"
            "Each shot object must include: shot_id,start_s,end_s,duration_seconds,transition,camera,prompt.\n"
            "Constraints: duration_seconds per segment <=15; each shot duration_seconds >=3; shot timings must be sequential and sum to segment duration.\n"
            "Use clean scene separation with explicit transition logic (hard_cut/rack_focus/pan etc) to avoid morphing.\n"
            "kling_prompt.prompt_structure_verified must be exactly 'Scene:... -> Character:... -> Action:... -> Camera:... -> Style:...'.\n"
            "Keep product/brand consistency and avatar consistency references in prompts."
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
    """Create a card in a Planka list with required fields."""

    def _collect_positions(node: Any, out: list[int]) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "position" and isinstance(value, (int, float)):
                    out.append(int(value))
                else:
                    _collect_positions(value, out)
        elif isinstance(node, list):
            for item in node:
                _collect_positions(item, out)

    def _next_position() -> int:
        fallback = 65536
        try:
            cards = _planka_request(f"/api/lists/{list_id}/cards", method="GET")
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            logger.warning(
                "Planka list cards fetch failed for list %s, using fallback position %s: %s",
                list_id,
                fallback,
                exc,
            )
            return fallback

        positions: list[int] = []
        _collect_positions(cards, positions)
        if not positions:
            logger.info(
                "No Planka card positions found for list %s, using fallback position %s",
                list_id,
                fallback,
            )
            return fallback
        return max(positions) + 65536

    body = {
        "name": name,
        "description": description,
        "type": "project",
        "position": _next_position(),
    }
    return _planka_request(f"/api/lists/{list_id}/cards", method="POST", body=body)


def _safe_name_for_export(src: Path) -> str:
    parts = [p for p in src.parts[-3:] if p not in ("", ".", "..")]
    if not parts:
        return "asset"
    return "__".join(parts)


def _export_hitl_input_package(
    package_dir: Path,
    hitl_path: Path,
    prompt_path: Path,
    job_path: Path,
    asset_files: list[str],
) -> tuple[list[str], list[str]]:
    package_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = package_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    for src in (hitl_path, prompt_path, job_path):
        try:
            shutil.copy2(src, package_dir / src.name)
        except Exception as exc:
            logger.warning("Failed to export %s to %s: %s", src, package_dir, exc)

    exported_assets: list[str] = []
    export_errors: list[str] = []
    for asset in asset_files:
        src = Path(asset)
        if not src.exists():
            continue

        base_name = _safe_name_for_export(src)
        dest = assets_dir / base_name
        suffix_idx = 1
        while dest.exists() or dest.is_symlink():
            dest = assets_dir / f"{base_name}_{suffix_idx}"
            suffix_idx += 1

        try:
            dest.symlink_to(src)
        except Exception:
            try:
                shutil.copy2(src, dest)
            except Exception as exc:
                export_errors.append(f"{src} -> {dest}: {exc}")
                logger.warning("Failed to export asset file %s to package: %s", src, exc)
                continue
        exported_assets.append(str(dest))

    readme = package_dir / "README.txt"
    readme.write_text(
        "\n".join(
            [
                "HITL Kling input package",
                "",
                "Files included:",
                "- hitl_handoff.json",
                "- prompt_engineer_output.json",
                "- job_payload.json",
                "- assets/ (symlinks or copies of resolved inputs when available)",
                "",
                "How to provide Kling output:",
                "1) Preferred local flow:",
                "   - Put generated output files into ../output_waiting",
                "2) Telegram flow:",
                "   - Upload the generated file in bot chat, or",
                "   - Send a direct downloadable URL in bot chat.",
            ]
        ),
        encoding="utf-8",
    )

    return exported_assets, export_errors


async def _best_effort_open_folder(path: Path) -> None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "xdg-open",
            str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
        except asyncio.TimeoutError:
            logger.debug("xdg-open timeout for folder %s", path)
    except FileNotFoundError:
        logger.info("xdg-open not available; folder not opened: %s", path)
    except Exception as exc:
        logger.debug("Could not open folder %s with xdg-open: %s", path, exc)


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
    kling_input_package_dir = job_dir / "kling_input_package"
    output_waiting_dir = job_dir / "output_waiting"
    stage_logs_dir = job_dir / "stage_logs"
    trace_path = job_dir / "trace.json"

    _safe_write_json(job_path, payload)
    stage_logs_dir.mkdir(parents=True, exist_ok=True)
    _safe_write_json(trace_path, {"job_id": job_id, "updated_at": _now_iso(), "stages": {}})

    current_stage = "pipeline"
    try:
        await status_message.edit_text("🎬 <b>Pipeline</b>\n\n🔍 Analyst running...")
        current_stage = "analyst"
        _trace_stage_start(trace_path, current_stage)
        analyst_raw = await _run_openclaw_agent(
            "analyst",
            session_id,
            _stage_prompt("analyst", job_path=job_path, in_path=None, out_path=analyst_path),
        )
        analyst_json = _extract_first_json_object(analyst_raw)
        _safe_write_json(analyst_path, analyst_json)
        _write_stage_logs(stage_logs_dir, "analyst", analyst_raw, analyst_json)
        _validate_stage_output("analyst", analyst_json)
        _trace_stage_end(trace_path, current_stage, "done")
        _set_pipeline_status(payload, "analyst", "done")

        await status_message.edit_text("🎬 <b>Pipeline</b>\n\n✍️ Scriptwriter running...")
        current_stage = "scriptwriter"
        _trace_stage_start(trace_path, current_stage)
        script_raw = await _run_openclaw_agent(
            "scriptwriter",
            session_id,
            _stage_prompt(
                "scriptwriter", job_path=job_path, in_path=analyst_path, out_path=script_path
            ),
        )
        script_json = _extract_first_json_object(script_raw)
        _safe_write_json(script_path, script_json)
        _write_stage_logs(stage_logs_dir, "scriptwriter", script_raw, script_json)
        _validate_stage_output("scriptwriter", script_json)
        _trace_stage_end(trace_path, current_stage, "done")
        _set_pipeline_status(payload, "scriptwriter", "done")

        await status_message.edit_text("🎬 <b>Pipeline</b>\n\n🎨 Prompt Engineer running...")
        current_stage = "prompt_engineer"
        _trace_stage_start(trace_path, current_stage)
        prompt_raw = await _run_openclaw_agent(
            "prompt_engineer",
            session_id,
            _stage_prompt(
                "prompt_engineer", job_path=job_path, in_path=script_path, out_path=prompt_path
            ),
        )
        prompt_json = _extract_first_json_object(prompt_raw)
        prompt_json = _ensure_multishot_prompt_package(prompt_json)
        _safe_write_json(prompt_path, prompt_json)
        _write_stage_logs(stage_logs_dir, "prompt_engineer", prompt_raw, prompt_json)
        _trace_stage_end(trace_path, current_stage, "done")
        _set_pipeline_status(payload, "prompt_engineer", "done")

        if generation_mode == "hitl":
            _set_pipeline_status(payload, "video_generator", "bypassed_hitl")
            _set_pipeline_status(payload, "assembly_manager", "waiting_human_input")
            assets = payload.get("assets", {})
            asset_files, missing_files = _resolve_hitl_asset_files(assets)
            kling_input_package_dir.mkdir(parents=True, exist_ok=True)
            output_waiting_dir.mkdir(parents=True, exist_ok=True)

            planka_status = await _move_planka_card_to_ready_for_kling(job_id, prompt_json, assets)

            handoff = {
                "job_id": job_id,
                "status": "waiting_for_human_kling_output",
                "ready_for_kling_column": "08 Ready for Kling (Albert)",
                "approved_generation_input": prompt_json,
                "asset_files": asset_files,
                "missing_files": missing_files,
                "kling_input_package_dir": str(kling_input_package_dir),
                "output_waiting_dir": str(output_waiting_dir),
                "planka_status": planka_status,
                "task_summary": payload.get("task", {}),
                "created_at": _now_iso(),
            }
            hitl_path.write_text(json.dumps(handoff, indent=2, ensure_ascii=False))

            packaged_assets, package_errors = _export_hitl_input_package(
                package_dir=kling_input_package_dir,
                hitl_path=hitl_path,
                prompt_path=prompt_path,
                job_path=job_path,
                asset_files=asset_files,
            )
            handoff["packaged_asset_files"] = packaged_assets
            handoff["package_export_errors"] = package_errors
            hitl_path.write_text(json.dumps(handoff, indent=2, ensure_ascii=False))
            try:
                shutil.copy2(hitl_path, kling_input_package_dir / hitl_path.name)
            except Exception as exc:
                logger.warning(
                    "Failed to refresh packaged handoff json for job %s: %s", job_id, exc
                )

            await _best_effort_open_folder(kling_input_package_dir)
            await _best_effort_open_folder(output_waiting_dir)

            job_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

            segments = prompt_json.get("segments", []) if isinstance(prompt_json, dict) else []
            await status_message.edit_text(
                "⏸️ <b>Pipeline paused for HITL</b>\n\n"
                f"Job: <code>{job_id}</code>\n"
                f"Prompt package ready: <code>{len(segments)}</code> segment(s).\n"
                f"Stage logs: <code>{stage_logs_dir}</code>\n"
                f"Trace: <code>{trace_path}</code>\n"
                f"Planka: <code>{planka_status}</code>\n"
                f"Handoff file: <code>{hitl_path}</code>\n"
                f"Kling input package: <code>{kling_input_package_dir}</code>\n"
                f"Output waiting folder: <code>{output_waiting_dir}</code>\n"
                "Please generate video manually in Kling, then upload output here or send a direct URL."
            )
            return {
                "status": "waiting_human",
                "job_id": job_id,
                "job_dir": str(job_dir),
                "hitl_handoff_path": str(hitl_path),
                "kling_input_package_dir": str(kling_input_package_dir),
                "output_waiting_dir": str(output_waiting_dir),
                "approved_generation_input": prompt_json,
            }

        await status_message.edit_text("🚧 Autonomous mode is currently locked.")
        return {"status": "locked", "job_id": job_id, "job_dir": str(job_dir)}

    except Exception as e:
        logger.exception("OpenClaw pipeline failed")
        _safe_write_text(job_dir / "error.txt", str(e))
        _trace_stage_end(trace_path, current_stage, "failed", str(e))
        try:
            await status_message.edit_text(
                f"❌ <b>Pipeline failed</b>\n\n<code>{str(e)[:1200]}</code>\n\n"
                f"Job folder: <code>{job_dir}</code>\n"
                f"Stage logs: <code>{stage_logs_dir}</code>\n"
                f"Trace: <code>{trace_path}</code>"
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

    stage_logs_dir = job_dir / "stage_logs"
    trace_path = job_dir / "trace.json"
    stage_logs_dir.mkdir(parents=True, exist_ok=True)

    stage_prompt = (
        _stage_prompt("assembly_manager", job_path, prompt_path, assembly_out)
        + "\n"
        + f"Read human Kling output manifest at: {human_input_manifest}\n"
        + "Treat video_path or video_url as the source generated footage and continue pipeline."
    )
    session_id = f"video-pipeline_{job_id}_resume"
    _trace_stage_start(trace_path, "assembly_manager")
    assembly_raw = await _run_openclaw_agent("assembly_manager", session_id, stage_prompt)
    try:
        assembly_json = _extract_first_json_object(assembly_raw)
    except Exception:
        assembly_json = {"raw": assembly_raw}
    _safe_write_json(assembly_out, assembly_json)
    _write_stage_logs(stage_logs_dir, "assembly_manager", assembly_raw, assembly_json)
    _trace_stage_end(trace_path, "assembly_manager", "done")

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
