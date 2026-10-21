// ============================================================================
// AUDIO SYSTEM
// ============================================================================
//
// PURPOSE:
// Multi-platform audio streaming subsystem with stb_vorbis Ogg decoding,
// sample library management, and 16-track mixing. Supports 5 audio backends
// selected at compile time via #ifdef for maximum platform coverage.
//
// WHY FIVE BACKENDS:
// No single audio API works across all target platforms. We provide:
//
//   1. CoreAudio (macOS)
//      - Platform: macOS, iOS
//      - API: AudioQueue with 3-buffer ring
//      - Why: Lowest latency on Apple platforms
//      - Threading: Separate callback thread
//
//   2. PulseAudio (Linux)
//      - Platform: Desktop Linux
//      - API: pa_threaded_mainloop
//      - Why: Standard Linux desktop audio server
//      - Threading: Separate thread with lock/signal coordination
//
//   3. SDL Audio
//      - Platform: Cross-platform fallback
//      - API: SDL2 audio callback
//      - Why: Portability for platforms without native audio
//      - Threading: Separate callback thread
//
//   4. Emscripten AudioWorklet (web preferred)
//      - Platform: Modern web browsers
//      - API: AudioWorkletProcessor
//      - Why: Separate thread (better performance, lower latency)
//      - Threading: Dedicated audio worklet thread
//
//   5. Emscripten ScriptNode (web legacy)
//      - Platform: Older web browsers
//      - API: ScriptProcessorNode
//      - Why: Wider browser support (fallback for browsers without AudioWorklet)
//      - Threading: Main thread (synchronous execution)
//
// STB_VORBIS INTEGRATION:
// XOgg() decodes .ogg files to 16-bit stereo PCM using stb_vorbis public domain library.
//
//   API calls used:
//   - stb_vorbis_open_memory(): Open Ogg stream from memory buffer
//   - stb_vorbis_get_info(): Get sample rate and channel count
//   - stb_vorbis_stream_length_in_samples(): Get total sample count
//   - stb_vorbis_get_frame_float(): Stream decode next frame
//   - stb_vorbis_get_markers(): Custom extension for embedded timing markers
//   - stb_vorbis_close(): Free decoder resources
//
//   Custom extension: stb_vorbis_get_markers() returns embedded markers
//   - Format: Tab-separated float pairs (start\tend timestamps in seconds)
//   - Use case: Footstep timing markers for material/foot variation
//   - Example: "0.0\t0.5\n0.5\t1.0" = 2 markers at 0-0.5s, 0.5-1.0s
//   - Stored after PCM data: int32_t marker_count, int32_t marker_pairs[]
//
//   Mono→Stereo duplication: Mono .ogg files duplicated to both L/R channels
//   for uniform stereo output (all backends expect stereo PCM).
//
// SAMPLE LIBRARY:
// 64-slot cache stores decoded PCM samples in memory for zero-latency playback.
//
//   Data structures:
//   - lib_sample_data[MAX_SAMPLES]: int16_t* array (stereo PCM buffers)
//   - lib_sample_len[MAX_SAMPLES]: Sample count per buffer
//   - sample_hash[HASH_MAKS+1]: Hash table for filename→sample_id lookup
//   - sample_ids[AUDIO_FILES]: Runtime mapping from AUDIO_FILE enum to slot
//
//   Loading flow:
//   - LoadAllSamples() scans samples/ directory at init
//   - LoadSample() checks hash table, loads .ogg if missing
//   - FindSample() uses djb2 hash for O(1) lookup
//   - GetSampleID() maps AUDIO_FILE enum to runtime sample index
//
// 16-TRACK MIXING ENGINE:
// PlyTrack[PLY_TRACKS] array supports 16 simultaneous sound effects.
//
//   Playback flow:
//   - DriverAudioCmd(): Receives commands (track, sample, volume, chunk)
//   - DriverAudioCB(): Generates mixed output (sums all active tracks)
//   - Marker-based chunking: sample_chunk = 2*material+(foot&1)
//
//   Track structure (PlyTrack):
//   - sample_id: Which sample to play (-1 = inactive)
//   - sample_vol: Track volume (0-65535)
//   - sample_pos: Current playback position in samples
//   - sample_end: Stop position (marker end or sample end)
//
//   Mixing algorithm:
//   - Sum all active tracks with int32_t accumulator (headroom for 16 tracks)
//   - Saturate to [-32767, +32767] to prevent clipping
//   - Apply global volume (volume variable, 0-32768)
//
// COMMAND/CALLBACK FLOW:
//
//   Game Code
//      ↓ AudioWalk(foot, volume, actor_kind, material)
//   CallAudio(data, size)
//      ↓ Enqueue command
//   DriverAudioCmd(data, size)
//      ↓ Configure track (sample_id, volume, chunk)
//   DriverAudioCB(buffer, frames)
//      ↓ Mix all tracks
//   Hardware Output (CoreAudio/PulseAudio/SDL/Worklet/ScriptNode)
//
// THREADING MODEL:
//
//   Desktop (CoreAudio/PulseAudio/SDL):
//   - CallAudio() enqueues commands via mutex-protected queue
//   - Audio callback runs on separate OS thread
//   - OnAudioCall() dequeues commands with lock_guard
//
//   Web AudioWorklet:
//   - CallAudio() uses EM_ASM postMessage to worklet thread
//   - Audio processing runs on dedicated worklet thread
//   - DriverAudioCmd() receives messages from main thread
//
//   Web ScriptNode:
//   - CallAudio() calls DriverAudioCmd() synchronously
//   - Audio processing runs on main thread
//   - No threading, zero-latency command execution
//
// KEY DATA STRUCTURES:
//
//   lib_sample_data[MAX_SAMPLES]  Decoded PCM buffers (int16_t stereo)
//   lib_sample_len[MAX_SAMPLES]   Sample count per buffer
//   ply_track[PLY_TRACKS]         Active playback tracks
//   sample_hash[HASH_MAKS+1]      Filename→sample_id hash table
//   volume                        Global volume (0-32768)
//
// KEY FUNCTIONS (line references):
//
//   InitAudio()         Line 593 (CoreAudio), 702 (PulseAudio), 862 (SDL), 1005 (Emscripten)
//   FreeAudio()         Line 586, 681, 840, 993
//   CallAudio()         Line 556 (desktop), 949 (web)
//   DriverAudioCmd()    Line 344 - Process playback command
//   DriverAudioCB()     Line 465 - Generate mixed audio output
//   XOgg()              Line 60 - Decode Ogg Vorbis to PCM
//   LoadAllSamples()    Line 314 - Pre-load all .ogg files
//   AudioWalk()         Line 27 - Play material-based footstep sound
//   AudioMute()         Line 21 - Toggle global volume
//
// [DEPENDENCY:STB_VORBIS] XOgg() uses stb_vorbis for Ogg Vorbis streaming decode
// [DATA-CONTRACT:OGG] Sample files in samples/ must be Ogg Vorbis format

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

#define _USE_MATH_DEFINES
#include <math.h>

#include "fast_rand.h"
#include "audio.h"

#include "stb_vorbis.h"

#define SEMI_TONE(x,y) x*pow(2.0,y/12.0)

#define PLY_TRACKS 16

#ifndef WORKLET
void AudioMute(bool mute)
{
    uint16_t vol = mute ? 0 : 32768;
	CallAudio((const uint8_t*)&vol, 2);
	if (!mute)
		AudioRestoreForestAmbient();
}

// WHY material-based sample selection: Each terrain material (rock, wood, dirt, grass, etc.)
// has distinct audio properties. Markers in FOOTSTEPS.ogg provide chunks for each material.
// WHY chunk indexing formula: 2*material+(foot&1) maps to marker indices.
// - material*2 = base offset for material (each material has 2 chunks: left/right)
// - +(foot&1) = add 0 for left foot, 1 for right foot
// Example: material=3 (grass), foot=1 (right) → chunk=7
void AudioWalk(int foot, int volume, int actor_kind, int material)
{
	(void)actor_kind; // Reserved for actor-kind-specific resonance; value is never a nullable pointer.
	// remember previous foot timestamp
	// so we can (ex/in)terpolate sub events for actor-kind resonance
	// add some rand delay to each sub event

	// get sample id for given: foot, actor kind, material

    static int land = 0;
    if (foot==0)
    {
        land ^= 1;
        foot = land;
    }

    // int sample = GetSampleID((AUDIO_FILE)(2*material+(foot&1))); // temp!
    int sample = GetSampleID(FOOTSTEPS);
    int sample_chunk = 2*material+(foot&1);

    static int track = 0;
	int32_t data[] = { track, sample, volume, sample_chunk };
    track++;
    if (track==PLY_TRACKS)
        track=0;

	CallAudio((uint8_t*)data, sizeof(data));
}

static void AudioPlaySample(AUDIO_FILE file, int volume, int sample_chunk)
{
	if (volume <= 0)
		return;
	if (volume > 65535)
		volume = 65535;

	int sample = GetSampleID(file);
	if (sample < 0)
		return;

	static int track = 0;
	int32_t data[] = { track, sample, volume, sample_chunk };
	track++;
	if (track == PLY_TRACKS)
		track = 0;

	CallAudio((uint8_t*)data, sizeof(data));
}

void AudioJump(int volume, int actor_kind)
{
	(void)actor_kind;
	AudioPlaySample(JUMP, volume, -1);
}

void AudioLand(int volume, int actor_kind, int material)
{
	AudioWalk(0, volume, actor_kind, material);
}

void AudioAttack(int volume, int actor_kind, int weapon)
{
	(void)actor_kind;
	(void)weapon;
	AudioPlaySample(ATTACK, volume, -1);
}

void AudioHurt(int volume, int actor_kind)
{
	(void)actor_kind;
	AudioPlaySample(HURT, volume, -1);
}

void AudioDie(int volume, int actor_kind)
{
	(void)actor_kind;
	AudioPlaySample(DIE, volume, -1);
}

int AudioSpatialVolume(int base_vol, float distance)
{
	if (base_vol <= 0)
		return 0;
	if (!isfinite(distance) || distance < 0.0f)
		distance = 0.0f;
	float v = (float)base_vol / (1.0f + distance / 10.0f);
	if (v < 1.0f)
		return 0;
	if (v > 65535.0f)
		return 65535;
	return (int)v;
}
#endif // !WORKLET

#define MAX_SAMPLES 64
// each sample data is prolonged with int32 array containing markers (first value is num of markers)
static int16_t* lib_sample_data[MAX_SAMPLES] = {0}; 
static int lib_sample_len[MAX_SAMPLES] = {0};

// WHY stb_vorbis: Public domain, single-file library with streaming decode support.
// No external dependencies, easy integration across all platforms.
// WHY mono→stereo duplication: All backends expect stereo PCM output. Mono files
// duplicated to both L/R channels for uniform processing.
// WHY marker parsing: Embedded timing markers enable footstep variation by material
// and foot (left/right). Markers stored after PCM data for chunk-based playback.
// [DEPENDENCY:STB_VORBIS] Decodes Ogg Vorbis to PCM
extern "C" void XOgg(int index, const uint8_t* data, int ogg_size)
{
    if (index>=MAX_SAMPLES)
        return;

    int err = 0;
    // [DEPENDENCY:STB_VORBIS] Open Ogg stream from memory
    stb_vorbis* ogg = stb_vorbis_open_memory(data,ogg_size,&err,0);
    if (!ogg)
    {
        // FL-4145: surface decode failure
        fprintf(stderr, "[AUDIO] XOgg: stb_vorbis_open_memory failed for slot %d (err=%d, ogg_size=%d)\n", index, err, ogg_size);
        lib_sample_data[index]=0;
        lib_sample_len[index]=0;
        return;
    }

    // [DEPENDENCY:STB_VORBIS] Get total sample count
    int size = stb_vorbis_stream_length_in_samples(ogg);
    int offs = 0;

    // [DEPENDENCY:STB_VORBIS] Get sample rate and channel count
    stb_vorbis_info nfo = stb_vorbis_get_info(ogg);
    int freq = (int)nfo.sample_rate;
    // [DEPENDENCY:STB_VORBIS] Get embedded markers (custom extension)
    const char* markers = stb_vorbis_get_markers(ogg);
    int num_markers = 0;
    if (markers)
    {
        const char* ptr = markers;
        while (1)
        {
            if (*ptr>32)
                num_markers++;
            ptr = strchr(ptr,'\n');
            if (!ptr)
                break;
            ptr++;
        }
    }

    int16_t* dec = (int16_t*)malloc(sizeof(int16_t)*2*size + sizeof(int32_t)*(num_markers*2+1));
    int32_t* mrk = (int32_t*)(dec + 2*size) + 1;

    mrk[-1] = num_markers;

    if (markers)
    {
        const char* ptr = markers;
        for (int i=0; i<num_markers; i++)
        {
            float a=0,b=0;
            sscanf(ptr,"%f\t%f", &a,&b);
            {
                ptr = strchr(ptr,'\n');
                ptr++;
            }
            
            mrk[2*i+0] = (int)floor(a*freq+0.5);
            mrk[2*i+1] = (int)floor(b*freq+0.5);
        }
    }

    float** ptr=0;
    int len;
    int chn;

    // [DEPENDENCY:STB_VORBIS] Stream decode frames
    while ( ( len = stb_vorbis_get_frame_float(ogg, &chn, &ptr) ) )
    {
        if (chn==1)
        {
            // mono -> L=0, R=0
            for (int i=0; i<len; i++)
            {
                if (offs>=2*size)
                {
                    // clip!
                    i=len;
                    break;
                }

                int m = (int)(ptr[0][i]*32767);
                if (m<-32767)
                    m=-32767;
                else
                if (m>+32767)
                    m=+32767;

                dec[offs++] = m;
                dec[offs++] = m;
            }
        }
        else
        {
            // stereo L=0, R=1
            for (int i=0; i<len; i++)
            {
                if (offs>=2*size)
                {
                    // clip!
                    i=len;
                    break;
                }

                int l = (int)(ptr[0][i]*32767);
                if (l<-32767)
                    l=-32767;
                else
                if (l>+32767)
                    l=+32767;

                int r = (int)(ptr[1][i]*32767);
                if (r<-32767)
                    r=-32767;
                else
                if (r>+32767)
                    r=+32767;

                dec[offs++] = l;
                dec[offs++] = r;
            }
        }
    }

    // [DEPENDENCY:STB_VORBIS] Free decoder resources
    stb_vorbis_close(ogg);

    lib_sample_data[index] = dec;
    lib_sample_len[index] = offs>>1;
}

#ifndef WORKLET
extern char base_path[];

struct SampleHash
{
    SampleHash* next;
    uint32_t hash;
    uint32_t id;
    char name[1];
};

#define HASH_MAKS (MAX_SAMPLES-1)
static SampleHash* sample_hash[HASH_MAKS+1]={0};
static int samples=0;

static int FindSample(const char* name, uint32_t* h, int* l)
{
    if (!name)
        return -1;

    uint32_t hash = 5381;
    const char* n = name;
    while (unsigned int c = *n++)
        hash = ((hash << 5) + hash) + c;

    if (h)
        *h = hash;
    if (l)
        *l = (int)(n-name);

    SampleHash* buck = sample_hash[hash&HASH_MAKS];
    while (buck)
    {
        if (buck->hash == hash && strcmp(name,buck->name)==0)
            return buck->id;
        buck = buck->next;
    }

    return -1;
}

#ifdef EMSCRIPTEN
extern "C" void Sample(const char* name)
{
    uint32_t hash;
    int len;
    if (FindSample(name,&hash,&len)<0)
    {
        SampleHash* item = (SampleHash*)malloc(sizeof(SampleHash)+len);
        SampleHash** base = sample_hash + (hash&HASH_MAKS);
        item->next = *base;
        *base = item;
        item->hash = hash;
        item->id = samples;
        strcpy(item->name,name);        
    }
    else
    {
        // name collision!!! how?
    }

    samples++;
}
#endif

static int LoadSample(const char* name)
{
    // if in hashmap, return its id
    uint32_t hash;
    int len;
    int id = FindSample(name,&hash,&len);
    if (id >= 0)
        return id;

    #ifdef EMSCRIPTEN
    return -1; // if not found in hashmap
    #else

    // FL-4145: load & dec file from <base_path>assets/samples/<name>
    // (the loader previously read from <base_path>samples/ but assets live at
    // assets/samples/; silent fopen failure produced inaudible runs)
    char path[1024];
    snprintf(path, sizeof(path), "%sassets/samples/%s", base_path, name);
    FILE* f = fopen(path,"rb");

    if (!f) // if file not found return -1
    {
        // FL-4145: surface the path-drift failure instead of failing silently.
        fprintf(stderr, "[AUDIO] LoadSample: fopen failed for %s (errno=%d)\n", path, errno);
        return -1;
    }

    if (samples == MAX_SAMPLES)
    {
        fclose(f);
        return -1;
    }

    fseek(f,0,SEEK_END);
    int size = (int)ftell(f);
    fseek(f,0,SEEK_SET);

    uint8_t* data = (uint8_t*)malloc(size);
    int r = (int)fread(data,1,size,f);
    fclose(f);

    // decode ogg
    // if fails it will store lib_sample_data[samples]=0
    XOgg(samples, data, size);

    free(data);

    id = samples++;

    // add to hashmap and return new id
    SampleHash* item = (SampleHash*)malloc(sizeof(SampleHash)+len);
    SampleHash** base = sample_hash + (hash&HASH_MAKS);
    item->next = *base;
    *base = item;
    item->hash = hash;
    item->id = id;
    strcpy(item->name,name);

    return id;
    #endif
}

static const char* sample_names[] = // IN ORDER OF enum AUDIO_FILE
{
    "forest.ogg",
    "footsteps.ogg",
    "jump.ogg",
    "attack.ogg",
    "hurt.ogg",
    "die.ogg",
    0
};

static int sample_ids[AUDIO_FILES] = {-1};

// WHY directory scan at init: Pre-load all .ogg files into memory for zero-latency playback.
// Audio callbacks run on separate thread with strict timing requirements. Disk I/O during
// callback would cause glitches. Pre-loading ensures all samples ready for immediate playback.
static void LoadAllSamples()
{
    int loaded = 0, failed = 0;
    for (int i=0; sample_names[i]; i++)
    {
        sample_ids[i] = LoadSample(sample_names[i]);
        if (sample_ids[i] >= 0) loaded++; else failed++;
    }
    // FL-4145: summary of LoadAllSamples; visible on stderr.
    fprintf(stderr, "[AUDIO] LoadAllSamples: loaded=%d failed=%d total_samples_counter=%d\n",
            loaded, failed, samples);

    // Forest ambient is intentionally not started here. ReadConf() owns applying
    // persisted mute, then AudioMute(false) starts ambient after config is known.
    fprintf(stderr, "[AUDIO] LoadAllSamples: forest ambient deferred until mute preference is applied\n");
}

int GetSampleID(AUDIO_FILE af)
{
    if (af<0 || af>=AUDIO_FILES)
        return -1;
    return sample_ids[af];
}

extern "C" void AudioRestoreForestAmbient()
{
    int forest_id = GetSampleID(FOREST);
    fprintf(stderr, "[AUDIO] AudioRestoreForestAmbient: FOREST sample id=%d\n", forest_id);
    CallAudio((uint8_t*)&forest_id, 4);
}
#endif // END OF NOT WORKLET

struct PlyTrack
{
    int sample_id;
    int sample_vol;
    int sample_pos;
    int sample_end;
};

static PlyTrack ply_track[PLY_TRACKS] = {-1};

static int ply_forest_id = -1;
static int32_t volume = 32768;
static int audio_diag_cmd_calls = 0;
static int audio_diag_track_cmds = 0;
static int audio_diag_volume_cmds = 0;
static int audio_diag_forest_cmds = 0;
static int audio_diag_cb_calls = 0;
static int audio_diag_nonzero_cb = 0;
static int audio_diag_last_peak = 0;
static int audio_diag_debug_jump_calls = 0;
static int audio_diag_debug_jump_sample_id = -999;
static int audio_diag_debug_jump_before_cmds = 0;
static int audio_diag_debug_jump_after_cmds = 0;
static int audio_diag_debug_jump_result = 0;

// WHY chunk calculation: 2*material+(foot&1) passed from AudioWalk() as msg[3].
// Maps to marker array index for material/foot-specific sound variation.
// WHY track selection: Round-robin through PLY_TRACKS (16 tracks) to support
// simultaneous footsteps (multiple characters walking).
// WHY marker lookup: If chunk index valid, sets sample_pos/sample_end to marker
// start/end positions. Enables playing sub-regions of sample without loading
// separate files for each material/foot combination.
void DriverAudioCmd(void* userdata, const uint8_t* data, int size)
{
    audio_diag_cmd_calls++;
    // testing samples on single track
    // { sample_id, volume }

    if (size==4)
    {
        audio_diag_forest_cmds++;
        // very first command
        ply_forest_id = *(int32_t*)data;
        return;
    }

    if (size==2)
    {
        audio_diag_volume_cmds++;
        // set volume
        volume = (int32_t)*(uint16_t*)data;
    }

    if (size>=12) // track, sample, vol
    {
        audio_diag_track_cmds++;
        const int* msg = (const int*)data;
        if (msg[0]>=0 && msg[0]<PLY_TRACKS)
        {
            PlyTrack* tr = ply_track + msg[0];
            tr->sample_id = msg[1];
            tr->sample_vol = msg[2];
            tr->sample_pos = 0;
            tr->sample_end = 0;

            if (tr->sample_id>=0 && tr->sample_id<MAX_SAMPLES)
                tr->sample_end = lib_sample_len[tr->sample_id];
            else
                tr->sample_id = -1;

            if (tr->sample_id >= 0)
            {
                if (size>=16 && msg[3]>=0)
                {
                    int32_t* marker = (int32_t*)(lib_sample_data[tr->sample_id] + 2*tr->sample_end);
                    if (*marker > msg[3])
                    {
                        marker = marker + 1 + 2 * msg[3];
                        tr->sample_pos = marker[0];
                        tr->sample_end = marker[1];
                    }
                }
            }
        }
    }

    /////////////////////////////////////
    // animables:

    // pan \
    // rot  }-- 2x2 mix-matrix
    // vol /
    // frq

    // replace track sample
    // { 0, sample_id>=0, track_idx, play_from, play_to, loop_a, loop_b>=loop_a, loops, pan, rot, vol, frq, paused}

    // replace transition on track
    // { 1, track_idx, pan_to, rot_to, vol_to, frq_to, time_to }

    // subtract num of remaining loops (clamp to 0)
    // { 2, track_idx, sub_loops }

    // pause track
    // { 3, track_idx }

    // resume track
    // { 4, track_idx }

    // clear track
    // { 5, track_idx }

    // set event to listen if track finishes
    // { 6. track_idx, event_idx, add/remove/replace }

    // set event to listen track's pos & loops
    // { 7. track_idx, event_idx, pos, loops, add/remove/replace }

    // clear all event listenings (on given track or all tracks)
    // { 8. track_idx(-1==ALL), event_idx(-1==ALL) }

    // set event script, script is able to access all internals like sample position, current vol/pan, etc ...
    // { 9. event_idx, [script] (can be null to clear) } 

    // manually trigger event
    // {10. event_idx }

    // pause renderer (ALIAS!)
    // { 3, -1}

    // resume renderer (ALIAS!)
    // { 4, -1}

    // set renderer transition  (ALIAS!)
    // { 1, -1, pan_to, rot_to, vol_to, frq_to, time_to }


    // SCRIPT 'ASSEMBLY'

    // VAR i
    // VAR i=1
    // VAR i,j
    // VAR i=1,j
    // VAR i,j=2
    // VAR i=1,j=2
    // ...

    // FOR(i in TR)
    // FOR(i in EV)

    // TR[i] : sample_id,pos,from,to,loop_a,loop_b,loops,vol,pan,rot,frq
    // EV[i] : listens, listen[j]

    // i=VAL

}

// WHY summing with saturation: Prevent clipping when multiple tracks play simultaneously.
// Without saturation, summed values could overflow 16-bit range causing distortion.
// WHY int32_t accumulator: Provides headroom for 16 tracks. Each track contributes
// up to ±32767, so 16 tracks could sum to ±524272. int32_t range (±2147483647)
// prevents overflow during summation. Saturate final result to [-32767, +32767]
// before writing to int16_t buffer.
void DriverAudioCB(void* userdata, int16_t buffer[], int frames)
{
    audio_diag_cb_calls++;
    memset(buffer,0,4*frames);

    if (ply_forest_id>=0)
    {
        static int forest_pos = 0;
        int16_t* data = lib_sample_data[ply_forest_id];
        int pos = forest_pos;
        int end = lib_sample_len[ply_forest_id];
        for (int i = 0; i < frames; i++)
        {
            buffer[2*i] = (data[pos*2] * volume) >> 15;
            buffer[2*i+1] = (data[pos*2+1] * volume) >> 15;

            pos++;
            if (pos == end)
                pos=0;
        }
        forest_pos = pos;
    }

    for (int t=0; t<PLY_TRACKS; t++)
    {
        PlyTrack* tr = ply_track + t;
        if (tr->sample_id < 0)
            continue;
        const int16_t* data = lib_sample_data[tr->sample_id];
        int len = tr->sample_end; //lib_sample_len[tr->sample_id];
        int pos = tr->sample_pos;
        int vol = (tr->sample_vol * volume) >> 15;
        for (int i = 0; i < frames; i++)
        {
            if (pos==len)
            {
                tr->sample_id=-1;
                break;
            }

            int L = buffer[2*i] + (data[pos*2] * vol) / 65535;
            int R = buffer[2*i+1] + (data[pos*2+1] * vol) / 65535;

            if (L<-32767)
                L=-32767;
            if (L>+32767)
                L=+32767;

            if (R<-32767)
                R=-32767;
            if (R>+32767)
                R=+32767;

            buffer[2*i] = L;
            buffer[2*i+1] = R;
            pos++;
        }
        tr->sample_pos = pos;
    }

    int peak = 0;
    for (int i = 0; i < 2 * frames; i++)
    {
        int v = buffer[i];
        if (v < 0) v = -v;
        if (v > peak) peak = v;
    }
    audio_diag_last_peak = peak;
    if (peak > 0)
        audio_diag_nonzero_cb++;
}

extern "C" int AudioDebugPlayJump()
{
#ifndef WORKLET
    audio_diag_debug_jump_calls++;
    audio_diag_debug_jump_sample_id = GetSampleID(JUMP);
    audio_diag_debug_jump_before_cmds = audio_diag_cmd_calls;
    if (audio_diag_debug_jump_sample_id < 0)
    {
        audio_diag_debug_jump_result = -1;
        audio_diag_debug_jump_after_cmds = audio_diag_cmd_calls;
        return audio_diag_debug_jump_result;
    }
    AudioJump(65535, 0);
    audio_diag_debug_jump_after_cmds = audio_diag_cmd_calls;
    audio_diag_debug_jump_result = audio_diag_debug_jump_after_cmds > audio_diag_debug_jump_before_cmds ? 1 : -2;
    return audio_diag_debug_jump_result;
#else
    return -100;
#endif
}

extern "C" const char* AudioDebugStateJson()
{
    static char buf[1024];
    snprintf(buf, sizeof(buf),
             "{\"cmd_calls\":%d,\"track_cmds\":%d,\"volume_cmds\":%d,"
             "\"forest_cmds\":%d,\"cb_calls\":%d,\"nonzero_cb\":%d,"
             "\"last_peak\":%d,\"volume\":%d,\"forest_id\":%d,"
             "\"debug_jump_calls\":%d,\"debug_jump_sample_id\":%d,"
             "\"debug_jump_before_cmds\":%d,\"debug_jump_after_cmds\":%d,"
             "\"debug_jump_result\":%d,"
             "\"sample_count_registered\":%d,\"sample_id_forest\":%d,"
             "\"sample_id_footsteps\":%d,\"sample_id_jump\":%d,"
             "\"sample_id_attack\":%d,\"sample_id_hurt\":%d,\"sample_id_die\":%d}",
             audio_diag_cmd_calls,
             audio_diag_track_cmds,
             audio_diag_volume_cmds,
             audio_diag_forest_cmds,
             audio_diag_cb_calls,
             audio_diag_nonzero_cb,
             audio_diag_last_peak,
             (int)volume,
             ply_forest_id,
             audio_diag_debug_jump_calls,
             audio_diag_debug_jump_sample_id,
             audio_diag_debug_jump_before_cmds,
             audio_diag_debug_jump_after_cmds,
             audio_diag_debug_jump_result,
#ifndef WORKLET
             samples,
             GetSampleID(FOREST),
             GetSampleID(FOOTSTEPS),
             GetSampleID(JUMP),
             GetSampleID(ATTACK),
             GetSampleID(HURT),
             GetSampleID(DIE)
#else
             0, -1, -1, -1, -1, -1, -1
#endif
             );
    return buf;
}

/////////////////////////////////////

#ifndef EMSCRIPTEN

// WHY mutex queue (desktop): Audio callback runs on separate OS thread. Game code
// runs on main thread. Without synchronization, race conditions would corrupt data.
// Mutex-protected queue ensures thread-safe command passing from main thread to
// audio callback thread.
// for all native builds use mutex synced queue
#include <mutex>

static std::mutex call_mutex;
struct CallQueue
{
    CallQueue* next;
    int size;
    uint8_t data[1];
};

static CallQueue* call_head=0;
static CallQueue* call_tail=0;

static CallQueue* OnAudioCall()
{
    CallQueue* head;
    {
        std::lock_guard<std::mutex> guard(call_mutex);
        head = call_head;
        call_head = 0;
        call_tail = 0;
    }

    return head;
}

void CallAudio(const uint8_t* data, int size)
{
    CallQueue* cq = (CallQueue*)malloc(sizeof(CallQueue)+size-1);
    cq->next = 0;
    cq->size = size;
    memcpy(cq->data,data,size);

    {
        std::lock_guard<std::mutex> guard(call_mutex);
        if (call_tail)
            call_tail->next = cq;
        else
            call_head = cq;
        call_tail = cq;
    }
}
#endif

#ifdef __APPLE__
#ifndef HAS_AUDIO

// WHY CoreAudio (macOS): Lowest latency audio API on Apple platforms. AudioQueue
// provides simple callback interface with automatic buffer management. 3-buffer
// ring (NUM_BUFFERS=2 allocated + 1 in flight) minimizes latency while preventing
// underruns.
#include <AudioToolbox/AudioQueue.h>
#include <CoreAudio/CoreAudioTypes.h>

#define NUM_BUFFERS 2
#define BUFFER_SIZE (2048) // full latency 2x512 samples
static AudioQueueRef coreaudio_queue = 0;

void coreaudio_cb(void* userdata, AudioQueueRef queue, AudioQueueBufferRef buffer);

void FreeAudio()
{
    AudioQueueStop(coreaudio_queue, false);
    AudioQueueDispose(coreaudio_queue, false); // deletes its buffers as well
    coreaudio_queue = 0;
}

bool InitAudio()
{
    fprintf(stderr, "[AUDIO] InitAudio: CoreAudio path entry\n");
    LoadAllSamples();
    AudioStreamBasicDescription format;

    format.mSampleRate       = 44100;
    format.mFormatID         = kAudioFormatLinearPCM;
    format.mFormatFlags      = kLinearPCMFormatFlagIsSignedInteger | kAudioFormatFlagIsPacked;
    format.mBitsPerChannel   = 8 * sizeof(int16_t);
    format.mChannelsPerFrame = 2;
    format.mBytesPerFrame    = sizeof(int16_t) * 2;
    format.mFramesPerPacket  = 1;
    format.mBytesPerPacket   = format.mBytesPerFrame * format.mFramesPerPacket;
    format.mReserved         = 0;

    OSStatus status_new = AudioQueueNewOutput(&format, coreaudio_cb, 0, 0, kCFRunLoopCommonModes, 0, &coreaudio_queue);
    if (status_new)
    {
        fprintf(stderr, "[AUDIO] InitAudio: AudioQueueNewOutput failed status=%d\n", (int)status_new);
        return false;
    }

    for (int i = 0; i < NUM_BUFFERS; i++)
    {
        AudioQueueBufferRef buffer;
        OSStatus status_alloc = AudioQueueAllocateBuffer(coreaudio_queue, BUFFER_SIZE, &buffer);
        if (status_alloc)
        {
            fprintf(stderr, "[AUDIO] InitAudio: AudioQueueAllocateBuffer failed status=%d (buffer %d)\n", (int)status_alloc, i);
            AudioQueueDispose(coreaudio_queue, true);
            return false;
        }

        buffer->mAudioDataByteSize = BUFFER_SIZE; // why?
        coreaudio_cb(0, coreaudio_queue, buffer);
    }

    OSStatus status_start = AudioQueueStart(coreaudio_queue, 0);
    if (status_start)
    {
        fprintf(stderr, "[AUDIO] InitAudio: AudioQueueStart failed status=%d\n", (int)status_start);
        AudioQueueDispose(coreaudio_queue, true);
        return false;
    }
    fprintf(stderr, "[AUDIO] InitAudio: CoreAudio queue STARTED OK (NUM_BUFFERS=%d, BUFFER_SIZE=%d, rate=44100)\n", NUM_BUFFERS, BUFFER_SIZE);
    return true;
}

void coreaudio_cb(void* userdata, AudioQueueRef queue, AudioQueueBufferRef buffer)
{
    static int cb_count = 0;
    int16_t* buf = (int16_t*)buffer->mAudioData;
    int len = BUFFER_SIZE / (sizeof(int16_t)*2);

    CallQueue* qc = OnAudioCall();
    while (qc)
    {
        DriverAudioCmd(0,qc->data,qc->size);
        // free it
        CallQueue* n = qc->next;
        free(qc);
        qc = n;
    }

    DriverAudioCB(0, buf, len);

    // FL-4145: AudioQueue may reset mAudioDataByteSize between callbacks.
    // Tell CoreAudio how many valid bytes were written before re-enqueueing.
    buffer->mAudioDataByteSize = BUFFER_SIZE;

    if (cb_count < 3)
    {
        int nonzero = 0, peak = 0;
        for (int i = 0; i < len * 2; i++)
        {
            int v = buf[i];
            if (v < 0) v = -v;
            if (v > peak) peak = v;
            if (buf[i] != 0) nonzero++;
        }
        fprintf(stderr, "[AUDIO] coreaudio_cb #%d: len=%d frames, nonzero=%d/%d, peak=%d, byte_size_set=%u\n",
                cb_count, len, nonzero, len * 2, peak, (unsigned)buffer->mAudioDataByteSize);
    }
    cb_count++;

    AudioQueueEnqueueBuffer(queue, buffer, 0, 0);
}

#define HAS_AUDIO

#endif
#endif

#ifdef __linux__
#ifndef HAS_AUDIO

// WHY PulseAudio (Linux): Standard audio server on desktop Linux distributions.
// pa_threaded_mainloop runs event loop on separate thread with lock/signal
// coordination for thread-safe API calls. Alternative ALSA would require more
// complex buffer management and lacks automatic resampling/mixing.
#include <stdio.h>
#include <assert.h>
#include <pulse/pulseaudio.h>

#include <pthread.h>
#include <sched.h>

#define FORMAT PA_SAMPLE_S16LE
#define RATE 44100
#define CHANNELS 2

void context_state_cb(pa_context* context, void* mainloop);
void stream_state_cb(pa_stream *s, void *mainloop);
void stream_success_cb(pa_stream *stream, int success, void *userdata);
void stream_write_cb(pa_stream *stream, size_t requested_bytes, void *userdata);

static pa_threaded_mainloop *mainloop = 0;
static pa_mainloop_api *mainloop_api = 0;
static pa_context *context = 0;
static pa_stream *stream = 0;

void FreeAudio()
{
    if (mainloop)
    {
        pa_threaded_mainloop_stop(mainloop);
        if (context)
        {
            pa_context_disconnect(context);
            pa_context_unref(context);
            context = 0;
            if (stream)
            {
                pa_stream_unref(stream);
                stream = 0;
            }
        }
        pa_threaded_mainloop_free(mainloop);
        mainloop = 0;
    }
}

bool InitAudio() 
{
    LoadAllSamples();

    // Get a mainloop and its context
    mainloop = pa_threaded_mainloop_new();
    assert(mainloop);
    mainloop_api = pa_threaded_mainloop_get_api(mainloop);
    context = pa_context_new(mainloop_api, "pcm-playback");
    assert(context);

    // Set a callback so we can wait for the context to be ready
    pa_context_set_state_callback(context, &context_state_cb, mainloop);

    // Lock the mainloop so that it does not run and crash before the context is ready
    pa_threaded_mainloop_lock(mainloop);

    // Start the mainloop
    assert(pa_threaded_mainloop_start(mainloop) == 0);
    assert(pa_context_connect(context, NULL, PA_CONTEXT_NOAUTOSPAWN, NULL) == 0);

    // Wait for the context to be ready
    for(;;) 
    {
        pa_context_state_t context_state = pa_context_get_state(context);
        assert(PA_CONTEXT_IS_GOOD(context_state));
        if (context_state == PA_CONTEXT_READY) 
            break;
        pa_threaded_mainloop_wait(mainloop);
    }

    // Create a playback stream
    pa_sample_spec sample_specifications;
    sample_specifications.format = FORMAT;
    sample_specifications.rate = RATE;
    sample_specifications.channels = CHANNELS;

    pa_channel_map map;
    pa_channel_map_init_stereo(&map);

    stream = pa_stream_new(context, "Playback", &sample_specifications, &map);
    pa_stream_set_state_callback(stream, stream_state_cb, mainloop);
    pa_stream_set_write_callback(stream, stream_write_cb, mainloop);

    // recommended settings, i.e. server uses sensible values
    pa_buffer_attr buffer_attr; 

    int stress = 1; // max no glitch = 7
    buffer_attr.maxlength = 4096 >> stress;
    buffer_attr.tlength = 2048 >> stress;
    buffer_attr.prebuf = 1024 >> stress;
    buffer_attr.minreq = 1024 >> stress;
    buffer_attr.fragsize = (uint32_t)-1; // rec only

    // Settings copied as per the chromium browser source
    pa_stream_flags_t stream_flags;
    stream_flags = (pa_stream_flags_t)(PA_STREAM_START_CORKED /* | PA_STREAM_INTERPOLATE_TIMING | 
        PA_STREAM_NOT_MONOTONIC | PA_STREAM_AUTO_TIMING_UPDATE | PA_STREAM_ADJUST_LATENCY*/);

    // Connect stream to the default audio output sink
    assert(pa_stream_connect_playback(stream, NULL, &buffer_attr, stream_flags, NULL, NULL) == 0);

    // Wait for the stream to be ready
    for(;;) 
    {
        pa_stream_state_t stream_state = pa_stream_get_state(stream);
        assert(PA_STREAM_IS_GOOD(stream_state));
        if (stream_state == PA_STREAM_READY) 
            break;
        pa_threaded_mainloop_wait(mainloop);
    }

    pa_threaded_mainloop_unlock(mainloop);

    // Uncork the stream so it will start playing
    pa_stream_cork(stream, 0, stream_success_cb, mainloop);

    return true;
}

void context_state_cb(pa_context* context, void* mainloop) 
{
    pa_threaded_mainloop_signal((pa_threaded_mainloop*)mainloop, 0);
}

void stream_state_cb(pa_stream *s, void *mainloop) 
{
    pa_threaded_mainloop_signal((pa_threaded_mainloop*)mainloop, 0);
}

void stream_write_cb(pa_stream *stream, size_t requested_bytes, void *userdata) 
{
    int bytes_remaining = requested_bytes;
    while (bytes_remaining > 0) 
    {
        uint16_t *buffer = NULL;
        size_t bytes_to_fill = bytes_remaining;

        CallQueue* qc = OnAudioCall();
        while (qc)
        {
            DriverAudioCmd(0,qc->data,qc->size);
            // free it
            CallQueue* n = qc->next;
            free(qc);
            qc = n;
        }

        pa_stream_begin_write(stream, (void**) &buffer, &bytes_to_fill);

        int frames = bytes_to_fill/4;
        DriverAudioCB(0, (int16_t*)buffer, frames);

        pa_stream_write(stream, buffer, bytes_to_fill, NULL, 0LL, PA_SEEK_RELATIVE);

        bytes_remaining -= bytes_to_fill;
    }
}

void stream_success_cb(pa_stream *stream, int success, void *userdata) 
{
    return;
}

#define HAS_AUDIO

#endif
#endif

#ifdef USE_SDL
#ifndef HAS_AUDIO

// WHY SDL Audio: Cross-platform fallback for platforms without native audio APIs
// (e.g., Windows without DirectSound, BSD, etc.). SDL2 abstracts platform differences
// and provides consistent callback interface. Trade-off: slightly higher latency than
// native APIs but much better portability.
#ifdef _WIN32
#include <SDL.h>
#else
#include <SDL2/SDL.h>
#endif

void FreeAudio()
{
    SDL_CloseAudio();
}

void SDLAudioCB(void* userdata, Uint8* stream, int len)
{
    CallQueue* qc = OnAudioCall();
    while (qc)
    {
        DriverAudioCmd(0,qc->data,qc->size);
        // free it
        CallQueue* n = qc->next;
        free(qc);
        qc = n;
    }

    int frames = len/4;
    int16_t* buffer = (int16_t*)stream;
    DriverAudioCB(0, (int16_t*)buffer, frames);
}

bool InitAudio() 
{
    LoadAllSamples();

    SDL_AudioSpec wanted;
    wanted.freq = 44100;
    wanted.format = AUDIO_S16;
    wanted.channels = 2;
    wanted.samples = 1024;
    wanted.callback = SDLAudioCB;
    wanted.userdata = NULL;

    if (SDL_OpenAudio(&wanted,0) < 0)
        return false;

    SDL_PauseAudio(0);
    return true;
}

#define HAS_AUDIO

#endif
#endif

#ifdef EMSCRIPTEN
#include <emscripten.h>

#ifndef NO_AUDIO
// Full audio implementation for web

#ifdef WORKLET

// WHY AudioWorklet (web preferred): Runs on dedicated audio thread separate from
// main thread. Better performance and lower latency than ScriptNode. Requires
// AudioWorklet API (Chrome 66+, Firefox 76+).
static int16_t proc_buffer[2*128];
static uint8_t call_buffer[4096];

extern "C"
{
    uint8_t* Init(int num)
    {
        // num is snumber of samples we will be feeded with to decode
        return call_buffer;
    }

    int16_t* Proc()
    {
        DriverAudioCB(0, proc_buffer, 128);
        return proc_buffer;
    }

    void Call(uint8_t* data, int size)
    {
        DriverAudioCmd(0,data,size);
    }
}

#else

// WHY ScriptNode (web legacy): Fallback for browsers without AudioWorklet support.
// Runs on main thread (synchronous), higher latency, but wider compatibility.
// ScriptProcessorNode deprecated but still supported in all browsers.
static int audio_mode = 0;

extern "C"
{
    const int16_t* Audio(int frames)
    {
        static int16_t* buffer = 0;
        static int buflen = 0;

        if (!buffer)
        {
            int alloc = 2*frames;
            buffer = (int16_t*)malloc(4 * alloc);
            buflen = alloc;
        }
        else
        if(frames > buflen)
        {
            free(buffer);
            int alloc = 2*frames;
            buffer = (int16_t*)malloc(4 * alloc);
            buflen = alloc;
        }

        DriverAudioCB(0, buffer, frames);

        return buffer;
    }
}

// FL-810: called from JS catch handler when worklet addModule fails, to switch
// CallAudio() from the worklet-cache path to the direct DriverAudioCmd path.
extern "C" void SwitchToScriptProcessorMode(int sample_rate)
{
    audio_mode = sample_rate;
}

// FL-810: read back audio_mode so JS can verify the switch succeeded.
extern "C" int GetAudioMode()
{
    return audio_mode;
}

// WHY postMessage (AudioWorklet) vs synchronous (ScriptNode):
// AudioWorklet runs on separate thread, requires postMessage for commands.
// ScriptNode runs on main thread, can call DriverAudioCmd() directly.
// audio_mode determines which path to use (set by InitAudio return value).
void CallAudio(const uint8_t* data, int size)
{
    if (!audio_mode)
        return;

    if (audio_mode>0)
    {
        // SCRIPTNODE
        // just exec it right now and here
        DriverAudioCmd(0, data, size);
    }
    else
    {
        // WORKLET
        // copy data to new Uint8Array
        // send it to worklet's audio_port    
        EM_ASM(
        {
            if (audio_port)
            {
                let view = new Uint8Array(Module.HEAPU8.buffer, Module.HEAPU8.byteOffset + $0, $1);
                audio_port.postMessage(new Uint8Array(view));
            }
            else
            {
                // last call to volume is super-importand
                if ($1 == 2)
                {
                    let view = new Uint8Array(Module.HEAPU8.buffer, Module.HEAPU8.byteOffset + $0, $1);
                    audio_vol_cache = new Uint8Array(view);
                }

                // very first audio call is essencial
                // cache it
                if (!audio_call_cache)
                {
                    let view = new Uint8Array(Module.HEAPU8.buffer, Module.HEAPU8.byteOffset + $0, $1);
                    audio_call_cache = new Uint8Array(view);
                }
            }
        },data,size);
    }
}

void FreeAudio()
{
    EM_ASM(
    {
        if (audio_ctx)
            audio_ctx.close();

        audio_cb = null;
        audio_ctx = null;
        audio_node = null;
    });
}

bool InitAudio()
{
    int ret = EM_ASM_INT(
    {
        var _t0 = performance.now();
        var _dbg = !!window.__ak_debug_isolation_enabled;
        if (_dbg) console.log('[AK_AUDIO] InitAudio start t=' + _t0.toFixed(1));
        var audioContext = window.AudioContext || window.webkitAudioContext;

        audio_cb = Module.cwrap("Audio", "number", ["number"]);

        if (!audioContext || !audio_cb)
        {
            if (_dbg) console.log('[AK_AUDIO] InitAudio FAIL: no AudioContext=' + !!audioContext + ' no audio_cb=' + !!audio_cb);
            return 0;
        }

        // FL-810: reuse pre-created context if StartGame() already created it inside user gesture.
        // If audio_ctx is null here we are outside any user gesture (called from Load()->Main()
        // via WebSocket callback) and the new context will start suspended.
        if (!audio_ctx)
        {
            audio_ctx = new audioContext({sampleRate: 44100, latencyHint: "interactive"});
            if (_dbg) console.log('[AK_AUDIO] InitAudio: created new AudioContext state=' + (audio_ctx ? audio_ctx.state : 'null'));
        }
        else
        {
            if (_dbg) console.log('[AK_AUDIO] InitAudio: reusing pre-created AudioContext state=' + audio_ctx.state);
        }
        if (!audio_ctx)
        {
            if (_dbg) console.log('[AK_AUDIO] InitAudio FAIL: AudioContext constructor returned null');
            return 0;
        }
        if (_dbg) console.log('[AK_AUDIO] AudioContext state=' + audio_ctx.state + ' sampleRate=' + audio_ctx.sampleRate);
        if (window.AK_AUDIO_DIAG) window.AK_AUDIO_DIAG.init_t = _t0;

        let samples = [];
        const enc = new TextEncoder();
        const assets = FS.root.contents["assets"];
        const c = assets.contents["samples"].contents;

        let Sample = Module.cwrap("Sample", null, ["string"]);
        let i = 0;
        let max_size = 0;
        for (const s in c)
        {
            if (max_size < c[s].contents.length)
                max_size = c[s].contents.length;
            samples[i++] = c[s].contents;
            Sample(s);
        }
        if (_dbg) console.log('[AK_AUDIO] sample_count=' + i + ' max_ogg_bytes=' + max_size + ' worklet_api=' + !!audio_ctx.audioWorklet);
        if (window.AK_AUDIO_DIAG) window.AK_AUDIO_DIAG.sample_count = i;

        function installScriptProcessorFallback(reason)
        {
            if (!audio_ctx || !audio_cb)
                return;
            if (audio_node && audio_node.disconnect) {
                try { audio_node.disconnect(); } catch (_e) {}
            }
            audio_port = null;
            var _sp_mode_ok = false;
            try {
                Module.ccall('SwitchToScriptProcessorMode', null, ['number'], [audio_ctx.sampleRate | 0]);
                var _verifiedMode = Module.ccall('GetAudioMode', 'number', [], []);
                if (_dbg) console.log('[AK_AUDIO] SP mode switch: audio_mode=' + _verifiedMode);
                if (window.AK_AUDIO_DIAG) window.AK_AUDIO_DIAG.audio_mode = _verifiedMode;
                _sp_mode_ok = (_verifiedMode > 0);
                if (!_sp_mode_ok) console.warn('[AK_AUDIO] WARN: audio_mode=' + _verifiedMode + ' after SP switch (expected >0)');
            } catch(_e) {
                console.error('[AK_AUDIO] SwitchToScriptProcessorMode FAILED: ' + _e);
                if (window.AK_AUDIO_DIAG) window.AK_AUDIO_DIAG.sp_switch_error = String(_e);
            }
            audio_node = audio_ctx.createScriptProcessor(1024, 0, 2);
	            audio_node.onaudioprocess = function(ev)
	            {
	                var nframes = ev.outputBuffer.length;
	                var audio_ptr = audio_cb(nframes);
	                var idx = audio_ptr >> 1;
	                var heap16 = Module.HEAP16 || (Module.HEAPU8 ? new Int16Array(Module.HEAPU8.buffer) : null);
	                if (!heap16)
	                {
	                    if (window.AK_AUDIO_DIAG)
	                        window.AK_AUDIO_DIAG.sp_output_error = 'missing_heap16';
	                    return;
	                }
	                var left = ev.outputBuffer.getChannelData(0);
	                var right = ev.outputBuffer.getChannelData(1);
	                const norm = 1.0/32767;
	                var peak = 0;
	                for (var i = 0; i < nframes; i++)
	                {
	                    var l = heap16[idx + 2*i];
	                    var r = heap16[idx + 2*i + 1];
	                    left[i] = l * norm;
	                    right[i] = r * norm;
	                    var al = Math.abs(l);
	                    var ar = Math.abs(r);
                    if (al > peak) peak = al;
                    if (ar > peak) peak = ar;
                }
                if (window.AK_AUDIO_DIAG) {
                    window.AK_AUDIO_DIAG.worklet_proc_calls++;
                    window.AK_AUDIO_DIAG.worklet_last_peak = peak;
                    if (peak > 0) window.AK_AUDIO_DIAG.worklet_nonzero_proc++;
                }
            };
            let sp_data = 0;
            if (max_size) sp_data = Module._malloc(max_size);
            let sp_idx = 0;
            let SPXOgg = Module.cwrap('XOgg', null, ['number','number','number']);
            for (const s in c)
            {
                if (c[s].contents.length)
                    Module.HEAPU8.set(c[s].contents, sp_data);
                SPXOgg(sp_idx, sp_data, c[s].contents.length);
                sp_idx++;
            }
            if (max_size) Module._free(sp_data);
            audio_node.connect(audio_ctx.destination);
            audio_ctx.resume();
            audio_call_cache = null;
            audio_vol_cache = null;
            // FL-810: re-init forest ambient — initial command was cached to worklet path
            // before fallback, so DriverAudioCmd never set ply_forest_id.
            if (_sp_mode_ok) {
                try {
                    Module.ccall('AudioRestoreForestAmbient', null, [], []);
                    if (_dbg) console.log('[AK_AUDIO] AudioRestoreForestAmbient called');
                } catch(_e) {
                    console.error('[AK_AUDIO] AudioRestoreForestAmbient FAILED: ' + _e);
                }
            }
            if (window.AK_AUDIO_DIAG) {
                window.AK_AUDIO_DIAG.backend = reason || 'script_processor_fallback';
                window.AK_AUDIO_DIAG.worklet_ok = false;
                window.AK_AUDIO_DIAG.worklet_err = reason || null;
            }
            if (window.__ak_debug_isolation_enabled) console.log('[AK_AUDIO] ScriptProcessor fallback READY reason=' + reason + ' ctx=' + audio_ctx.state);
            if (window.UpdateAudioDiagPanel) window.UpdateAudioDiagPanel();
        }

        // FL-810: candidate Chrome/phone path can construct AudioWorkletNode but never
        // run process() before gameplay/manual effect commands arrive. Those commands
        // are posted to the dead worklet and are not replayed after fallback. Use the
        // single backend that has produced nonzero PCM in candidate artifacts.
        installScriptProcessorFallback('script_processor_forced_fl810');
        return audio_ctx.sampleRate | 0;

        if (audio_ctx.audioWorklet)
        {
            if (window.AK_AUDIO_DIAG) window.AK_AUDIO_DIAG.backend = 'worklet_pending';
            if (window.UpdateAudioDiagPanel) window.UpdateAudioDiagPanel();
            audio_ctx.audioWorklet.addModule('audio.js').then(
            function(e)
            {
                let cfg =
                {
                    numberOfInputs:0,
                    numberOfOutputs:1,
                    outputChannelCount:[2],
                    processorOptions : samples
                };

                audio_node = new AudioWorkletNode(audio_ctx, 'asciicker-audio',cfg);
                audio_port = audio_node.port;
                audio_port.onmessage = function(e)
                {
                    if (e.data && e.data.ak_audio_diag && window.AK_AUDIO_DIAG)
                    {
                        var d = e.data.ak_audio_diag;
                        if (d.cmd_calls !== undefined) window.AK_AUDIO_DIAG.worklet_cmd_calls = d.cmd_calls;
                        if (d.proc_calls !== undefined) window.AK_AUDIO_DIAG.worklet_proc_calls = d.proc_calls;
                        if (d.nonzero_proc !== undefined) window.AK_AUDIO_DIAG.worklet_nonzero_proc = d.nonzero_proc;
                        if (d.last_peak !== undefined) window.AK_AUDIO_DIAG.worklet_last_peak = d.last_peak;
                        if (d.state !== undefined) window.AK_AUDIO_DIAG.worklet_state_json = d.state;
                        if (window.UpdateAudioDiagPanel) window.UpdateAudioDiagPanel();
                    }
                    else if (window.__ak_debug_isolation_enabled)
                    {
                        console.log(e.data, performance.now());
                    }
                };

                audio_node.connect(audio_ctx.destination);

                audio_ctx.resume();

                if (window.AK_AUDIO_DIAG) { window.AK_AUDIO_DIAG.backend = 'worklet'; window.AK_AUDIO_DIAG.worklet_ok = true; }
                if (window.__ak_debug_isolation_enabled) console.log('[AK_AUDIO] Worklet READY ctx=' + audio_ctx.state + ' t=' + performance.now().toFixed(1));
                if (window.UpdateAudioDiagPanel) window.UpdateAudioDiagPanel();

                setTimeout(function() {
                    var d = window.AK_AUDIO_DIAG || {};
                    if (audio_node && audio_node.constructor &&
                        audio_node.constructor.name === 'AudioWorkletNode' &&
                        (!d.worklet_proc_calls || d.worklet_proc_calls <= 0))
                    {
                        installScriptProcessorFallback('worklet_no_process_sp_fallback');
                    }
                }, 1000);

                if (audio_vol_cache)
                {
                    audio_port.postMessage(audio_vol_cache);
                    audio_vol_cache = null;
                }

                if (audio_call_cache)
                {
                    audio_port.postMessage(audio_call_cache);
                    audio_call_cache = null;
                }

            }).catch(function(err)
            {
                if (window.AK_AUDIO_DIAG) { window.AK_AUDIO_DIAG.backend = 'worklet_failed_sp_fallback'; window.AK_AUDIO_DIAG.worklet_ok = false; window.AK_AUDIO_DIAG.worklet_err = String(err); }
                if (window.__ak_debug_isolation_enabled) console.log('[AK_AUDIO] Worklet addModule FAILED: ' + err + ' — falling back to ScriptProcessor');
                if (window.UpdateAudioDiagPanel) window.UpdateAudioDiagPanel();
                installScriptProcessorFallback('worklet_failed_sp_fallback');
            });

            return ~(audio_ctx.sampleRate | 0);
        }
        else
        {
            if (window.AK_AUDIO_DIAG) window.AK_AUDIO_DIAG.backend = 'script_processor';
            if (_dbg) console.log('[AK_AUDIO] no audioWorklet API — using ScriptProcessor fallback');
            var bufsize = 1024;
            audio_node = audio_ctx.createScriptProcessor(bufsize, 0, 2);

	            audio_node.onaudioprocess = function(ev)
	            {
	                var samples = ev.outputBuffer.length;

	                var audio_ptr = audio_cb(samples);

	                var idx = audio_ptr >> 1;
	                var heap16 = Module.HEAP16 || (Module.HEAPU8 ? new Int16Array(Module.HEAPU8.buffer) : null);
	                if (!heap16)
	                {
	                    if (window.AK_AUDIO_DIAG)
	                        window.AK_AUDIO_DIAG.sp_output_error = 'missing_heap16';
	                    return;
	                }

	                var left = ev.outputBuffer.getChannelData(0);
	                var right = ev.outputBuffer.getChannelData(1);

	                const norm = 1.0/32767;

	                for (var i=0; i<samples; i++)
	                {
	                    left[i] = heap16[idx + 2*i] * norm;
	                    right[i] = heap16[idx + 2*i + 1] * norm;
	                }
	            };

            audio_node.connect(audio_ctx.destination);

            let data = 0;
            if (max_size)
                data = Module._malloc(max_size);

            var _t_dec = performance.now();
		    let XOgg = Module.cwrap('XOgg', null, ['number','number','number']);
	        let s_idx = 0;
	            for (const s in c)
	            {
	                if (c[s].contents.length)
	                    Module.HEAPU8.set(c[s].contents, data);
                XOgg(s_idx, data, c[s].contents.length);
                s_idx++;
            }
            if (_dbg) console.log('[AK_AUDIO] ScriptProcessor XOgg sync decode done samples=' + s_idx + ' ms=' + (performance.now()-_t_dec).toFixed(1) + ' (main-thread block)');

            if (max_size)
                Module._free(data);

            audio_ctx.resume();
            if (window.AK_AUDIO_DIAG) { window.AK_AUDIO_DIAG.backend = 'script_processor'; window.AK_AUDIO_DIAG.worklet_ok = false; }
            if (_dbg) console.log('[AK_AUDIO] ScriptProcessor READY ctx=' + audio_ctx.state + ' rate=' + audio_ctx.sampleRate);
            if (window.UpdateAudioDiagPanel) window.UpdateAudioDiagPanel();
            return audio_ctx.sampleRate | 0;
        }
    });

    audio_mode = ret;
    LoadAllSamples();
    return ret!=0;
}

#define HAS_AUDIO
#endif // WORKLET
#endif // !NO_AUDIO

#ifdef NO_AUDIO
// Audio disabled stubs for web build
void CallAudio(const uint8_t* data, int size) {}
void FreeAudio() {}
bool InitAudio() { return false; }
extern "C" {
    const int16_t* Audio(int frames) { return nullptr; }
}
#define HAS_AUDIO
#endif // NO_AUDIO

#endif // EMSCRIPTEN

#ifndef HAS_AUDIO
/*
void CallAudio(const uint8_t* data, int size)
{
}
*/

void FreeAudio()
{
}

bool InitAudio()
{
	return false;
}

#endif
