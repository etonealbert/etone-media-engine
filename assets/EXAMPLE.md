# Asset Directory Structure Examples

This file documents the expected folder naming conventions.
Real asset files are gitignored — only this reference is tracked.

## Avatars (`assets/avatars/`)

Structure: One subfolder per avatar.

```
assets/avatars/
├── AV01_Sarah_F_25_Fitness/
│   ├── front_medium.png      # Direct eye contact, chest up (MAIN for Kling)
│   ├── profile_left.png
│   ├── profile_right.png
│   └── full_body.png
├── AV02_Mike_M_30_Tech/
│   ├── front_medium.png
│   └── profile_left.png
```

**Naming:** `[ID]_[Name]_[Gender]_[Age]_[Niche]`
**Specs:** PNG/JPG, 1024x1024 or 1080x1920, even lighting, no face-covering accessories.

## Locations (`assets/locations/`)

```
assets/locations/
├── LOC01_ModernKitchen_MorningSun/
│   ├── wide_shot.png
│   ├── medium_shot.png
│   └── close_up.png
```

**Naming:** `[ID]_[Environment]_[Lighting]`
**Specs:** PNG/JPG, 1080x1920 (9:16), slight depth-of-field bokeh.

## Products (`assets/products/`)

```
assets/products/
├── Ghost_Creatine/
│   ├── product_front.png      # Transparent background
│   ├── product_side.png       # Transparent background
│   └── label_flat.jpg         # Unwrapped label for texture reference
```

**Naming:** `[Brand]_[ProductName]`
**Specs:** PNG (cutouts with transparent BG), min 1000x1000px.

## B-Rolls (`assets/b_rolls/`)

Flat folder, no subfolders.

```
assets/b_rolls/
├── Fitness_MixingPowder_Kitchen.mp4
├── Tech_Unboxing_Desk.mp4
```

**Naming:** `[Niche]_[Action]_[Setting].mp4`
**Specs:** MP4, 1080x1920, 3-8 seconds, no recognizable faces.

## Voiceovers (`assets/voiceovers/`)

Clean dry speech ONLY — required for Kling 3.0 lip-sync.

```
assets/voiceovers/
├── Hook_Creatine_Take1.wav
├── CTA_Supplement_Final.wav
```

**Specs:** WAV preferred, minimal reverb, NO background music.

## Audio/SFX (`assets/audio_sfx/`)

Music and effects — added ONLY during post-production assembly.

```
assets/audio_sfx/
├── Whoosh_Fast.wav
├── Music_Phonk_Gym.mp3
```

**Specs:** WAV (SFX) or MP3 (music), royalty-free/commercial-safe.
