// game_utility.h — config, path, environment helper declarations
// Extracted from game.cpp alongside game_utility.cpp.
#pragma once

#include <stdint.h>
#include <stdio.h>

struct Game;
struct PhysicsIO;
struct GameSession;
struct UiState;

extern char player_name[32 * 4];
extern char player_name_cp437[32];
extern char g_requested_a3d_path[1024];

float ReadEnvFloatOrDefault(const char* name, float fallback);
void GetDefaultGameStart(float* water, float pos[3], float* yaw, float* dir, float lt[4]);
uint8_t ConvertToCP437(uint32_t uc);
void ConvertToCP437(char* cp437, const char* utf8, int maxlen);
const char* A3dTitleMapPath();
void ReadGitCodestateLabel(char* out, int out_size);
bool IsAbsoluteA3dPath(const char* path);
const char* ResolveRequestedA3dPath(char* out, int out_size, const char* base_path);
void BuildGameTermTitle(char* out, int out_size);
uint32_t GetMultiplayerWorldSeed();
void SetMultiplayerWorldSeed(uint32_t seed);
void ChatLog(const char* fmt, ...);
void WriteJsonString(FILE* f, const char* str);
void WriteShotJson(const char* path, uint64_t stamp, const PhysicsIO* io, const Game* g, int width, int height);
bool AutoShotFlagPath(char* out, int out_size);
bool AutoShotFlagPresent();
void ConsumeAutoShotFlag();
bool AutoShotOnFirstFrameEnabled();

// Observe-render harness (Plan 004 / RQ-11 Phase C):
// - Must be driven by an explicit CLI flag, not a hidden flag file.
// - Outputs source artifacts used by Godot parity scripts.
void ConfigureObserveRender(const char* output_dir, const char* view_tuple_json_path, const char* schema_version);
bool ObserveRenderEnabled();
const char* ObserveRenderOutputDir();
const char* ObserveRenderViewTuplePath();
const char* ObserveRenderSchemaVersion();
void ReadConf(Game* g);
void WriteConf(Game* g);
void WriteConf(GameSession& session, UiState& ui);
