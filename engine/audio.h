// ============================================================================
// AUDIO API
// ============================================================================
//
// PURPOSE:
// Public API for the multi-platform audio subsystem. Exposes audio lifecycle
// management (InitAudio/FreeAudio), presentation event playback, volume
// control (AudioMute), and sample identification (AUDIO_FILE enum).
//
// API OVERVIEW:
//
//   InitAudio()
//   - Platform-specific audio backend initialization
//   - Called once at startup (from game_app.cpp or platform entry point)
//   - Returns: true if audio initialized successfully, false otherwise
//   - Backends: CoreAudio (macOS), PulseAudio (Linux), SDL (cross-platform),
//               AudioWorklet (web modern), ScriptNode (web legacy)
//
//   FreeAudio()
//   - Platform-specific cleanup and resource deallocation
//   - Called once at shutdown
//   - Stops audio callback thread, frees sample buffers
//
//   AudioWalk(foot, volume, actor_kind, material)
//   - Play material-based footstep sound effect
//   - foot: 0=auto-alternate left/right, 1=left, 2=right
//   - volume: Playback volume (0-65535)
//   - actor_kind: reserved for actor-kind audio resonance (int; callers pass LocalPhysicsActorProfile::Kind)
//   - material: Terrain material index (0=rock, 1=wood, 2=dirt, etc.)
//   - Uses marker-based chunking: sample_chunk = 2*material+(foot&1)
//
//   AudioMute(mute)
//   - Toggle global audio volume
//   - mute=true: Sets volume to 0 (silence)
//   - mute=false: Restores volume to 32768 (50%)
//
//   XOgg(index, data, size)
//   - Decode Ogg Vorbis data into sample library slot
//   - index: Sample slot (0-63)
//   - data: Ogg Vorbis file data
//   - size: File size in bytes
//   - Called internally by LoadSample() or externally by web JS loader
//
//   GetSampleID(file)
//   - Map AUDIO_FILE enum to runtime sample index
//   - Returns: Sample slot index, or -1 if not loaded
//
// USAGE PATTERN:
// Game code calls AudioWalk() with foot (0=auto, 1=left, 2=right), volume,
// and terrain material index. The audio system automatically selects the
// appropriate footstep sample chunk from FOOTSTEPS.ogg based on material
// and foot. Markers embedded in the .ogg file define chunk boundaries for
// each material/foot combination.
//
// AUDIO_FILE ENUM:
// Maps logical sample names to indices. Currently includes:
// - FOREST: Background ambient loop
// - FOOTSTEPS: Multi-chunk footstep sample (markers for material/foot variation)
//
// Enum value AUDIO_FILES marks the count (used for array sizing).
//
// INTEGRATION:
// Included by: game.cpp, game_app.cpp, physics.cpp (for audio playback)
// Implementation: audio.cpp (1,167 lines, 5 platform backends)
//
// [DATA-CONTRACT:AUDIO_FILE] Enum order must match sample_names[] array in audio.cpp

#ifndef AUDIO_H
#define AUDIO_H

#include <stdint.h>

bool InitAudio();
void FreeAudio();

void CallAudio(const uint8_t* data, int size);
// actor_kind: reserved for actor-kind-specific audio resonance (int to avoid coupling to physics types;
// callers pass LocalPhysicsActorProfile::Kind, which converts implicitly)
void AudioWalk(int foot, int volume, int actor_kind, int material);
void AudioJump(int volume, int actor_kind);
void AudioLand(int volume, int actor_kind, int material);
void AudioAttack(int volume, int actor_kind, int weapon);
void AudioHurt(int volume, int actor_kind);
void AudioDie(int volume, int actor_kind);
int AudioSpatialVolume(int base_vol, float distance);
extern "C" void AudioRestoreForestAmbient();
void AudioMute(bool mute);

enum AUDIO_FILE
{
    // merge them all into single file, use in file markers
    // prepare similar files for several armor levels

    FOREST,
    FOOTSTEPS,
    JUMP,
    ATTACK,
    HURT,
    DIE,
    /*
    WALK_ROCK_L,
    WALK_ROCK_R,
    WALK_WOOD_L,
    WALK_WOOD_R,
    WALK_DIRT_L,
    WALK_DIRT_R,
    WALK_GRASS_L,
    WALK_GRASS_R,
    WALK_BUSH_L,
    WALK_BUSH_R,
    WALK_BLOOD_L,
    WALK_BLOOD_R,
    WALK_WATER_L,
    WALK_WATER_R,
    */


    AUDIO_FILES
};

int GetSampleID(AUDIO_FILE af);


// is this right direction?
/*
void SetAudioSteps(int num_steps, int contact);
void SetAudioWingsFreq(int freq);
void SetAudioSwoosh(int speed, int contact);
void SetAudioJump(int contact, bool end);
void SetAudioShoot(int contact);
void SetAudioWater(int height, int speed);
*/

#endif
