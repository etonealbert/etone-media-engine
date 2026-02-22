"""
OpenClaw agent runner mock with Kling 3.0 API stubs.
Simulates the multi-agent video production pipeline with status streaming.

INTEGRATION NOTES (for connecting to real OpenClaw):
-----------------------------------------------------
This module contains a mock implementation that simulates the agent workflow.
When the real OpenClaw system is ready, replace the asyncio.sleep() calls
with actual API/CLI invocations. The JSON payload structure is documented
in the comments to match the expected OpenClaw Gateway contract.
"""

import asyncio
import json
from pathlib import Path

from aiogram import Bot
from aiogram.types import Message

from bot.services.file_manager import get_latest_video


async def run_openclaw_agents(
    task_data: dict,
    status_message: Message,
    bot: Bot,
) -> Path | None:
    """
    Run the OpenClaw multi-agent video production pipeline.

    This is currently a mock that simulates the workflow with status updates.
    In production, this function would:
    1. Construct a JSON payload from task_data
    2. Submit it to the OpenClaw Gateway (CLI or API)
    3. Poll for status updates
    4. Download the resulting video

    Args:
        task_data: Dict containing avatar, location, product, task description,
                   and target length selections.
        status_message: The Telegram message to edit with status updates.
        bot: The Bot instance for API calls.

    Returns:
        Path to the generated video file, or None if generation failed.
    """

    # =========================================================================
    # STEP 1: Construct the OpenClaw JSON Payload
    # =========================================================================
    # When connecting to the real OpenClaw system, this payload would be sent
    # to the OpenClaw Gateway via CLI (`openclaw run --config payload.json`)
    # or via HTTP API (`POST /api/v1/jobs`).

    target_length = task_data.get("target_length_seconds", 15)
    needs_chaining = target_length > 15
    num_segments = (target_length + 14) // 15  # ceil division

    payload = {
        # --- Asset References ---
        "avatar": {
            "folder": task_data.get("avatar_folder", ""),
            "path": task_data.get("avatar_path", ""),
            # The front_medium.png file is used as the primary face reference
            # for Kling's Elements/Reference API to lock facial features
        },
        "location": {
            "folder": task_data.get("location_folder", ""),
            "path": task_data.get("location_path", ""),
        },
        "product": {
            "folder": task_data.get("product_folder", ""),
            "path": task_data.get("product_path", ""),
        },
        # --- Task Description ---
        "task_description": task_data.get("task_description", ""),
        # --- Video Configuration ---
        "target_length_seconds": target_length,
        "num_segments": num_segments,
        # --- Kling 3.0 API Configuration ---
        "kling_config": {
            # CRITICAL: Enable Elements/Reference feature to lock the avatar's
            # facial features from the base image. Without this, Kling will
            # generate inconsistent faces across segments.
            "use_elements_locking": True,
            # CRITICAL: Enforce the exact prompt layer ordering required by
            # Kling 3.0 for optimal results:
            # Scene -> Character -> Action -> Camera -> Style
            "prompt_structure": "Scene -> Character -> Action -> Camera -> Style",
            # Kling API hard limit per segment
            "max_segment_seconds": 15,
            # When target > 15s, chaining is used:
            # The final frame of segment A becomes the starting frame of segment B.
            # This maintains visual continuity across segments.
            "chain_segments": needs_chaining,
            # Audio constraints: Kling requires clean, dry audio for lip-sync.
            # Background music must be added ONLY during post-production assembly.
            "audio_source": "voiceovers",  # NOT audio_sfx
            "audio_note": "Clean dry audio only. BGM added in post.",
        },
        # --- Planka Integration (Future) ---
        # When Planka integration is active, each job creates a card:
        # "planka_config": {
        #     "project_id": "...",
        #     "board_id": "...",
        #     "create_card": True,
        #     "card_title": f"Video: {task_data.get('task_description', '')[:50]}",
        #     "initial_list": "In Progress",
        # },
    }

    # Log the payload for debugging (would be sent to OpenClaw in production)
    _payload_json = json.dumps(payload, indent=2, ensure_ascii=False)

    # =========================================================================
    # STEP 2: Agent Status Streaming
    # =========================================================================
    # In production, these status updates would come from the OpenClaw event
    # stream (via WebSocket, SSE, or polling the /api/v1/jobs/{id}/status endpoint).

    agent_stages = [
        ("\U0001f50d Analyst parsing customer pain points...", 3),
        ("\u270d\ufe0f Scriptwriter writing hooks...", 3),
        (
            "\U0001f3a8 Prompt Engineer working with Kling 3.0...\n"
            f"   Prompt structure: Scene > Character > Action > Camera > Style",
            3,
        ),
    ]

    for status_text, delay in agent_stages:
        try:
            await status_message.edit_text(
                f"\U0001f3ac <b>OpenClaw Pipeline Active</b>\n\n{status_text}"
            )
        except Exception:
            pass  # Message might not have changed
        await asyncio.sleep(delay)

    # =========================================================================
    # STEP 3: Kling API Polling Simulation
    # =========================================================================
    # The real Kling API workflow:
    # 1. POST /api/v1/lip-sync  (submit the job)
    # 2. GET  /api/v1/lip-sync/{task_id}  (poll for completion)
    # The API returns status: "pending" | "processing" | "completed" | "failed"
    # Typical processing time: 30-120 seconds per segment.

    max_attempts = 10
    for segment in range(1, num_segments + 1):
        segment_label = f" (Segment {segment}/{num_segments})" if needs_chaining else ""

        for attempt in range(1, max_attempts + 1):
            try:
                status_text = (
                    f"\u23f3 Kling 3.0 API rendering{segment_label}\n"
                    f"   Attempt {attempt}/{max_attempts}..."
                )
                if needs_chaining and segment > 1:
                    status_text += (
                        "\n   \U0001f517 Chaining: using last frame of "
                        f"segment {segment - 1} as start frame"
                    )

                await status_message.edit_text(
                    f"\U0001f3ac <b>OpenClaw Pipeline Active</b>\n\n{status_text}"
                )
            except Exception:
                pass
            await asyncio.sleep(2)

            # Simulate completion after a few attempts
            if attempt >= 3:
                break

    # =========================================================================
    # STEP 4: Final Assembly
    # =========================================================================
    try:
        await status_message.edit_text(
            "\U0001f3ac <b>OpenClaw Pipeline Active</b>\n\n"
            "\U0001f39e Manager assembling final video...\n"
            "   Merging segments, adding BGM from audio_sfx, "
            "applying transitions..."
        )
    except Exception:
        pass
    await asyncio.sleep(3)

    # =========================================================================
    # STEP 5: Retrieve Output
    # =========================================================================
    # In production, the OpenClaw Gateway would return the output file path
    # or a download URL. For now, we look for the latest .mp4 in output/videos/.

    video_path = await get_latest_video()

    if video_path:
        try:
            await status_message.edit_text(
                f"\u2705 <b>Pipeline complete!</b>\n\nOutput: <code>{video_path.name}</code>"
            )
        except Exception:
            pass
    else:
        try:
            await status_message.edit_text(
                "\u26a0\ufe0f <b>Pipeline complete</b> but no output video found.\n\n"
                "Place a test .mp4 file in <code>output/videos/</code> "
                "to test the delivery flow."
            )
        except Exception:
            pass

    return video_path
