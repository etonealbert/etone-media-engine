# OpenClaw Agent Orchestration Guide

> This document defines the multi-agent pipeline that the Telegram bot orchestrates.
> Use this as a configuration reference when setting up OpenClaw agents.

## Pipeline Overview

The video production pipeline consists of 5 specialized agents that execute
sequentially. The Telegram bot collects inputs (avatar, location, product,
task description, target length) and passes them as a JSON payload to the
OpenClaw Gateway, which routes work through each agent.

```
[Telegram Bot] → [OpenClaw Gateway]
                      │
                      ├── 1. Analyst Agent
                      ├── 2. Scriptwriter Agent
                      ├── 3. Prompt Engineer Agent
                      ├── 4. Video Generator Agent (Kling 3.0)
                      └── 5. Assembly Manager Agent
```

## Agent Definitions

### Agent 1: Analyst

**Role:** Research customer pain points and competitive angles.

**Input:**
- `task_description` — Amazon product URL or freeform description
- `product` — Product metadata (brand, name, category)

**Behavior:**
1. If input is an Amazon URL: scrape/parse the product listing
   - Extract product title, price, key features
   - Parse customer reviews (focus on negative reviews = pain points)
   - Identify top 3-5 customer complaints/desires
2. If input is freeform text: extract key selling points and target audience
3. Output a structured brief:

**Output Schema:**
```json
{
  "product_name": "Ghost Creatine",
  "target_audience": "Gym beginners, 18-30",
  "pain_points": [
    "Most creatine tastes terrible",
    "Confused about dosing",
    "Worried about bloating"
  ],
  "key_hooks": [
    "Finally a creatine that actually tastes good",
    "One scoop, no confusion",
    "Zero bloat formula"
  ],
  "competitive_angle": "Taste + simplicity vs generic brands",
  "cta_suggestion": "Link in bio for 20% off"
}
```

**OpenClaw Config:**
```yaml
agent: analyst
model: gpt-4o  # or claude-sonnet
tools:
  - web_search
  - url_parser
temperature: 0.3
max_tokens: 2000
```

---

### Agent 2: Scriptwriter

**Role:** Write the video script with hooks, body, and CTA.

**Input:**
- Analyst output (pain points, hooks, CTA suggestion)
- `target_length_seconds` — determines script length
- Avatar metadata (name, gender, age, niche)

**Behavior:**
1. Write a script segmented into timed blocks (each ≤15s for Kling)
2. Each segment has: spoken text + visual direction
3. Opening hook must be attention-grabbing (first 3 seconds)
4. Follow UGC conventions: conversational tone, direct address

**Output Schema:**
```json
{
  "title": "Ghost Creatine Review",
  "total_duration_seconds": 30,
  "segments": [
    {
      "segment_id": 1,
      "duration_seconds": 15,
      "spoken_text": "Okay so I finally tried Ghost Creatine and I have to be honest...",
      "visual_direction": "Avatar talking to camera, kitchen background, holding product",
      "emotion": "curious, slightly skeptical"
    },
    {
      "segment_id": 2,
      "duration_seconds": 15,
      "spoken_text": "This is the first creatine that doesn't taste like chalk. And zero bloat. Link in bio.",
      "visual_direction": "Avatar mixing powder, close-up of product, then direct CTA",
      "emotion": "excited, genuine surprise"
    }
  ],
  "voiceover_instructions": "Natural, conversational. Not salesy. Slight vocal fry ok."
}
```

**OpenClaw Config:**
```yaml
agent: scriptwriter
model: claude-sonnet  # creative writing benefits from Claude
tools: []
temperature: 0.7
max_tokens: 3000
system_prompt: |
  You are a UGC scriptwriter for TikTok/Reels.
  Write in first person as the avatar character.
  Each segment MUST be ≤15 seconds of spoken text.
  Hook must grab attention in first 3 seconds.
```

---

### Agent 3: Prompt Engineer

**Role:** Generate Kling 3.0 API prompts for each video segment.

**Input:**
- Scriptwriter output (segments with visual direction)
- Avatar assets (front_medium.png path for face locking)
- Location assets (medium_shot.png path for background)
- Product assets (product_front.png path)

**Behavior:**
1. For each segment, construct a Kling prompt following the STRICT layer order:
   `Scene → Character → Action → Camera → Style`
2. Set up Elements/Reference API calls for face consistency
3. Generate negative prompts to avoid common failures

**Output Schema (per segment):**
```json
{
  "segment_id": 1,
  "kling_prompt": {
    "positive": "Modern kitchen with warm morning sunlight streaming through windows. A 25-year-old female with shoulder-length brown hair wearing a casual grey t-shirt. She holds a white supplement container and speaks directly to camera with a curious expression. Medium shot, slight low angle, shallow depth of field. Photorealistic, natural lighting, 4K UGC style.",
    "negative": "cartoon, anime, deformed face, extra fingers, blurry, watermark, text overlay",
    "prompt_structure_verified": "Scene:kitchen → Character:female_25 → Action:holding_product_speaking → Camera:medium_low_angle → Style:photorealistic_UGC"
  },
  "elements_config": {
    "reference_image": "assets/avatars/AV01_Sarah_F_25_Fitness/front_medium.png",
    "face_lock_strength": 0.85,
    "reference_type": "face"
  },
  "generation_params": {
    "duration_seconds": 15,
    "aspect_ratio": "9:16",
    "resolution": "1080x1920",
    "fps": 30
  }
}
```

**OpenClaw Config:**
```yaml
agent: prompt_engineer
model: gpt-4o  # precise structured output
tools: []
temperature: 0.2  # low creativity, high precision
max_tokens: 2000
system_prompt: |
  You are a Kling 3.0 prompt engineer.
  ALWAYS follow this exact layer order: Scene → Character → Action → Camera → Style
  NEVER deviate from this structure.
  Include Elements/Reference config for face consistency.
```

**Critical Kling Constraints:**
- Prompt MUST follow: Scene → Character → Action → Camera → Style
- `use_elements_locking: true` in every request
- Face reference image: always `front_medium.png` from avatar folder
- Each segment ≤ 15 seconds (hard API limit)

---

### Agent 4: Video Generator (Kling 3.0 Interface)

**Role:** Submit prompts to Kling API, poll for completion, download results.

**Input:**
- Prompt Engineer output (per-segment prompts + configs)
- Voiceover audio files (clean dry speech from `assets/voiceovers/`)

**Behavior:**
1. For each segment:
   a. Submit generation request to Kling API
   b. Poll `GET /api/v1/lip-sync/{task_id}` every 5 seconds
   c. Handle status: `pending` → `processing` → `completed` | `failed`
   d. On completion: download generated video segment
2. If chaining (target > 15s):
   a. Extract last frame of segment N
   b. Use as `start_frame` for segment N+1
   c. This maintains visual continuity across segments
3. Report progress back to Telegram bot for status streaming

**Output:**
- One `.mp4` file per segment in a temp directory
- Metadata JSON with timecodes and segment boundaries

**OpenClaw Config:**
```yaml
agent: video_generator
model: none  # this agent calls external API, not an LLM
tools:
  - kling_api_client
  - ffmpeg  # for frame extraction during chaining
api_config:
  kling_base_url: "https://api.klingai.com/v1"
  kling_api_key: "${KLING_API_KEY}"
  poll_interval_seconds: 5
  max_poll_attempts: 60  # 5 min timeout per segment
  retry_on_failure: 2
```

**Kling API Flow:**
```
POST /api/v1/videos/generations
  → Returns: { "task_id": "..." }

GET /api/v1/videos/generations/{task_id}
  → Returns: { "status": "processing" | "completed", "video_url": "..." }

POST /api/v1/lip-sync
  → Submit voiceover for lip-sync on generated video
  → Returns: { "task_id": "..." }

GET /api/v1/lip-sync/{task_id}
  → Poll until completed
```

---

### Agent 5: Assembly Manager

**Role:** Combine segments, add audio, produce final video.

**Input:**
- Generated video segments (from Video Generator)
- Background music (from `assets/audio_sfx/`)
- Sound effects (from `assets/audio_sfx/`)
- Script metadata (segment timecodes)

**Behavior:**
1. Concatenate video segments in order
2. Add background music (volume reduced under speech)
3. Add SFX (whooshes for transitions, etc.)
4. Apply any transitions between segments (cross-dissolve, etc.)
5. Export final video to `output/videos/`
6. Name format: `{timestamp}_{product}_{avatar}.mp4`

**Output:**
- Final `.mp4` in `output/videos/`
- Assembly metadata JSON

**OpenClaw Config:**
```yaml
agent: assembly_manager
model: none  # this agent runs FFmpeg, not an LLM
tools:
  - ffmpeg
  - ffprobe
output_config:
  format: mp4
  codec: h264
  audio_codec: aac
  resolution: 1080x1920
  fps: 30
  output_dir: "output/videos/"
```

---

## Full Pipeline JSON Payload

This is the complete payload the Telegram bot constructs and sends to OpenClaw:

```json
{
  "job_id": "uuid-v4",
  "created_at": "2026-02-22T12:00:00Z",
  "created_by": "telegram_bot",

  "assets": {
    "avatar": {
      "folder": "AV01_Sarah_F_25_Fitness",
      "path": "assets/avatars/AV01_Sarah_F_25_Fitness/",
      "face_reference": "assets/avatars/AV01_Sarah_F_25_Fitness/front_medium.png"
    },
    "location": {
      "folder": "LOC01_ModernKitchen_MorningSun",
      "path": "assets/locations/LOC01_ModernKitchen_MorningSun/",
      "background": "assets/locations/LOC01_ModernKitchen_MorningSun/medium_shot.png"
    },
    "product": {
      "folder": "Ghost_Creatine",
      "path": "assets/products/Ghost_Creatine/",
      "hero_image": "assets/products/Ghost_Creatine/product_front.png"
    }
  },

  "task": {
    "description": "https://amazon.com/dp/B0EXAMPLE or freeform text",
    "target_length_seconds": 30,
    "num_segments": 2
  },

  "kling_config": {
    "use_elements_locking": true,
    "prompt_structure": "Scene -> Character -> Action -> Camera -> Style",
    "max_segment_seconds": 15,
    "chain_segments": true,
    "audio_source": "voiceovers",
    "aspect_ratio": "9:16",
    "resolution": "1080x1920"
  },

  "pipeline": [
    {"agent": "analyst", "status": "pending"},
    {"agent": "scriptwriter", "status": "pending"},
    {"agent": "prompt_engineer", "status": "pending"},
    {"agent": "video_generator", "status": "pending"},
    {"agent": "assembly_manager", "status": "pending"}
  ],

  "planka": {
    "create_card": true,
    "project_id": "...",
    "board_id": "...",
    "initial_list": "In Progress"
  }
}
```

## Agent Count Summary

| # | Agent | Type | Model | Purpose |
|---|-------|------|-------|---------|
| 1 | Analyst | LLM | GPT-4o | Research pain points, hooks |
| 2 | Scriptwriter | LLM | Claude Sonnet | Write timed script segments |
| 3 | Prompt Engineer | LLM | GPT-4o | Generate Kling prompts |
| 4 | Video Generator | API | Kling 3.0 | Generate + lip-sync video |
| 5 | Assembly Manager | Tool | FFmpeg | Combine segments, add audio |

**Total: 5 agents** — 3 LLM-based, 1 external API, 1 local tool.

## HITL Override (Current Production Path)

For Telegram `/generator`, current production flow is HITL:
- `analyst -> scriptwriter -> prompt_engineer` run normally
- `video_generator` is explicitly bypassed
- card is routed to `08 Ready for Kling (Albert)`
- Albert manually generates with Kling and submits output
- `assembly_manager` resumes pipeline using Albert-provided media

## Environment Variables Needed for Full Pipeline

```env
# Already configured
BOT_TOKEN=...
ADMIN_ID=...

# Add for real agent execution
OPENAI_API_KEY=...          # For Analyst + Prompt Engineer
ANTHROPIC_API_KEY=...       # For Scriptwriter
KLING_API_KEY=...           # For Video Generator
ELEVENLABS_API_KEY=...      # For voiceover generation (optional)

# Planka integration
PLANKA_BASE_URL=...
PLANKA_TOKEN=...
```

## How to Configure Agents in OpenClaw

Each agent should be defined as an OpenClaw worker with:

1. **System prompt** — defines the agent's role and constraints
2. **Input schema** — Pydantic model of expected input
3. **Output schema** — Pydantic model of expected output
4. **Tools** — external capabilities (web search, API clients, FFmpeg)
5. **Model** — which LLM to use (or `none` for tool-only agents)
6. **Temperature** — creativity vs precision tradeoff

The pipeline is sequential: each agent's output becomes the next agent's input.
The Telegram bot streams status updates by polling the OpenClaw job status.
