#pragma once

struct Game;
struct Human;
struct Server;

enum WebRecorderBridgeMode
{
    WEB_RECORDER_BRIDGE_MODE_FULL = 0,
    WEB_RECORDER_BRIDGE_MODE_MINIMAL = 1,
    WEB_RECORDER_BRIDGE_MODE_NONE = 2,
};

struct WebRecorderBridgeInputs
{
    const Game* game;
    const Server* live_server;
    const Server* snapshot_server;
    const Human* remote0_appearance;
};

struct WebRecorderBridgeStats
{
    int mode;
    int field_count;
    int bytes_appended;
    int publish_duration_us;
};

WebRecorderBridgeMode WebRecorderBridgeClampMode(int raw_mode);

WebRecorderBridgeStats WebRecorderBridgeAppendMountedProofFields(
    char* buf,
    int cap,
    int& used,
    WebRecorderBridgeMode mode,
    const WebRecorderBridgeInputs& inputs);

// Build full recorder state JSON into buf starting from index 0.
// Uses submodule APIs (web_diagnostics, web_filesystem, web_network_client)
// for data access. Caller must provide game and server pointers.
// Returns pointer to buf.
const char* BuildRecorderStateJson(
    char* buf, int cap,
    const Game* game,
    const Server* server,
    const Server* alloc_server);

// FL-4079: atomic wearable-proof probe. Returns a single JSON object joining
// server-equipped truth, renderer-selected fields, render-buffer hash/seq, and
// ROI metadata for one actor (0 = local). The single-call shape is what
// prevents two-call races across Render(): probe_seq + render fields + ROI are
// all sampled within one C entry-point, holding the engine in a consistent
// frame.
//
// GREEN-1 of FL-4079 lands the server-truth + render-selection + probe_seq
// fields. The expected_armor_cells array and ROI raw cells are stubbed and
// land in GREEN-2 with the per-cell render trace.
const char* BuildActorWearableProofProbeJson(
    char* buf, int cap,
    const Game* game,
    const Server* server,
    int actor);
