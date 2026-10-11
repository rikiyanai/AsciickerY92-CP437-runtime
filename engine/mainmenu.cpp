// ============================================================================
// MAINMENU.CPP - Stack-Based Hierarchical Menu System
// ============================================================================
//
// PURPOSE:
// Multi-platform main menu system with stack-based hierarchical navigation,
// smooth scrolling, level loading flow, and background rendering. Handles
// keyboard, mouse, touch, and gamepad input with unified state machine.
//
// ARCHITECTURE:
//
// 1. MENU STATE MACHINE (Stack-Based Hierarchical Navigation)
//    - State Variables:
//      * menu_depth: int, range [-1, 3]
//        -1 = menu closed (not currently used - menu always visible)
//         0 = root menu level
//         1 = first submenu level
//         2 = second submenu level
//         3 = third submenu level (maximum depth)
//      * menu_stack[4]: int array storing selected item index at each depth
//        menu_stack[menu_depth] = currently highlighted menu item
//      * menu_temp: int, stores keyboard/gamepad highlight when mouse takes over
//
//    - WHY max depth 4:
//      * Prevents deep menu nesting (UX simplicity - users get lost in deep trees)
//      * Fixed-size array allocation (no dynamic memory, stack-friendly)
//      * Sufficient for typical game menu hierarchy (root -> category -> subcategory -> action)
//
//    - State Transition Diagram:
//      depth=-1 (closed - unused in current implementation)
//           | [ESC/Show]
//           v
//      depth=0 (root menu)
//           | [UP/DOWN] navigate items
//           | menu_stack[0] tracks selected index
//           | [ENTER/RIGHT on submenu item]
//           v
//      depth=1 (submenu)
//           | [UP/DOWN] navigate items
//           | menu_stack[1] tracks selected index
//           | [ENTER/RIGHT on submenu item]
//           v
//      depth=2 (sub-submenu)
//           | [UP/DOWN] navigate items
//           | menu_stack[2] tracks selected index
//           | [ENTER on action item]
//           v
//      Execute action (e.g., start_new_game)
//           ^
//           | [ESC/LEFT at any depth > 0]
//      Pop level (menu_depth--, restore menu_stack[menu_depth] from menu_temp)
//
//    [FLOW:ENTITY] State transitions occur at: menu open, navigate up/down,
//    submenu enter (depth++), back/pop (depth--), action execute, ESC to root
//
// 2. MENU STRUCTURE (Static Data-Driven Definition)
//    struct MainMenu {
//        const char* str;           // Display string (NULL = terminator)
//        const MainMenu* sub;       // Submenu array (NULL = leaf item)
//        void (*action)(MainMenuContext*); // Action callback (NULL = no action)
//        bool (*getter)(MainMenuContext*); // State getter for toggles (NULL = no state)
//        void* cookie;              // User data for action (e.g., level manifest)
//    };
//
//    WHY static const arrays:
//    - Menu structure defined at compile time (no runtime allocation)
//    - Enables data-driven menu definition (easy to add/modify items)
//    - Supports hierarchical nesting via recursive sub-pointers
//    - Terminator convention (str=NULL) simplifies iteration
//
// 3. INPUT HANDLING (Multi-Platform)
//    Supported input methods:
//    - Keyboard: arrow keys, enter, escape, backspace
//    - Mouse: left button, move, hover
//    - Touch: begin, move, end, cancel
//    - Gamepad: buttons, analog sticks
//
//    Input State Management:
//    - menu_down: 0=released, 1=mouse_captured, 2=touch_captured
//      WHY: Prevents input conflicts (e.g., mouse shouldn't interfere with active touch)
//    - menu_temp: stores keyboard/gamepad highlight when mouse takes over
//      WHY: When user hovers mouse, menu_stack[depth] becomes -1 (no keyboard highlight),
//           but we preserve the previous keyboard position in menu_temp so when they
//           press arrow keys again, highlight restores to last keyboard position
//
//    Input Priority:
//    1. Touch has highest priority (if touch active, mouse ignored)
//    2. Mouse second (if mouse active, keyboard highlight hidden)
//    3. Keyboard/gamepad share state (both use menu_stack[depth] directly)
//
// 4. SMOOTH SCROLLING
//    - menu_scroll: target scroll position (discrete, jumps on navigation)
//    - menu_smooth_scroll: interpolated scroll position (smooth animation)
//    - Interpolation: simple ±1 per frame approach
//      if (menu_smooth_scroll < menu_scroll) menu_smooth_scroll++;
//      if (menu_smooth_scroll > menu_scroll) menu_smooth_scroll--;
//
//    WHY NOT 0.2 coefficient exponential decay here:
//    The ±1 approach provides linear interpolation (~16ms per pixel at 60 FPS),
//    creating predictable, frame-rate-independent scrolling. For small scroll
//    distances (typical in menus), this is simpler and sufficient.
//
//    NOTE: scroll system is frame-rate dependent (assumes ~60 FPS). At lower
//    frame rates, scrolling will be slower. At higher rates, faster.
//
//    - menu_rescroll: flag set after keyboard/gamepad navigation to trigger
//      auto-scroll logic (ensures highlighted item is visible on screen)
//
// 5. LEVEL LOADING FLOW (Async State Machine)
//    game_loading state machine:
//    - 0 = not loaded (no level loaded, show_continue = false)
//    - 1 = loading (LoadGame() in progress, render "LOADING" screen with progress dots)
//    - 2 = loaded (level fully loaded, show_continue = true, ready to resume)
//
//    Loading Progress Tracking:
//    - mainmenu_context.progress: coarse progress steps [0-3]
//      0 = fully loaded (triggers game_loading = 2 transition)
//      1 = initialization
//      2 = loading patches (patch_iter / patch_num for fine-grained progress)
//      3 = finalization
//
//    WHY progress counts DOWN from 3 to 0:
//    Simplifies completion detection (if progress == 0 -> done). LoadGame()
//    decrements progress as it completes stages.
//
//    Loading UI:
//    - Renders "LOADING" text at top
//    - Progress dots (10 dots total) fill from left to right
//    - Gold dots = completed, grey dots = remaining
//    - During patch loading (progress==2), dots fill proportionally based on
//      patch_iter / patch_num ratio
//
//    [FLOW:ENTITY] Loading state transitions: game_loading 0->1 (start),
//    progress 3->2->1->0 (stages), game_loading 1->2 (complete)
//
// 6. BACKGROUND RENDERING
//    - Half-tone dithering: menu_bk_img scaled with aspect-correct cropping
//    - Dither transition: mainmenu_dither counter animates menu appearance
//      (decrements from mainmenu_dither_hidden*2 to 0 at 60 FPS)
//    - Logo sprite: Asciicker logo blitted at calculated position
//    - Character sprites: Wolfie + player sprites blitted in background
//
//    WHY SetSpriteDither(mainmenu_dither>>1):
//    Right-shift by 1 divides dither value by 2, creating gradual fade-in effect.
//    As mainmenu_dither decrements from 40->0, sprites transition from fully
//    dithered (transparent) to solid (opaque).
//
// 7. KEY FUNCTIONS
//    - LoadMainMenuSprites(): Load background image, generate half-tone palette
//    - FreeMainMenuSprites(): Release allocated resources
//    - MainMenu_Render(): Main rendering entry point (called every frame)
//    - MainMenu_Show(): Open menu (currently unused - menu always visible)
//    - MainMenu_OnKeyb(): Keyboard input handler (arrow keys, enter, escape)
//    - MainMenu_OnMouse(): Mouse input handler (button, move, hover)
//    - MainMenu_OnTouch(): Touch input handler (begin, move, end, cancel)
//    - MainMenu_OnPadButton(): Gamepad button handler
//    - MainMenu_OnPadAxis(): Gamepad analog stick handler
//    - MainMenuContext::OnKeyb(): Internal keyboard handler (state machine logic)
//    - MainMenuContext::OnMouse(): Internal mouse handler (hit testing, capture)
//    - MainMenuContext::OnTouch(): Internal touch handler (hit testing, capture)
//    - MainMenuContext::Paint(): Render menu items with scrolling
//    - MainMenuContext::HitMenu(): Hit test screen coordinates against menu items
//    - ResetGame(): Reset game state before loading new level
//    - start_new_game(): Action callback to initiate level loading
//    - LoadGame(): Async level loading function (called from MainMenu_Render)
//
// 8. INTEGRATION POINTS
//    - game.cpp: Game struct, game->main_menu flag
//    - game_app.cpp: Platform entry point (native desktop)
//    - game_web.cpp: Platform entry point (Emscripten/WebAssembly)
//    - font1.cpp: Font rendering (Font1Paint, Font1Size, Font1UnderLine)
//    - sprite.cpp: Sprite rendering (BlitSprite, SetSpriteDither)
//    - gamepad.cpp: Virtual gamepad rendering (PaintGamePad)
//
// CRITICAL INVARIANTS:
// - menu_depth must stay in range [-1, 3]
// - menu_stack[depth] must be valid index into current menu level
// - menu_down states (0/1/2) are mutually exclusive
// - game_loading states (0/1/2) form strict state machine
// - menu_temp always holds last valid keyboard/gamepad highlight
//
// ============================================================================

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "mainmenu.h"
#include "a3d_load_context.h"
#include "game_utility.h"
#include "enemygen.h"
#include "fast_rand.h"
#include "font1.h"
#include "gamepad.h"
#include "material_glyph_plane.h"
#include "material_sidecar.h"
#include "platform/input_backend.h"

#include "audio.h"

#include "upng.h"

extern Game* game;
extern Terrain* terrain;
extern World* world;
extern Material mat[256];

static int ApplyMainMenuMaterialGlyphCell(void* user, int material_id, int elev, int shade, GlyphId glyph_id, uint16_t coverage)
{
	Material* materials = (Material*)user;
	if (!materials || material_id < 0 || material_id >= 256 || elev < 0 || elev >= 4 || shade < 0 || shade >= 16)
		return 1;
	if (!materials[material_id].glyph_plane)
	{
		materials[material_id].glyph_plane = material_glyph_plane_alloc();
		if (!materials[material_id].glyph_plane)
			return 1;
		material_glyph_plane_init(materials[material_id].glyph_plane);
	}
	const int idx = elev * 16 + shade;
	materials[material_id].glyph_plane->cells[idx] = glyph_id;
	materials[material_id].glyph_plane->coverage[idx] = coverage;
	return 0;
}

static bool LoadMainMenuMaterialGlyphSidecar(const char* map_path, Material* materials)
{
	char errbuf[512] = "";
	int applied_cells = 0;
	if (material_sidecar_load_apply_for_map(map_path, ApplyMainMenuMaterialGlyphCell, materials, "[LOAD]", &applied_cells, errbuf, sizeof(errbuf)) != 0)
		return false;
	if (applied_cells > 0)
		printf("[LOAD] Runtime material glyph sidecar applied cells=%d\n", applied_cells);
	return true;
}

#if !defined(__EMSCRIPTEN__)
// Native single-player session lifecycle is owned by game_app.cpp.
bool EnsureNormalGameAuthoritativeSession(const char* user, const char* map_path);
void StopNormalGameAuthoritativeSession();
#else
extern "C" bool WebAuthoritativeJoinActive();
extern "C" void WebFlushPendingNetPacketsToServer();
extern "C" void WebFL933ServerPointerWatch(const char* stage,
                                           const void* game_ptr,
                                           uint32_t game_size,
                                           const void* observed_player_head,
                                           const void* observed_player_tail);
extern "C" int WebFL933AssertAuthoritativeServerPresent(const char* stage,
                                                        const void* game_ptr,
                                                        uint32_t game_size,
                                                        const void* observed_player_head,
                                                        const void* observed_player_tail);
extern Character* player_head;
extern Character* player_tail;
#endif

static int  game_loading = 0; // 0-not_loaded, 1-loading, 2-loaded
static bool show_continue = false;
static bool show_gamepad = false;

static uint64_t mainmenu_stamp = 0;
static uint64_t dither_stamp = 0;
static bool mainmenu_shot = false;
static const int mainmenu_dither_hidden = 20;
static int mainmenu_dither = mainmenu_dither_hidden * 2;


static bool MainMenuDebugEnabled()
{
	static int cached = -1;
	if (cached < 0)
	{
		const char* env = getenv("ASCIICKER_MENU_DEBUG");
		cached = (env && *env) ? 1 : 0;
	}
	return cached == 1;
}

static bool MainMenuReadyToEnterLoadedWorld()
{
	if (!server)
	{
#ifdef __EMSCRIPTEN__
		return !WebAuthoritativeJoinActive();
#else
		return true;
#endif
	}
	if (!game || !game->physics || !world || !terrain)
		return false;
	if (server->connection.local_id < 0)
		return false;
	if (server->authority.snapshot_client.last_snapshot_seq == 0 || server->authority.snapshot_client.last_snapshot_tick == 0)
		return false;
	return LocalPlayerAuthoritativePoseReady(game->player, server != nullptr);
}

static bool MainMenuAutoShotFlagPresent()
{
    if (ObserveRenderEnabled())
        return true;
	if (!base_path[0])
		return false;
	char flag_path[1200];
	snprintf(flag_path, sizeof(flag_path), "%s.run/auto-shot-on-first-frame.flag", base_path);
	FILE* flag = fopen(flag_path, "rb");
	if (!flag)
		return false;
	fclose(flag);
	return true;
}

static void WriteMainMenuShotJson(uint64_t stamp, int width, int height)
{
	char shot_path[1024 + 20];
	sprintf(shot_path, "%sshot.json", base_path);
	FILE* f = fopen(shot_path, "wb");
	if (!f)
		return;

	fprintf(f, "{\n");
	fprintf(f, "  \"version\": 1,\n");
	fprintf(f, "  \"stamp\": %llu,\n", (unsigned long long)stamp);
	fprintf(f, "  \"context\": \"main_menu\",\n");
	fprintf(f, "  \"size\": {\"width\": %d, \"height\": %d},\n", width, height);
	if (g_loaded_a3d_path[0])
	{
		fprintf(f, "  \"map_path\": ");
		WriteJsonString(f, g_loaded_a3d_path);
		fprintf(f, "\n");
	}
	else
		fprintf(f, "  \"map_path\": null\n");
	fprintf(f, "}\n");
	fclose(f);
}

////////////////////////////////////////
static uint32_t* xxx_table = 0;
static uint32_t  xxx_step = 0;
static uint32_t  xxx_offs = 0;
static uint32_t  xxx_size = 0;
static uint32_t  xxx_size2 = 0;

static uint16_t* menu_bk_img=0;
static int menu_bk_width=0;
static int menu_bk_height=0;

static const int pal_size = 216;
static uint8_t pal[pal_size][3] = {{0}};
static uint8_t half_tone[2][216][216][3] = {{{{0}}}};

static Sprite* menu_logo_sprite = 0;

struct MainMenuContext;
struct MainMenu
{
	const char* str; // if 0 this is terminator
	const MainMenu* sub; // for terminator this is back menu
	void (*action)(MainMenuContext* mmc);
	bool (*getter)(MainMenuContext* mmc);
    void* cookie;
};

static void ResetGame();
static const int MAINMENU_TERRAIN_DARK_PATCHES_PER_FRAME = 512;
// [FLOW:ENTITY] Action callback: initiate level loading (game_loading 0->1)
static void start_new_game(MainMenuContext* m)
{
    if (game_loading)
        ResetGame(); // WHY: if already loading/loaded, reset state before restarting
#ifdef DARK_TERRAIN
    CancelDeferredTerrainDarkBootstrap();
#endif
#if !defined(__EMSCRIPTEN__)
    char a3d_path[1024 + 20];
    ResolveRequestedA3dPath(a3d_path, sizeof(a3d_path), base_path);
    // WARNING (FL-2540): local single-player authority must boot the same map
    // artifact as the native client. Starting the repo-local server without the
    // selected A3D path revives collision/fall-through families because the
    // server stays on its default world while the client loads the chosen run.
    if (!EnsureNormalGameAuthoritativeSession(player_name, a3d_path))
    {
        printf("[GAME_STATE] LOCAL_AUTH_START_FAILED\n");
        return;
    }
#endif
    game_loading = 1; // WHY: transition to loading state (0->1, triggers LoadGame in MainMenu_Render)
    printf("[GAME_STATE] LOADING_LEVEL\n");
}

static const MainMenu dummy_test2[] =
{
    {"TEST 2A", 0, start_new_game, 0, /*cookie*/0},
    {"TEST 2B", 0, start_new_game, 0, /*cookie*/0},
    {"TEST 2C", 0, start_new_game, 0, /*cookie*/0},
    {0}
};

static const MainMenu dummy_test1[] =
{
    {"TEST 1A", dummy_test2, 0, 0, /*cookie*/0},
    {"TEST 1B", dummy_test2, 0, 0, /*cookie*/0},
    {"TEST 1C", dummy_test2, 0, 0, /*cookie*/0},
    {"TEST 1D", dummy_test2, 0, 0, /*cookie*/0},
    {"TEST 1E", dummy_test2, 0, 0, /*cookie*/0},
    {0}
};

static const MainMenu dummy[] =
{
    {"PRE Y9", 0, start_new_game, 0, /*cookie*/0},
    {"TEST", dummy_test1, 0, 0, /*cookie*/0},
    {0}
};

// here put parsed entries from the manifest
// it is referenced by some parent MainMenu element
// MainMenu { "title", 0, start_new_game, 0, manifest_cookie}
// static void main_menu_continue(MainMenu* m) { /* get cookie, load files, start new game */ } 
static const MainMenu* main_menu_new_game = /*0*/ dummy;
static const MainMenu* MainMenuGetRoot();


struct MainMenuContext
{
    int progress; // loading coarse progress in steps currently 0-3

    PatchIndex* patch_index;
    int patch_num;
    int patch_iter;

    int font_size[2];   // from OnSize
    int input_size[2];  // from OnSize
    int render_size[2]; // from Render
    
	// menu context
	int menu_stack[4]; // menu_stack[menu_depth] contains current item (hilight)
	int menu_depth; // -1 when closed, 0 just after OpenMenu

	// menu mouse / touch state
	int menu_down; // 0: released, 1:mouse_captured, 2:touch_captured
    bool down_back; // true if mouse or touch is holding 'back' item
	int menu_down_x;
	int menu_down_y;

    // re-calc on every menu jump
    int menu_scroll;
    int menu_smooth_scroll;
    int menu_max_scroll; 
    bool menu_rescroll; // flag it right after keyb/gamepad up/down navigation

	// when mouse/touch is taking over, store current hilight here
	// so we can revert hilight when pad/keyb is back
	int menu_temp; 

    void Root(bool default_highlight)
    {
        if (menu_depth != 0)
            mainmenu_dither = mainmenu_dither_hidden;
        menu_scroll=0;
        menu_smooth_scroll=0;
        menu_depth=0;
        menu_stack[menu_depth] = default_highlight ? 0 : -1;
        menu_temp = menu_stack[menu_depth];
    }

    void Init()
    {
        progress = 0;
        
        menu_max_scroll = 0;
        menu_smooth_scroll = 0;
        menu_scroll = 0;
        menu_depth = 0;
        menu_down = 0;
        down_back = false;
        menu_down_x = 0;
        menu_down_y = 0;
        menu_temp = 0;
        memset(menu_stack,0,sizeof(menu_stack));
    }

	//void Open(int method);
	//void Close();
	//void Toggle(int method);

    int CalcMaxScroll(int height) const
    {
        if (menu_depth<0)
            return 0;

        const MainMenu* m = MainMenuGetRoot();
        const char* title = "";
        for (int d=0; d<menu_depth; d++)
        {
            title = m[ menu_stack[d] ].str;
            m = m[ menu_stack[d] ].sub;
        }

        if (!m[0].str)
            return 0;

        int y = height-15;

        int w = 0, h = 0;
        Font1Size(title,&w,&h);

        if (title[0])
            y -= h+2;

        int i=1;
        while(m[i].str)
        {
            y -= h+1;
            i++;
        }

        return y < 0 ? -y : 0;
    }

	bool Paint(AnsiCell* ptr, int width, int height)
    {
        if (menu_depth<0)
        {
            // indicate we didn't take over logo space
            return true;
        }

        menu_max_scroll = CalcMaxScroll(height);
        if (menu_scroll > menu_max_scroll)
            menu_scroll = menu_max_scroll;
        if (menu_smooth_scroll > menu_max_scroll)
            menu_smooth_scroll = menu_max_scroll;

        // WHY: Linear interpolation for smooth scrolling (±1 per frame at 60 FPS)
        // Simpler than exponential decay (0.2 coefficient) for small menu scroll distances.
        // Frame-rate dependent: at 60 FPS, scrolling is smooth; at lower rates, slower.
        // Each ±1 step takes ~16ms at 60 FPS, creating predictable animation.
        if (menu_smooth_scroll < menu_scroll)
            menu_smooth_scroll++; // WHY: gradually approach target scroll position from below
        if (menu_smooth_scroll > menu_scroll)
            menu_smooth_scroll--; // WHY: gradually approach target scroll position from above
       
        const MainMenu* m = MainMenuGetRoot();
        char title[32]="";
        for (int d=0; d<menu_depth; d++)
        {
            sprintf(title,"\x04%s",m[ menu_stack[d] ].str);
            //title = m[ menu_stack[d] ].str;
            m = m[ menu_stack[d] ].sub;
        }

        // right align
        int x = width-5;
        int y = height-15;

        const int font_clip_height = 5;
        int scroll_clip_height = y + font_clip_height;

        // paint title
        if (title[0])
        {
            int w = 0, h = 0;
            Font1Size(title,&w,&h);
            Font1Paint(ptr,width,scroll_clip_height,3+x-w,y,title,FONT1_PINK_SKIN);
            Font1UnderLine(ptr,width,scroll_clip_height,3+x-w,y,w,FONT1_PINK_SKIN);
            y -= h+2;

            scroll_clip_height = y + font_clip_height;
        }

        y += menu_smooth_scroll;

        int i=0;
        while(m[i].str)
        {
            int w = 0, h = 0;
            Font1Size(m[i].str,&w,&h);

            int skin = i == menu_stack[menu_depth] ? FONT1_GOLD_SKIN : FONT1_GREY_SKIN;
            Font1Paint(ptr,width,scroll_clip_height,x-w,y,m[i].str,skin);

            if (i == menu_stack[menu_depth] && menu_rescroll)
            {
                menu_rescroll = false;

                // check if we should auto scroll
                int sharp_y = y - menu_smooth_scroll + menu_scroll;
                if (sharp_y<0)
                    menu_scroll += -sharp_y;
                if (sharp_y+font_clip_height > scroll_clip_height)
                    menu_scroll -= sharp_y+font_clip_height - scroll_clip_height;
            }

            const char* str = 0;
            if (m[i].sub)
                str = "\x03";
            else
            if (m[i].getter)
                str = m[i].getter(this) ? "\x02" : "\x01";

            if (str)
                Font1Paint(ptr,width,scroll_clip_height,x,y,str,FONT1_PINK_SKIN);

            y -= h+1;
            i++;
        }

        // indicate we didn't take over logo space
        return true; 
    }

    void ScreenToCell(int p[2]) const
    {
        p[0] = (2*p[0] - input_size[0] + render_size[0] * font_size[0]) / (2 * font_size[0]);
        p[1] = (input_size[1]-1 - 2*p[1] + render_size[1] * font_size[1]) / (2 * font_size[1]);
    }

	int  HitMenu(int hx, int hy)
    {
        if (menu_depth<0)
            return -3;

        int cp[2] = { hx, hy };
        ScreenToCell(cp);
        hx=cp[0];
        hy=cp[1];

        const MainMenu* m = MainMenuGetRoot();
        char title[32]="";
        for (int d=0; d<menu_depth; d++)
        {
            sprintf(title,"\x04%s",m[ menu_stack[d] ].str);
            //title = m[ menu_stack[d] ].str;
            m = m[ menu_stack[d] ].sub;
        }

        // right align
        int x = render_size[0]-5;
        int y = render_size[1]-15;

        if (title[0])
        {
            int w = 0, h = 0;
            Font1Size(title,&w,&h);

            if (hx >= 3+x-w /*&& hx<3+x*/ && hy >=y && hy<y+h)
            {
                // title hit
                return -1;
            }

            y -= h+2;
        }

        y += menu_smooth_scroll;

        int i=0;
        while(m[i].str)
        {
            int w = 0, h = 0;
            Font1Size(m[i].str,&w,&h);

            if (hx >= x-w /*&& hx < x*/ && hy>=y && hy<y+h)
            {
                // item hit
                return i;
            }

            y -= h+1;
            i++;
        }

        return -2;
    }

    // these things should call things above
    void OnFocus(bool set);
    void OnSize(int w, int h, int fw, int fh);
	void OnKeyb(GAME_KEYB keyb, int key);
	void OnMouse(GAME_MOUSE mouse, int x, int y);
	void OnTouch(GAME_TOUCH touch, int id, int x, int y);
	void OnPadMount(bool connected);
	void OnPadButton(int b, bool down);
	void OnPadAxis(int a, int16_t pos);
};

static MainMenuContext mainmenu_context = {0};

#ifdef __EMSCRIPTEN__
extern "C" void MainMenuResetWebLoadingState()
{
    game_loading = 0;
    show_continue = false;
    mainmenu_context.Init();
    printf("[MM] reset web loading state for runtime rebuild\n");
    fflush(stdout);
}

extern "C" int MainMenuWebGameLoadingState()
{
    return game_loading;
}

extern "C" int MainMenuWebProgressState()
{
    return mainmenu_context.progress;
}
#endif

struct Manifest
{
    const char* xp;    // this should be embedded using --preload-file
    const char* title; // short title (big font)
    const char* desc;  // long description (small font)
    const char* a3d;   // world file
    const char* ajs;   // game script 
    void* cookie;      // this contains menu runtime data (loaded sprites etc. or ad cookie)

    // if this is terminator, all fileds should be null
    // if this is dir, a3d must be null and ajs must point to Manifest array of children
    // if this is server based game, ajs must be null and a3d must contain address
    // if this is "coming soon" / ad, both a3d and ajs must be null, cookie may point to url 

    /*
    .ajs is required to initialize world with:
    - ak.setWater (55)
    - ak.setDir   (0)
    - ak.setYaw   (45)
    - ak.setPos   (0,15,0)
    - ak.setLight (1,0,1,.5)
    */

    /*
    .ajs optionally may hook these 2 to handle loading/saving game state
    - function onRead(arrbuf) -> applies modifications stored in arrbuf to the world
    - function onWrite() -> stores modified world state in array buffer, returns arrbuf
    // - read will be called only during fresh page load -> CreateGame
    // - write will be called on 'beforeunload' event or when process is about to
    //   terminate when there's game currently playing or is suspended by main menu
    */
};


char cookie_ad[] = "https://twitter.com/mrgumix";

static Manifest dev_toys_manifest_arr[]=
{
    {
        "dev_toy.xp",
        "DEV TOY1",
        "Example showing thing1, source: https://...dev_toy1",
        "dev_toys.a3d",
        "dev_toy1.ajs",
        0 // cookie
    },

    {
        "dev_toy.xp",
        "DEV TOY2",
        "Example showing thing2, source: https://...dev_toy2",
        "dev_toys.a3d",
        "dev_toy2.ajs",
        0 // cookie
    },

    {
        "dev_toy.xp",
        "DEV TOY3",
        "Example showing thing3, source: https://...dev_toy3",
        "dev_toys.a3d",
        "dev_toy3.ajs",
        0 // cookie
    },

    {0} // terminator
};

static Manifest manifest[]=
{
    {
        "tutorial.xp",
        "CONTROLS TUTORIAL ",
        "Tutorial teaching you how to control the game",
        "tutorial.a3d",
        "tutorial.ajs",
        0 // cookie
    },

    {
        "y9.xp",
        "Y9 DEMO",
        "Latest official demo world containig few playable quest",
        "game_map_y9.a3d",
        "game_map_y9.ajs",
        0 // cookie
    },

    {
        "y9_online.xp",
        "Y9 MULTIPLAYER DEMO",
        "Latest official multiplayer demo",
        "y9_server", // if ajs (below) is null, this is wss/endpoint 
        0, // real a3d and ajs files will be sent by server during joining
        0 // cookie
    },

    {
        "dev_toys.xp",
        "DEV TOYS",
        "Latest official dev toys",
        0, // this is directory!
        (const char*)dev_toys_manifest_arr, // and here are children
        0 // cookie
    },

    {
        "gumix.xp",
        "GUMIX NEWS",
        "",
        0,        // this
        0,        // is an ad
        cookie_ad // with a cookie
    },

    {0} // terminator
};

struct Gamma
{
    uint16_t dec[256];  // 0..8192 incl
    uint8_t  enc[8193]; // 0..255 incl

    Gamma()
    {
        for (int i=0; i<256; i++)
        {
            double t = i / 255.0;
            t = t >= 0.04045 ? pow((t+0.055)/1.055, 2.4) : t/12.92;
            dec[i] = (uint16_t)round(t * 8192.0);
        } 
        
        for (int i=0; i<=8192; i++)
        {
            double t = i / 8192.0;
            t = t > 0.0031308 ? 1.055*pow(t, 1.0/2.4) - 0.055 : 12.92*t;
            enc[i] = (uint8_t)round(255.0 * t);
        }
    }
};

static Gamma gamma_tables;

static void Bilinear(const uint16_t* src, int pitch, uint8_t x, uint8_t y, uint16_t* dst)
{
    // NEAREST TEST
    dst[0] = src[0];
    dst[1] = src[1];
    dst[2] = src[2];
    return;

    // +---------+---------+
    // |   src   |  src+3  |
    // |  R,G,B  |  R,G,B  | < y=0
    // |         |         |
    // +---------+---------+ < y=128
    // |  src+p  | src+3+p |
    // |  R,G,B  |  R,G,B  | < y=256
    // |         |         |
    // +---------+---------+
    //      ^    ^    ^
    //     x=0 x=128 x=256

    // src must be (dst will be) normalized to (0..8192 incl)

    const uint16_t* lwr = src;
    const uint16_t* upr = src + pitch;
    const uint32_t qx = x;
    const uint32_t qy = y;
    const uint32_t px = 256-qx;
    const uint32_t py = 256-qy;
    const uint32_t r_ofs = 1<<15;

    const uint32_t pypx = py * px;
    const uint32_t pyqx = py * qx;
    const uint32_t qypx = qy * px;
    const uint32_t qyqx = qy * qx;

    dst[0] = (pypx * lwr[0] + pyqx * lwr[3] + qypx * upr[0] + qyqx * upr[3] + r_ofs) >> 16; 
    dst[1] = (pypx * lwr[1] + pyqx * lwr[4] + qypx * upr[1] + qyqx * upr[4] + r_ofs) >> 16; 
    dst[2] = (pypx * lwr[2] + pyqx * lwr[5] + qypx * upr[2] + qyqx * upr[5] + r_ofs) >> 16; 
}

static uint32_t Extract4(const uint16_t* c1, const uint16_t* c2, const uint16_t* c3, const uint16_t* c4)
{
    const int xxx_3 = 3;
    int i = 
        (gamma_tables.enc[(c1[0] + c2[0] + c3[0] + c4[0] + 2) >> 2] + xxx_offs) / xxx_3 +
        (gamma_tables.enc[(c1[1] + c2[1] + c3[1] + c4[1] + 2) >> 2] + xxx_offs) / xxx_3 * xxx_size +
        (gamma_tables.enc[(c1[2] + c2[2] + c3[2] + c4[2] + 2) >> 2] + xxx_offs) / xxx_3 * xxx_size2;

    return xxx_table[ i ];    
}

static uint32_t Extract2(const uint16_t* c1, const uint16_t* c2)
{
    const int xxx_3 = 3;
    int i = 
        (gamma_tables.enc[(c1[0] + c2[0]) >> 1] + xxx_offs) / xxx_3 +
        (gamma_tables.enc[(c1[1] + c2[1]) >> 1] + xxx_offs) / xxx_3 * xxx_size +
        (gamma_tables.enc[(c1[2] + c2[2]) >> 1] + xxx_offs) / xxx_3 * xxx_size2;

    return xxx_table[ i ];    
}

static void Accumulate(uint16_t a[3], const int16_t v[3])
{
    a[0] = std::max(0, std::min(8192, a[0]+v[0] ));
    a[1] = std::max(0, std::min(8192, a[1]+v[1] ));
    a[2] = std::max(0, std::min(8192, a[2]+v[2] ));
}

#define DITHERING
static void ScaleImg(const uint16_t* src, int src_w, int src_h, const float src_xywh[4], 
                     AnsiCell* dst, int dst_w, int dst_h, int dst_pitch=0)
{
    const int src_pitch = src_w * 3;

    #ifdef DITHERING
    // DITHERING STUFF
    int16_t e0[160][3] = {{0}};
    int16_t e1[160][3] = {{0}};
    int16_t e2[160][3] = {{0}};

    // [0]-current line, [1]-next line, [2]-nextnext line
    int16_t (*dither[3])[3] = {e0,e1,e2};
    #endif

    if (dst_pitch<=0)
        dst_pitch = dst_w;

    // offset start pos by +half dst px and -half src px
    const int sx = (int)round(256.0 * src_xywh[0] + 128.0 * src_xywh[2] / (2*dst_w) - 128);
    const int sy = (int)round(256.0 * src_xywh[1] + 128.0 * src_xywh[3] / (2*dst_h) - 128);
    const int dx = (int)round(256.0 * src_xywh[2] / (2*dst_w));
    const int dy = (int)round(256.0 * src_xywh[3] / (2*dst_h));

    // for enlarging near src edges, or arbitrary src_rect (partially outside src image)
    // we'd need also to handle sampling outside src img !!!
    // that's the reason to keep src_w, src_h for clamping

    int cy1 = sy;

    for (int y=0; y<dst_h; y++)
    {
        int cx1 = sx;
        int cy2 = cy1+dy;

        cy1 = sy + (int)round(256.0 * src_xywh[3] * (2 * y + 0) / (2*dst_h));
        cy2 = sy + (int)round(256.0 * src_xywh[3] * (2 * y + 1) / (2*dst_h));

        uint8_t ry1 = cy1 & 0xFF;
        const uint16_t* lwr = src + src_pitch * (cy1 >> 8);

        uint8_t ry2 = cy2 & 0xFF;
        const uint16_t* upr = src + src_pitch * (cy2 >> 8);

        AnsiCell* ptr = dst + y * dst_pitch;

        for (int x=0; x<dst_w; x++)
        {
            int cx2 = cx1+dx;

            cx1 = sx + (int)round(256.0 * src_xywh[2] * (2 * x + 0) / (2*dst_w));
            cx2 = sx + (int)round(256.0 * src_xywh[2] * (2 * x + 1) / (2*dst_w));

            

            uint8_t rx1 = cx1 & 0xFF;
            uint8_t rx2 = cx2 & 0xFF;

            if (!((cx1>>8)>=0 && (cx1>>8)<src_w &&
                  (cx2>>8)>=0 && (cx2>>8)<src_w &&
                  (cy1>>8)>=0 && (cy1>>8)<src_h &&
                  (cy2>>8)>=0 && (cy2>>8)<src_h))
            {
                printf("PROBLEM AT X=%d, Y=%d, (%d,%d)\n", x,y,2*x,2*y);
                printf("DST W=%d, H=%d, (%d,%d)\n", dst_w,dst_h,2*dst_w,2*dst_h);

                printf("cx1: %d.%d , cx2: %d.%d , cy1: %d.%d , cy2: %d.%d\n",
                    cx1>>8,cx1&0xff, cx2>>8,cx1&0xff, cy1>>8,cx1&0xff, cy2>>8,cx1&0xff);

                printf("src_xywh: %f , %f , %f , %f\n", 
                    src_xywh[0],src_xywh[1],src_xywh[2],src_xywh[3]);

                printf("sx: %d , sy: %d , dx: %d , dy: %d\n", 
                    sx,sy,dx,dy);

                assert(0);
            }

            uint16_t LL[3], LR[3], UL[3], UR[3];
            Bilinear(lwr + (cx1 >> 8)*3, src_pitch, rx1,ry1, LL); 
            Bilinear(lwr + (cx2 >> 8)*3, src_pitch, rx2,ry1, LR);
            Bilinear(upr + (cx1 >> 8)*3, src_pitch, rx1,ry2, UL);
            Bilinear(upr + (cx2 >> 8)*3, src_pitch, rx2,ry2, UR);

            // read & apply errors with clamping 
            #ifdef DITHERING
            Accumulate(LL, dither[0][x]);
            Accumulate(LR, dither[0][x]);
            Accumulate(UL, dither[0][x]);
            Accumulate(UR, dither[0][x]);

            // reset
            dither[0][x][0] = 0;
            dither[0][x][1] = 0;
            dither[0][x][2] = 0;
            #endif

            // we have 4 filtered samples, let's ANSIfy them into the single cell

            // WORKAROUND: Boost brightness to make menu visible on web
            // The menu.png image renders very dark (RGB ~51) after gamma correction
            // Multiply by 3.0x to get ~153 (60% brightness) which is visible
            // TODO: Investigate gamma_tables.enc[] to fix root cause
            // Range: 0-8192 (linear color space before palette encoding)
            for (int i = 0; i < 3; i++) {
                LL[i] = (LL[i] * 3 > 8192) ? 8192 : LL[i] * 3;
                LR[i] = (LR[i] * 3 > 8192) ? 8192 : LR[i] * 3;
                UL[i] = (UL[i] * 3 > 8192) ? 8192 : UL[i] * 3;
                UR[i] = (UR[i] * 3 > 8192) ? 8192 : UR[i] * 3;
            }

            // calc 4 encoded reference colors (for calcing errors)
            int ll[3] ={gamma_tables.enc[LL[0]],gamma_tables.enc[LL[1]],gamma_tables.enc[LL[2]]};
            int lr[3] ={gamma_tables.enc[LR[0]],gamma_tables.enc[LR[1]],gamma_tables.enc[LR[2]]};
            int ul[3] ={gamma_tables.enc[UL[0]],gamma_tables.enc[UL[1]],gamma_tables.enc[UL[2]]};
            int ur[3] ={gamma_tables.enc[UR[0]],gamma_tables.enc[UR[1]],gamma_tables.enc[UR[2]]};

            // now reconstruct rgb values from the palette
            uint32_t l_slot = Extract2(LL,UL);
            uint32_t r_slot = Extract2(LR,UR);
            uint32_t b_slot = Extract2(LL,LR);
            uint32_t t_slot = Extract2(UL,UR);
            uint32_t d_slot = Extract4(LL,LR,UL,UR);

            const uint8_t* l = pal[(l_slot>>16) & 0xFF];
            const uint8_t* r = pal[(r_slot>>16) & 0xFF];
            const uint8_t* b = pal[(b_slot>>16) & 0xFF];
            const uint8_t* t = pal[(t_slot>>16) & 0xFF];
            const uint8_t* d = half_tone[d_slot>>24][d_slot&0xFF][(d_slot>>8)&0xFF];

            // calc errors
            int lr_err = 
                2*(std::abs(l[0] - ll[0]) + std::abs(l[0] - ul[0]) + std::abs(r[0] - lr[0]) + std::abs(r[0] - ur[0])) +
                3*(std::abs(l[1] - ll[1]) + std::abs(l[1] - ul[1]) + std::abs(r[1] - lr[1]) + std::abs(r[1] - ur[1])) +
                1*(std::abs(l[2] - ll[2]) + std::abs(l[2] - ul[2]) + std::abs(r[2] - lr[2]) + std::abs(r[2] - ur[2]));

            int bt_err = 
                2*(std::abs(b[0] - ll[0]) + std::abs(b[0] - lr[0]) + std::abs(t[0] - ul[0]) + std::abs(t[0] - ur[0])) +
                3*(std::abs(b[1] - ll[1]) + std::abs(b[1] - lr[1]) + std::abs(t[1] - ul[1]) + std::abs(t[1] - ur[1])) +
                1*(std::abs(b[2] - ll[2]) + std::abs(b[2] - lr[2]) + std::abs(t[2] - ul[2]) + std::abs(t[2] - ur[2]));

            int ht_err = 
                2*(std::abs(d[0] - ll[0]) + std::abs(d[0] - lr[0]) + std::abs(d[0] - ul[0]) + std::abs(d[0] - ur[0])) +
                3*(std::abs(d[1] - ll[1]) + std::abs(d[1] - lr[1]) + std::abs(d[1] - ul[1]) + std::abs(d[1] - ur[1])) +
                1*(std::abs(d[2] - ll[2]) + std::abs(d[2] - lr[2]) + std::abs(d[2] - ul[2]) + std::abs(d[2] - ur[2]));

            // DEBUG: Trace center pixel calculation
			if (MainMenuDebugEnabled() && d_slot == 6063958)
			{
				printf("ScaleImg[%d,%d]: errs(lr=%d, bt=%d, ht=%d) d_slot=%u\n", x, y, lr_err, bt_err, ht_err, d_slot);
				printf("  Components: l=[%d,%d,%d] r=[%d,%d,%d] b=[%d,%d,%d] t=[%d,%d,%d] d=[%d,%d,%d]\n",
					l[0], l[1], l[2], r[0], r[1], r[2], b[0], b[1], b[2], t[0], t[1], t[2], d[0], d[1], d[2]);
				//printf("  -> Taking %s path\n", d_idx == 0 ? "Left-Right" : "Bottom-Top");
			}
            #ifdef DITHERING
            int32_t dev[3] =
            {
                LL[0]+LR[0]+UL[0]+UR[0],
                LL[1]+LR[1]+UL[1]+UR[1],
                LL[2]+LR[2]+UL[2]+UR[2]
            };
            #endif

            // pick best and calculate deviations
            if (ht_err <= lr_err && ht_err <= bt_err)
            {
                if (MainMenuDebugEnabled() && x == dst_w/2 && y == dst_h/2)
					printf("  -> Taking Half-Tone path\n");
                #ifdef DITHERING
                dev[0] -=  4 * gamma_tables.dec[d[0]];
                dev[1] -=  4 * gamma_tables.dec[d[1]];
                dev[2] -=  4 * gamma_tables.dec[d[2]];
                #endif

                dst->fg = ((d_slot>>8) & 0xFF) + 16;
                dst->bk = (d_slot & 0xFF) + 16;
                dst->gl = (d_slot>>24) + 176;
                dst->spare = 0;
            }
            else
            if (bt_err < lr_err)
            {
                if (x == dst_w/2 && y == dst_h/2)
                {
                    static bool debug_path_init = false;
                    static bool debug_path = false;
                    if (!debug_path_init)
                    {
                        debug_path_init = true;
                        debug_path = getenv("ASCIICKER_DEBUG_PATH") != nullptr;
                    }
                    if (debug_path)
                    {
                        // Debug log: enable with ASCIICKER_DEBUG_PATH.
                        printf("  -> Taking Bottom-Top path\n");
                    }
                }
                #ifdef DITHERING
                dev[0] -= 2 * (gamma_tables.dec[b[0]] + gamma_tables.dec[t[0]]);
                dev[1] -= 2 * (gamma_tables.dec[b[1]] + gamma_tables.dec[t[1]]);
                dev[2] -= 2 * (gamma_tables.dec[b[2]] + gamma_tables.dec[t[2]]);
                #endif

                dst->fg = ((b_slot>>16) & 0xFF) + 16;
                dst->bk = ((t_slot>>16) & 0xFF) + 16;
                dst->gl = 220;
                dst->spare = 0;
            }
            else
            {
                if (x == dst_w/2 && y == dst_h/2)
                {
                    static bool debug_path_init = false;
                    static bool debug_path = false;
                    if (!debug_path_init)
                    {
                        debug_path_init = true;
                        debug_path = getenv("ASCIICKER_DEBUG_PATH") != nullptr;
                    }
                    if (debug_path)
                    {
                        // Debug log: enable with ASCIICKER_DEBUG_PATH.
                        printf("  -> Taking Left-Right path\n");
                    }
                }
                #ifdef DITHERING
                dev[0] -= 2 * (gamma_tables.dec[l[0]] + gamma_tables.dec[r[0]]);
                dev[1] -= 2 * (gamma_tables.dec[l[1]] + gamma_tables.dec[r[1]]);
                dev[2] -= 2 * (gamma_tables.dec[l[2]] + gamma_tables.dec[r[2]]);
                #endif

                dst->fg = ((l_slot>>16) & 0xFF) + 16;
                dst->bk = ((r_slot>>16) & 0xFF) + 16;
                dst->gl = 221;
                dst->spare = 0;
            }

            // FORCE CONTRAST DEBUG
            if (dst->fg == dst->bk) {
                 dst->fg = 15 + 16; // White
            }

            // finaly distribute deviations
            #ifdef DITHERING
            dev[0] /= 32;
            dev[1] /= 32;
            dev[2] /= 32;

            if (x<dst_w-1)
            {
                dither[0][x+1][0] += dev[0];
                dither[0][x+1][1] += dev[1];
                dither[0][x+1][2] += dev[2];

                if (x<dst_w-2)
                {
                    dither[0][x+2][0] += dev[0];
                    dither[0][x+2][1] += dev[1];
                    dither[0][x+2][2] += dev[2];
                }
            }

            if (y<dst_h-1)
            {
                dither[1][x][0] += dev[0];
                dither[1][x][1] += dev[1];
                dither[1][x][2] += dev[2];

                if (x>0)
                {
                    dither[1][x-1][0] += dev[0];
                    dither[1][x-1][1] += dev[1];
                    dither[1][x-1][2] += dev[2];
                }

                if (x<dst_w-1)
                {
                    dither[1][x+1][0] += dev[0];
                    dither[1][x+1][1] += dev[1];
                    dither[1][x+1][2] += dev[2];
                }

                if (y<dst_h-2)
                {
                    dither[2][x][0] += dev[0];
                    dither[2][x][1] += dev[1];
                    dither[2][x][2] += dev[2];
                }
            }
            #endif

            cx1 = cx2 + dx;
            dst++;
        }

        cy1 = cy2 + dy;

        #ifdef DITHERING
        int16_t (*roll)[3] = dither[0];
        dither[0] = dither[1];
        dither[1] = dither[2];
        dither[2] = roll;
        #endif
    }
}

static void FreeImg(uint16_t* img)
{
    free(img);
}

static uint16_t* LoadImg(const char* path, int* w, int* h)
{
	upng_t* upng = upng_new_from_file(path);

	if (!upng)
		return 0;

	if (upng_get_error(upng) != UPNG_EOK)
	{
		upng_free(upng);
		return 0;
	}

	if (upng_header(upng) != UPNG_EOK)
	{
		upng_free(upng);
		return 0;
	}    

	int format, width, height, depth;
	format = upng_get_format(upng);
	width = upng_get_width(upng);
	height = upng_get_height(upng);

    if (format != UPNG_RGB8)
    {
		upng_free(upng);
		return 0;
    }

	if (upng_decode(upng) != UPNG_EOK)
	{
		upng_free(upng);
		return 0;
	}

	const uint8_t* buf = upng_get_buffer(upng);

    // allocate extra row and 1 px so Bilinear sampler won't overflow
    int wh3 = (width*(height+1) + 1)*3;
	uint16_t* pix = (uint16_t*)malloc(wh3*sizeof(uint16_t));

    // reflect vertically and decode gamma!
    for (int i=0,y=0; y<height; y++)
    {
        int j = (height - y - 1) * width * 3;
        for (int x=0; x<width; x++, i+=3, j+=3)
        {
            pix[j+0] = gamma_tables.dec[buf[i+0]];
            pix[j+1] = gamma_tables.dec[buf[i+1]];
            pix[j+2] = gamma_tables.dec[buf[i+2]];
        }
    }

    *w = width;
    *h = height;

	upng_free(upng);
    return pix;
}

extern "C" void *tinfl_decompress_mem_to_heap(const void *pSrc_buf, size_t src_buf_len, size_t *pOut_len, int flags);

// WHY: Load main menu background sprites and initialize dithering palette
// Purpose: (1) Initialize 6x6x6 RGB cube palette (216 colors)
//          (2) Generate half-tone dithering lookup tables
//          (3) Load compressed palette.gz inverse palettizer
//          (4) Load menu_back.png background image
//          (5) Load logo sprite
// Called once at startup from game initialization code.
int LoadMainMenuSprites(const char* base_path)
{
    // init palette entries - WHY: 6x6x6 RGB cube (0, 51, 102, 153, 204, 255 per channel)
    /*
    FILE* ppp = fopen("666.gpl","wb");
    fprintf(ppp,"GIMP Palette\n");
    fprintf(ppp,"Name: 666\n");
    fprintf(ppp,"\n");
    fprintf(ppp,"#");
    */
	for (int i=0; i<pal_size; i++)
	{
		int j = i;
		pal[i][2] = j%6*51; j /= 6;
		pal[i][1] = j%6*51; j /= 6;
		pal[i][0] = j%6*51; j /= 6;

        //fprintf(ppp,"%3d %3d %3d    mycolor %d\n",pal[i][0],pal[i][1],pal[i][2],i);
	}
    //fclose(ppp);

    // init half_tone mapper
    for (int gl=1; gl<3; gl++)
    {
        int g = gl-1;
        int c0_w = 4 - gl;
        int c1_w = gl;
        for (int c0=0; c0<216; c0++)
        {
            for (int c1=0; c1<216; c1++)
            {
                for (int c=0; c<3; c++)
                {
                    half_tone[g][c0][c1][c] = 
                        gamma_tables.enc[( 
                            c0_w * gamma_tables.dec[pal[c0][c]] + 
                            c1_w * gamma_tables.dec[pal[c1][c]] + 2) >> 2 ];
                }
            }
        }
    }

    // load inverse palettizer

	char path[1024];
	sprintf(path,"%sassets/palettes/palette.gz", base_path);
	printf("Loading palette from: %s\n", path);

	FILE* f = fopen(path, "rb");
	if (!f)
	{
		printf("FAILED to load palette file!\n");
		return 0;
	}
	printf("Palette file opened successfully\n");

	/////////////////////////////////
	// GZ INTRO:

	struct GZ
	{
		uint8_t id1, id2, cm, flg;
		uint8_t mtime[4];
		uint8_t xfl, os;
	};

	GZ gz;
	int r;
	r=(int)fread(&gz, 10, 1, f);

	/*
	assert(gz.id1 == 31 && gz.id2 == 139 && "gz identity");
	assert(gz.cm == 8 && "deflate method");
	*/

	if (gz.id1 != 31 || gz.id2 != 139 || gz.cm != 8)
	{
		fclose(f);
		return 0;
	}

	if (gz.flg & (1 << 2/*FEXTRA*/))
	{
		int hi, lo;
		r=(int)fread(&hi, 1, 1, f);
		r=(int)fread(&lo, 1, 1, f);

		int len = (hi << 8) | lo;
		fseek(f, len, SEEK_CUR);
	}

	if (gz.flg & (1 << 3/*FNAME*/))
	{
		uint8_t ch;
		do
		{
			ch = 0;
			r=(int)fread(&ch, 1, 1, f);
		} while (ch);
	}

	if (gz.flg & (1 << 4/*FCOMMENT*/))
	{
		uint8_t ch;
		do
		{
			ch = 0;
			r=(int)fread(&ch, 1, 1, f);
		} while (ch);
	}

	if (gz.flg & (1 << 1/*FFHCRC*/))
	{
		uint16_t crc;
		r=(int)fread(&crc, 2, 1, f);
	}

	// deflated data blocks ...
	// read everything till end of file, trim tail by 8 bytes (crc32,isize)

	long now = ftell(f);
	fseek(f, 0, SEEK_END);

	unsigned long insize = ftell(f) - now - 8;
	unsigned char* in = (unsigned char*)malloc(insize);
	fseek(f, now, SEEK_SET);

	r=(int)fread(in, 1, insize, f);


	size_t out_size=0;
	void* out = tinfl_decompress_mem_to_heap(in, insize, &out_size, 0);
	// void* out = u_inflate(in, insize);
	free(in);

	/////////////////////////////////
	// GZ OUTRO:

	uint32_t crc32, isize;
	r=(int)fread(&crc32, 4, 1, f);
	r=(int)fread(&isize, 4, 1, f);
	fclose(f);

	// assert(out && isize == *(uint32_t*)out);
	assert(out && isize == out_size);

    xxx_step = (uint8_t)*(uint32_t*)out;
    xxx_table = (uint32_t*)out + 1;
	xxx_offs = xxx_step >> 1;
	xxx_size = 255 / xxx_step + 1;
    xxx_size2 = xxx_size * xxx_size;

    assert(xxx_step == 3);

    // prepare GAMMA decoder and encoder
    // linear space will be in range (0..8192 incl)
    sprintf(path,"%sassets/images/menu.png", base_path);
    printf("Loading menu background from: %s\n", path);
    menu_bk_img = LoadImg(path, &menu_bk_width, &menu_bk_height);

    if (!menu_bk_img)
    {
        printf("FAILED to load menu background image!\n");
        return 0;
    }
    printf("Menu background loaded: %dx%d\n", menu_bk_width, menu_bk_height);

    sprintf(path,"%sassets/sprites/asciicker.xp", base_path);
    menu_logo_sprite = LoadSprite(path,"asciicker");

    // parse manifest, load sprites (oridinary sync)
    // and store cookies (ad cookies require copying original value into the actual cookie)

    return 0;
}

// WHY: Free main menu resources (inverse of LoadMainMenuSprites)
// Purpose: Release memory allocated for palette tables, background image, and logo sprite.
// Called at shutdown or when transitioning away from main menu.
void FreeMainMenuSprites()
{
    // undo LoadMainMenuSprites

    if (xxx_table)
        free(xxx_table-1); // WHY: xxx_table-1 because LoadMainMenuSprites allocated with +1 offset
    xxx_table = 0;

    if (menu_bk_img)
        FreeImg(menu_bk_img);
    menu_bk_img = 0;

    if (menu_logo_sprite)
        FreeSprite(menu_logo_sprite);
    menu_logo_sprite = 0;
}

void LoadGame(MainMenuContext* mmc)
{
    float water = 55;
    float dir = 0;
    float yaw = 45;
    float pos[3] = {0,15,0};
    float lt[4] = {1,0,1,.5};
    GetDefaultGameStart(&water, pos, &yaw, &dir, lt);

    /*
        NEW mmc->progress steps:
        0: request to download a3d will be sent, mmc->progress is changed to 1
        1: awaiting for a3d download complete -> 2 or error -> (0 with cleanup)
            // todo: later
            2: verify which meshes need to be downloaded, prepare the list and fetch all -> 3
            3: awaiting for meshes, if all completed -> 4, if any error -> (0 with cleanup)
        4: rebuild world -> 5
        5: keeps updating terrain darks, when completed -> 6
        6: init game -> 0
    */

    // if this is first call
    if (mmc->progress == 0)
    {
        printf("[LOAD] progress=0: loading a3d...\n");
        // here the path should be taken from the module manifest
        // ...

        char a3d_path[1024 + 20];
        ResolveRequestedA3dPath(a3d_path, sizeof(a3d_path), base_path);
        strncpy(g_loaded_a3d_path, a3d_path, sizeof(g_loaded_a3d_path) - 1);
        g_loaded_a3d_path[sizeof(g_loaded_a3d_path) - 1] = 0;
        FreeMinimapMarkers();
        FILE* f = fopen(a3d_path, "rb");

        // TODO:
        // if GameServer* gs != 0
        // DO NOT LOAD ITEMS!
        // we will receive them from server

        if (f)
        {
            PatchIndex* index = 0;
            terrain = LoadTerrain(f, &index);

            mmc->patch_num = GetTerrainPatches(terrain);
            mmc->patch_index = index;
            mmc->patch_iter = 0;

            if (terrain)
            {
                for (int i = 0; i < 256; i++)
                {
                    if (fread(mat[i].shade, 1, sizeof(MatCell) * 4 * 16, f) != sizeof(MatCell) * 4 * 16)
                        break;
                    material_glyph_plane_free(mat[i].glyph_plane);
                    mat[i].glyph_plane = NULL;
                }
                if (!LoadMainMenuMaterialGlyphSidecar(a3d_path, mat))
                {
                    fclose(f);
                    mmc->progress = 0;
                    printf("[LOAD] material glyph sidecar failed\n");
                    return;
                }

                // Snow material (matid=250): override after fread. MatCell = {fg[3], gl, bg[3], flags}.
                for (int r = 0; r < 4; r++)
                    for (int s = 0; s < 16; s++) {
                        float sf = 1.0f - (s / 16.0f) * 0.15f;
                        uint8_t snow_gl[] = {'*', '+', '.', ' '};
                        mat[250].shade[r][s].fg[0] = (uint8_t)(255 * sf);
                        mat[250].shade[r][s].fg[1] = (uint8_t)(255 * sf);
                        mat[250].shade[r][s].fg[2] = (uint8_t)(255 * sf);
                        mat[250].shade[r][s].gl = snow_gl[r];
                        mat[250].shade[r][s].bg[0] = (uint8_t)(255 * sf);
                        mat[250].shade[r][s].bg[1] = (uint8_t)(255 * sf);
                        mat[250].shade[r][s].bg[2] = (uint8_t)(255 * sf);
                        mat[250].shade[r][s].flags = 0;
                    }

                world = LoadWorldRuntime(f);
                if (world)
                {
                    WorldGetPlayerStart(world, pos, &yaw, &dir);
                    static bool debug_load_init = false;
                    static bool debug_load = false;
                    if (!debug_load_init)
                    {
                        debug_load_init = true;
                        debug_load = getenv("ASCIICKER_DEBUG_LOAD") != nullptr;
                    }

                    // reload meshes too
                    Mesh* m = GetFirstMesh(world);

                    while (m)
                    {
                        char mesh_name[256];
                        GetMeshName(m, mesh_name, 256);
                        char obj_path[4096];
                        ResolveMeshAssetPath(obj_path, sizeof(obj_path), base_path, mesh_name);
                        if (debug_load)
                        {
                            // Debug log: enable with ASCIICKER_DEBUG_LOAD.
                            printf("[LoadMesh] Loading: %s\n", obj_path);
                            fflush(stdout);
                        }
                        if (!UpdateMesh(m, obj_path))
                        {
                            if (debug_load)
                            {
                                // Debug log: enable with ASCIICKER_DEBUG_LOAD.
                                printf("[LoadMesh] FAILED: %s\n", obj_path);
                                fflush(stdout);
                            }
                        }
                        else
                        {
                            if (debug_load)
                            {
                                // Debug log: enable with ASCIICKER_DEBUG_LOAD.
                                printf("[LoadMesh] OK: %s (faces=%d)\n", mesh_name, GetMeshFaces(m));
                                fflush(stdout);
                            }
                        }

                        m = GetNextMesh(m);
                    }

                    LoadEnemyGens(f);
                    LoadMinimapMarkers(f);
                }
            }

            fclose(f);
        }

        mmc->progress = 1;
        printf("[LOAD] progress -> 1\n");
    }
    else
    if (mmc->progress == 1)
    {
        printf("[LOAD] progress=1: RebuildWorld...\n");
        RebuildWorld(world, true);
        mmc->progress = 2;
        printf("[LOAD] progress -> 2\n");
    }
    else
    if (mmc->progress == 2)
    {
        #ifdef DARK_TERRAIN

        // LINEAGE_JSON: {"fl":"FL-3011","cautionary_precedent":"terrain_dark_blocking_skip_removed","note":"DO NOT reintroduce the old blocking DARK_TERRAIN skip (progress=2 wall) as a lag mitigation. The old __EMSCRIPTEN__ shortcut skipped terrain-dark entirely for faster multiplayer loading — this was a rendering-quality regression masquerading as a lag fix. The deferred/incremental bootstrap below (DeferTerrainDarkBootstrap + StepDeferredTerrainDarkBootstrap in game_render_bridge.cpp) is the correct approach. The real lag owner is elsewhere (FL-2957)."}
        // FL-3161: an agent repeated this spent terrain-dark mitigation family on
        // 2026-05-05. Do not replace this deferred handoff with skip/disable/throttle
        // logic unless a fresh FL-3011 family audit proves the visual guard obsolete.
        // Keep terrain-dark quality in multiplayer without reviving the old
        // blocking bootstrap stall: hand the patch list to the deferred runtime
        // owner, then enter gameplay immediately.
        #ifdef __EMSCRIPTEN__
        if (server || WebAuthoritativeJoinActive())
        {
            printf("[LOAD] Deferring terrain-dark bootstrap for multiplayer, progress -> 3\n");
            DeferTerrainDarkBootstrap(mmc->patch_index, mmc->patch_num, lt, false);
            mmc->patch_index = 0;
            mmc->patch_num = 0;
            mmc->patch_iter = 0;
            mmc->progress = 3;
        }
        else
        #endif
        // Skip terrain dark update if no patches
        if (mmc->patch_num == 0)
        {
            CancelDeferredTerrainDarkBootstrap();
            mmc->progress = 3;
        }
        else
        {
            printf("[LOAD] Processing %d patches incrementally (%d/frame)...\n",
                mmc->patch_num, MAINMENU_TERRAIN_DARK_PATCHES_PER_FRAME);
            int patch_end = mmc->patch_iter + MAINMENU_TERRAIN_DARK_PATCHES_PER_FRAME;
            if (patch_end > mmc->patch_num)
                patch_end = mmc->patch_num;
            for (int n = mmc->patch_iter; n < patch_end; n++)
            {
                PatchIndex* pi = mmc->patch_index + n;
                UpdateTerrainDark(terrain, pi, world, lt, false);
            }
            mmc->patch_iter = patch_end;
            if (mmc->patch_iter >= mmc->patch_num)
            {
                printf("[LOAD] All %d patches done, progress -> 3\n", mmc->patch_num);
                FreePatchIndex(mmc->patch_index);
                mmc->patch_index = 0;
                mmc->patch_num = 0;
                mmc->patch_iter = 0;
                mmc->progress = 3;
            }
        }
        #else
        // No DARK_TERRAIN: skip directly to next stage
        mmc->progress = 3;
        #endif
    }
    else
    if (mmc->progress == 3)
    {
        printf("[LOAD] progress=3: InitGame...\n");
        // finalize loading ...
        mmc->progress = 0;

#ifdef __EMSCRIPTEN__
        WebFL933ServerPointerWatch("LoadGame:progress3-before-InitGame", game,
                                   game ? (uint32_t)sizeof(Game) : 0,
                                   player_head, player_tail);
        WebFL933AssertAuthoritativeServerPresent("LoadGame:progress3-before-InitGame", game,
                                                 game ? (uint32_t)sizeof(Game) : 0,
                                                 player_head, player_tail);
#endif
        InitGame(game, water, pos, yaw, dir, lt, /*mainmenu_stamp*/ MakeStamp());
#ifdef __EMSCRIPTEN__
        WebFL933ServerPointerWatch("LoadGame:progress3-after-InitGame", game,
                                   game ? (uint32_t)sizeof(Game) : 0,
                                   player_head, player_tail);
        WebFL933AssertAuthoritativeServerPresent("LoadGame:progress3-after-InitGame", game,
                                                 game ? (uint32_t)sizeof(Game) : 0,
                                                 player_head, player_tail);
#endif
        printf("[LOAD] progress -> 0 (done)\n");

        // Create world instances for remote players who joined before map loaded
        if (server && world)
        {
            Human* h = server->authority.head;
            while (h)
            {
                // Visual pipeline gutted — main menu remote player instances
                // must be created from canonical presentation state, not raw fields.
                (void)h;
                h = (Human*)h->next;
            }
        }

        // here we should also execute some .ajs
        // ...

        // if it sets onRead, and we have same saved progress, call it now.
        // ...

        game->OnSize(
            mainmenu_context.input_size[0],
            mainmenu_context.input_size[1],
            mainmenu_context.font_size[0],
            mainmenu_context.font_size[1]);
    }
}

// WHY: Reset game state before loading new level
// Purpose: Free existing game/terrain/world resources to prepare for fresh level load.
// Called from start_new_game() when user initiates new game while already loading/loaded.
// Ensures clean slate for LoadGame() to avoid memory leaks and stale state.
static void ResetGame()
{
#if !defined(__EMSCRIPTEN__)
    StopNormalGameAuthoritativeSession();
#endif
#ifdef DARK_TERRAIN
    CancelDeferredTerrainDarkBootstrap();
#endif
    // let's test fresh restart
    FreeGame(game); // WHY: release player state, entities, physics objects

    if (terrain)
        DeleteTerrain(terrain); // WHY: release voxel terrain data

    if (world)
        DeleteWorld(world); // WHY: release world mesh/sprite data

    PurgeItemInstCache(); // WHY: clear cached item instances

    // we would also RESET:
    // - akAPI_This to {}
    // - all CB handlers to null
}

// WHY: Main rendering entry point for main menu (called every frame)
// Purpose: (1) Update dither animation timer
//          (2) Render background image with aspect-correct scaling
//          (3) Render character sprites (Wolfie + player)
//          (4) Render menu items (via MainMenuContext::Paint)
//          (5) Render logo sprite
//          (6) Handle level loading UI (if game_loading == 1)
//          (7) Handle screenshot capture (if mainmenu_shot == true)
// Called from game_app.cpp/game_web.cpp platform render loops at ~60 FPS.
void MainMenu_Render(uint64_t _stamp, AnsiCell* ptr, int width, int height)
{
    mainmenu_stamp = _stamp; // WHY: store timestamp for gamepad/animation timing

    mainmenu_context.render_size[0] = width;
    mainmenu_context.render_size[1] = height;

    // FL-036 fix (web only): auto-start loading when joining multiplayer.
    // Without this, the game stays on the main menu and Game::Render() never reaches
    // the if(server) block that sends POSE/LAG/INPUT_MOVE packets.
#ifdef __EMSCRIPTEN__
    if ((server || WebAuthoritativeJoinActive()) && game_loading == 0)
    {
        game_loading = 1;
        printf("[FL036-FIX] multiplayer detected, auto-starting level load\n");
    }
#endif

    if (game_loading == 0)
        show_continue = false;
    if (game_loading == 2)
        show_continue = true;

#ifdef __EMSCRIPTEN__
    if (game_loading == 2 && game && game->ui.main_menu &&
        (server || WebAuthoritativeJoinActive()) &&
        MainMenuReadyToEnterLoadedWorld())
    {
        game->ui.main_menu = false;
        mainmenu_context.Init();
    }
#endif

#if !defined(__EMSCRIPTEN__)
    if (game_loading == 0 && game && game->ui.main_menu &&
        g_requested_a3d_path[0] && MainMenuAutoShotFlagPresent())
    {
        printf("[auto-shot] native requested map detected, auto-starting level load\n");
        start_new_game(&mainmenu_context);
    }
#endif

    uint64_t dt = _stamp - dither_stamp;
    dither_stamp += dt / 16666 * 16666;
    mainmenu_dither -= dt / 16666;
    if (mainmenu_dither < 0)
        mainmenu_dither = 0;

    /*
    int s = width*height;
    for (int i=0; i<s; i++)
        *((uint32_t*)ptr+i) = fast_rand() | (fast_rand()<<15);// | (fast_rand()<<30);
    */

    // ensure there's enough horizontal source space
    // for all menu depths

    if (width>0 && height>0 && menu_bk_img && menu_bk_width>2 && menu_bk_height>2)
    {
        // note scrolling disabled (scroll_step=0)
        // no need it, we have disolve transition
        int max_depth = 3; // scan it!
        int scroll_step = 0; // 4 per depth
        int scroll_width = scroll_step * max_depth;

        // we want to scroll horizontally by scroll_cells of destination surface

        float dst_aspect = (float)(width+scroll_width) / height;
        float img_aspect = (float)(menu_bk_width-2) / (menu_bk_height-2);

        float src_xywh[4];
        if (dst_aspect > img_aspect)
        {
            src_xywh[2] = menu_bk_width-2;
            src_xywh[0] = 1;
            src_xywh[3] = (menu_bk_width-2) / dst_aspect;
            src_xywh[1] = 1;//0.5f * (menu_bk_height - src_xywh[3]);
        }
        else
        {
            src_xywh[3] = menu_bk_height-2;
            src_xywh[1] = 1;
            src_xywh[2] = (menu_bk_height-2) * dst_aspect;
            src_xywh[0] = 1 + 0.5f * ((menu_bk_width-2) - src_xywh[2]);
        }

        float scale = src_xywh[3] / height;

        // ensure wolfie is visible
        // (portrait with hi zoom can easily move it outside)
        float wolfie_x = 112, wolfie_y = 156;
        const float wolfie_margin = 4 * scale;
        if (src_xywh[0] > wolfie_x - wolfie_margin)
            src_xywh[0] = wolfie_x - wolfie_margin;

        float src_scroll_step = scroll_step * scale;
        float src_scroll_width = scroll_width * scale;

        // shrink src width to match actual width (without scroll space)
        src_xywh[2] -= src_scroll_width;
        
        // shift src horizontally by amount from the current depth
        src_xywh[0] += src_scroll_step * mainmenu_context.menu_depth;

        if (src_xywh[0]<1 || src_xywh[0]+src_xywh[2]>menu_bk_width-1 ||
            src_xywh[1]<1 || src_xywh[1]+src_xywh[3]>menu_bk_height-1)
        {
            printf("x1:%f y1:%f x2:%f y2:%f\n", 
                src_xywh[0],
                src_xywh[1],
                src_xywh[0]+src_xywh[2],
                src_xywh[1]+src_xywh[3]);
        }
        //else
            ScaleImg(menu_bk_img, menu_bk_width, menu_bk_height, src_xywh, ptr, width, height);

        // scaleimg could also scale alpha channel into AnsiCell::spare ( 4 x 2bits / AnsiCell )
        // so BlitSprite could test it against sprite "distance" (limited to 4 layers)

        // DETERMINE IF WE SHOULD GO FOR SINGLE OR DUAL COLUMN LAYOUT

        // AT THE CURRENT LEVEL DETERMINE LONGEST STRING WITH EXTRA SPACING

        // xform src coords (115x150): 
        wolfie_y = menu_bk_height - wolfie_y;

        wolfie_x -= src_xywh[0];
        wolfie_y -= src_xywh[1];

        wolfie_x /= scale;
        wolfie_y /= scale;

        
        bool logo_space = true;

        if (show_gamepad)
        {
            // TODO:
            // determine somehow if we can keep logo or not!!!!

            // TODO:
            // also add x,y arguments to PaintGamePad for better centering when 
            // we have enough space for both the logo and the gamepad

            SetSpriteDither(mainmenu_dither>>1);
            PaintGamePad(ptr, width,height, mainmenu_stamp);
            SetSpriteDither(0);
        }
        else
        if (game_loading != 1) // hide menu while 'loading'
        {
            SetSpriteDither(mainmenu_dither>>1);
            logo_space = mainmenu_context.Paint(ptr,width,height);
            SetSpriteDither(0);
        }

        if (logo_space && menu_logo_sprite && menu_logo_sprite->atlas)
        {
            int x = 5, y = height - 5;
            // Font1Paint(ptr,width,height, x,y, "ASCIICKER", FONT1_GOLD_SKIN);
            int logo_x = (width - menu_logo_sprite->atlas->width) / 2;
            // int logo_y = (height - menu_logo_sprite->atlas->height) / 2;
            BlitSprite(ptr,width,height,menu_logo_sprite->atlas, logo_x,y-menu_logo_sprite->atlas->height/2);
        }
    }

    // [FLOW:ENTITY] Level loading state machine (game_loading == 1)
    if (game_loading == 1)
    {
        printf("[MM] game_loading=1 progress=%d\n", mainmenu_context.progress);
        LoadGame(&mainmenu_context); // here we should provide manifest index
#ifdef __EMSCRIPTEN__
        if ((server || WebAuthoritativeJoinActive()) && mainmenu_context.progress == 0)
        {
            WebFL933ServerPointerWatch("MainMenu_Render:before-WebFlushPendingNetPacketsToServer", game,
                                       game ? (uint32_t)sizeof(Game) : 0,
                                       player_head, player_tail);
            WebFL933AssertAuthoritativeServerPresent("MainMenu_Render:before-WebFlushPendingNetPacketsToServer", game,
                                                     game ? (uint32_t)sizeof(Game) : 0,
                                                     player_head, player_tail);
            WebFlushPendingNetPacketsToServer();
        }
#endif
        printf("[MM] after LoadGame progress=%d\n", mainmenu_context.progress);
        // WHY: LoadGame() updates mainmenu_context.progress as it loads

        int y = 5;
        int w,h;
        Font1Size("LOADING",&w,&h);
        Font1Paint(ptr,width,height, (width-w)/2,y, "LOADING", FONT1_PINK_SKIN);
        y -= h;

        // assume we have fixed number of progress dots
        char pro[]=".........."; // WHY: 10 dots provide fine-grained visual progress feedback
        Font1Size(pro,&w,&h);
        if (mainmenu_context.progress == 3)
        {
            // fully loaded - WHY: progress==3 means finalization stage (almost done)
            Font1Paint(ptr,width,height, (width-w)/2, y, pro, FONT1_GOLD_SKIN);
        }
        else
        if (mainmenu_context.progress == 2 && mainmenu_context.patch_num)
        {
            // loading in progress ... - WHY: progress==2 means loading patches (bulk of loading)
            int dots = strlen(pro);
            int dot_w = w/dots;
            // WHY: calculate proportional fill based on patch_iter / patch_num ratio
            int complete = (dots * mainmenu_context.patch_iter + mainmenu_context.patch_num / 2) / mainmenu_context.patch_num;

            if (complete < dots)
            {
                pro[complete] = 0; // WHY: null-terminate to paint only completed portion in gold
                Font1Paint(ptr,width,height, (width-w)/2,y, pro, FONT1_GOLD_SKIN);
                pro[complete] = '.'; // WHY: restore dot for grey portion
                Font1Paint(ptr,width,height, (width-w)/2 + dot_w*complete,y, pro+complete, FONT1_GREY_SKIN);
            }
            else
                Font1Paint(ptr,width,height, (width-w)/2,y, pro, FONT1_GOLD_SKIN);
        }
        else
        {
            // no progress yet - WHY: progress==1 or progress==0 before loading starts
            Font1Paint(ptr,width,height, (width-w)/2, y, pro, FONT1_GREY_SKIN);
        }

        // [FLOW:ENTITY] Loading complete: transition game_loading 1->2
        if (mainmenu_context.progress == 0)
        {
            if (!MainMenuReadyToEnterLoadedWorld())
            {
                game_loading = 2;
                if (ObserveRenderEnabled())
                {
                    // RQ-11 Phase C: observe-render is a tooling mode. The native
                    // main-menu readiness predicate depends on an authoritative pose
                    // that may never become "ready" while ui.main_menu is still true
                    // (Game::Render returns early). Bypass the gate so the world
                    // renderer can run and emit the committed source-shot artifacts.
                    printf("[observe-render] bypassing authoritative readiness gate; entering world render loop\n");
                    fflush(stdout);
                    game->ui.main_menu = false;
                    mainmenu_context.Init();
                }
                else
                {
                    static int auth_wait_logs = 0;
                    if (auth_wait_logs < 24)
                    {
                        printf("[MM] loaded world waiting for authoritative readiness local_id=%d snap_seq=%u snap_tick=%u pose=%d\n",
                            server ? server->connection.local_id : -1,
                            server ? (unsigned)server->authority.snapshot_client.last_snapshot_seq : 0u,
                            server ? (unsigned)server->authority.snapshot_client.last_snapshot_tick : 0u,
                            LocalPlayerAuthoritativePoseReady(game->player, server != nullptr) ? 1 : 0);
                        fflush(stdout);
                        auth_wait_logs++;
                    }
                }
            }
            else
            {
                // fully loaded!!! - WHY: progress counts DOWN 3->2->1->0, so 0 means done
                game_loading = 2; // WHY: transition to loaded state (1->2, enables show_continue)
                game->ui.main_menu = false; // WHY: exit main menu, start gameplay
                if (!server)
                    printf("[GAME_STATE] ENTERED_WORLD\n");

                // prepare in advance for getting back to the main menu
                mainmenu_context.Init(); // WHY: reset menu state for future menu access
            }
        }
    }

    if (mainmenu_shot)
    {
        mainmenu_shot = false;
        char shot_xp_path[1024 + 20];
        sprintf(shot_xp_path, "%sshot.xp", base_path);
        FILE* f = fopen(shot_xp_path, "wb");
        if (f)
        {
            uint32_t hdr[4] = { (uint32_t)-1, (uint32_t)1, (uint32_t)width, (uint32_t)height };
            fwrite(hdr, sizeof(uint32_t), 4, f);
            for (int x = 0; x < width; x++)
            {
                for (int y = height - 1; y >= 0; y--)
                {
                    AnsiCell* c = ptr + y * width + x;
                    int fg = c->fg - 16;
                    int f_r = (fg % 6) * 51; fg /= 6;
                    int f_g = (fg % 6) * 51; fg /= 6;
                    int f_b = (fg % 6) * 51; fg /= 6;

                    int bk = c->bk - 16;
                    int b_r = (bk % 6) * 51; bk /= 6;
                    int b_g = (bk % 6) * 51; bk /= 6;
                    int b_b = (bk % 6) * 51; bk /= 6;

                    uint8_t f_rgb[3] = { (uint8_t)f_b,(uint8_t)f_g,(uint8_t)f_r };
                    uint8_t b_rgb[3] = { (uint8_t)b_b,(uint8_t)b_g,(uint8_t)b_r };
                    uint32_t chr = c->gl;

                    fwrite(&chr, sizeof(uint32_t), 1, f);
                    fwrite(f_rgb, 1, 3, f);
                    fwrite(b_rgb, 1, 3, f);
                }
            }

            fclose(f);
            WriteMainMenuShotJson(_stamp, width, height);
        }
    }
}

// WHY: Open main menu with dither fade-in animation
// Purpose: Initialize dither counter to trigger fade-in effect when menu appears.
// Currently unused (menu is always visible), but preserved for future feature.
// [FLOW:ENTITY] Menu open (currently unused - menu always visible)
void MainMenu_Show()
{
    if (MakeStamp)
        mainmenu_stamp = dither_stamp = MakeStamp(); // WHY: sync timestamps for animation timing
    mainmenu_dither = 2*mainmenu_dither_hidden; // WHY: reset dither to max (triggers fade-in animation)
}

void MainMenu_OnSize(int w, int h, int fw, int fh)
{
    mainmenu_context.OnSize(w,h,fw,fh);
}

void MainMenu_OnKeyb(GAME_KEYB keyb, int key)
{
    if (keyb == GAME_KEYB::KEYB_DOWN)
    {
        if (key == A3D_F10)
        {
            mainmenu_shot = true;
        }
    }

    if (show_gamepad)
    {
		int k = -1;
		switch (keyb)
		{
			case GAME_KEYB::KEYB_CHAR:
			{
				switch (key)
				{
					case ' ': k = 0; break;
					case '\n': k = 1; break;
					case 8:
					case '\\':
					case 27: k = 2; break;

					default:
						if (key>32 && key<127)
							k = key;
				}
				break;
			}

			case GAME_KEYB::KEYB_PRESS:
			case GAME_KEYB::KEYB_DOWN:
			{
				switch (key)
				{
					case A3D_ENTER: k = 1; break;
					case A3D_ESCAPE: k = 2; break;
					case A3D_UP: k = 3; break;
					case A3D_DOWN: k = 4; break;
					case A3D_LEFT: k = 5; break;
					case A3D_RIGHT: k = 6; break;
				}
				break;
			}

			default:
				break;
		}

		if (k>=0)
			GamePadKeyb(k, mainmenu_stamp);
    }
    else
    if (game_loading==0 || game_loading==2)
        mainmenu_context.OnKeyb(keyb,key);
}

void MainMenu_OnMouse(GAME_MOUSE mouse, int x, int y)
{
    if (show_gamepad)
    {
		int ev = -1;
		switch (mouse)
		{
			case GAME_MOUSE::MOUSE_LEFT_BUT_DOWN: ev = 0; break;
			case GAME_MOUSE::MOUSE_MOVE: ev = 1; break;
			case GAME_MOUSE::MOUSE_LEFT_BUT_UP: ev = 2; break;

			default:
				break;
		}

		if (ev>=0)
		{
			int p[2] = {x,y};
			mainmenu_context.ScreenToCell(p);
			GamePadContact(0,ev,p[0],p[1], mainmenu_stamp);
		}
    }
    else
    if (game_loading==0 || game_loading==2)
        mainmenu_context.OnMouse(mouse,x,y);
}

void MainMenu_OnTouch(GAME_TOUCH touch, int id, int x, int y)
{
    if (show_gamepad)
    {
		int ev = -1;
		switch (touch)
		{
			case GAME_TOUCH::TOUCH_BEGIN: ev = 0; break;
			case GAME_TOUCH::TOUCH_MOVE: ev = 1; break;
			case GAME_TOUCH::TOUCH_END: ev = 2; break;
			case GAME_TOUCH::TOUCH_CANCEL: ev = 3; break;
		}

		if (ev>=0)
		{
			int p[2] = {x,y};
			mainmenu_context.ScreenToCell(p);
			GamePadContact(id,ev,p[0],p[1], mainmenu_stamp);
		}
    }
    else
    if (game_loading==0 || game_loading==2)
        mainmenu_context.OnTouch(touch,id,x,y);
}

void MainMenu_OnFocus(bool set)
{
    if (game_loading==0 || game_loading==2)
        mainmenu_context.OnFocus(set);
}

void MainMenu_OnPadMount(bool connect)
{
    if (!show_gamepad && (game_loading==0 || game_loading==2))
        mainmenu_context.OnPadMount(connect);
}

void MainMenu_OnPadButton(int b, bool down)
{
    if (!show_gamepad && (game_loading==0 || game_loading==2))
        mainmenu_context.OnPadButton(b,down);
}

void MainMenu_OnPadAxis(int a, int16_t pos)
{
    if (!show_gamepad && (game_loading==0 || game_loading==2))
        mainmenu_context.OnPadAxis(a,pos);
}


/////////////////////////

#ifndef SERVER
bool NextGLFont();
bool PrevGLFont();
void ToggleFullscreen(Game* g);
bool IsFullscreen(Game* g);
#endif

static void main_menu_zoomin(MainMenuContext* m)
{
	#ifndef SERVER
	if (NextGLFont())
        mainmenu_dither = mainmenu_dither_hidden;
	#endif
}

static void main_menu_zoomout(MainMenuContext* m)
{
	#ifndef SERVER
	if (PrevGLFont())
        mainmenu_dither = mainmenu_dither_hidden;
	#endif
}

static void main_menu_fullscreen(MainMenuContext* m)
{
    mainmenu_dither = mainmenu_dither_hidden;

	#ifndef SERVER
    bool was = IsFullscreen(game);
	ToggleFullscreen(game);

    // warning: on web IsFullscreen can be late!
    // we should rather listen on the event!
    /* 
    if (was != IsFullscreen())
        mainmenu_dither = mainmenu_dither_hidden * 2;
    */

	#endif
}

static bool main_menu_fullscreen_getter(MainMenuContext* m)
{
	#ifndef SERVER
    bool current = IsFullscreen(game);
    static bool cash = current;
    if (cash != current)
    {
        cash = current;
        mainmenu_dither = mainmenu_dither_hidden * 2;
    }
	return current;
	#endif
	return false;
}

static void main_menu_perspective(MainMenuContext* m)
{
	game->session.perspective = !game->session.perspective;
	WriteConf(game);
}

static bool main_menu_perspective_getter(MainMenuContext* m)
{
	return game->session.perspective;
}

static void main_menu_blood(MainMenuContext* m)
{
	game->session.blood = !game->session.blood;
	WriteConf(game);
}

static bool main_menu_blood_getter(MainMenuContext* m)
{
	return game->session.blood;
}

static const MainMenu main_menu_video[]=
{
	{"ZOOM IN", 0, main_menu_zoomin, 0, /*cookie*/0},
	{"ZOOM OUT", 0, main_menu_zoomout, 0, /*cookie*/0},
	{"FULL SCREEN", 0, main_menu_fullscreen, main_menu_fullscreen_getter, /*cookie*/0},
	{"PERSPECTIVE", 0, main_menu_perspective, main_menu_perspective_getter, /*cookie*/0},
	{"SHOW BLOOD", 0, main_menu_blood, main_menu_blood_getter, /*cookie*/0},
	{0}
};

static void main_menu_gamepad_close(void* cookie)
{
    // hide gamepad, unhide mainmenu
    mainmenu_dither = mainmenu_dither_hidden;
    show_gamepad = false;
}

static void main_menu_gamepad(MainMenuContext* m)
{
	//game->CloseMenu();
	//g->show_gamepad = true;
	//g->show_buts = false;

    mainmenu_dither = mainmenu_dither_hidden;
    show_gamepad = true;
	GamePadOpen(main_menu_gamepad_close,0);
}

static const MainMenu main_menu_controls[]=
{
	{"KEYBOARD", 0, 0, 0, /*cookie*/0},
	{"MOUSE", 0, 0, 0, /*cookie*/0},
	{"TOUCH", 0, 0, 0, /*cookie*/0},
	{"GAMEPAD", 0, main_menu_gamepad, 0, /*cookie*/0},
	{0}
};

static void main_menu_no_exit(MainMenuContext* m)
{
    mainmenu_dither = mainmenu_dither_hidden;
	m->menu_depth--;
	m->menu_temp = mainmenu_context.menu_stack[game->ui.menu_depth];

    // TODO:
    // update scroll and smooth scroll so menu_temp appears fully visible
    // ...
}

void exit_handler(int signum);
static void main_menu_yes_exit(MainMenuContext* m)
{
	#ifdef USE_SDL
	exit(0);
	#else
	exit_handler(0);
	#endif
}

static const MainMenu main_menu_exit[]=
{
	{"NO", 0, main_menu_no_exit, 0, /*cookie*/0},
	{"YES", 0, main_menu_yes_exit, 0, /*cookie*/0},
	{0}
};

static void main_menu_continue(MainMenuContext* m)
{
    /* get back to the game (without loading) !!!*/
    if (game_loading==2)
        game->ui.main_menu = false;
    
    // prepare in advance for getting back to the main menu
    m->Init();
}

static void main_menu_mute(MainMenuContext* m)
{
	game->session.mute = !game->session.mute;
	AudioMute(game->session.mute);
	WriteConf(game);
}

static bool main_menu_mute_getter(MainMenuContext* m)
{
    return game->session.mute;
}

// --- TOOLS submenu (desktop only) ---
#ifndef __EMSCRIPTEN__

static void launch_tool(const char* label, const char* cmd)
{
#if defined(__APPLE__)
	// osascript reliably opens a new Terminal.app window
	char buf[512];
	snprintf(buf, sizeof(buf),
		"osascript -e 'tell application \"Terminal\" to do script \"cd %s && %s\"' &",
		base_path, cmd);
	int ret = system(buf);
#elif defined(__linux__)
	char buf[512];
	snprintf(buf, sizeof(buf), "%s &", cmd);
	int ret = system(buf);
#else
	char buf[512];
	snprintf(buf, sizeof(buf), "start %s", cmd);
	int ret = system(buf);
#endif
	if (ret != 0)
		printf("TOOLS: failed to launch %s (exit code %d)\n", label, ret);
}

static void launch_asciiid(MainMenuContext* m)
{
	launch_tool("asciiid", "./.run/asciiid");
}

static void launch_xp_editor(MainMenuContext* m)
{
	launch_tool("XP editor", "python3 -m scripts.asset_gen.xp_tool");
}

static void launch_asset_gen(MainMenuContext* m)
{
	launch_tool("asset generator", "python3 -m scripts.asset_gen.cli --tui");
}

static const MainMenu main_menu_tools[] =
{
	{"WORLD EDITOR", 0, launch_asciiid, 0, /*cookie*/0},
	{"XP SPRITE EDITOR", 0, launch_xp_editor, 0, /*cookie*/0},
	{"ASSET GENERATOR", 0, launch_asset_gen, 0, /*cookie*/0},
	{0}
};

#endif // __EMSCRIPTEN__

static const MainMenu mainmenu_root[]=
{
    // this is optional (must be first entry!)
    // MainMenuGetRoot() will skip it if !show_continue
	{"CONTINUE", 0, main_menu_continue, 0, /*cookie*/0},

	{"NEW GAME", 0, start_new_game, 0, /*cookie*/0},
	{"VIDEO", main_menu_video, 0, 0, /*cookie*/0},
	{"CONTROLS", main_menu_controls, 0, 0, /*cookie*/0},
#ifndef __EMSCRIPTEN__
	{"TOOLS", main_menu_tools, 0, 0, /*cookie*/0},
#endif
	{"MUTE SOUND", 0, main_menu_mute, main_menu_mute_getter, /*cookie*/0},
	{"EXIT?", main_menu_exit, 0, 0, /*cookie*/0},
	{0}
};

static const MainMenu* MainMenuGetRoot()
{
    return mainmenu_root + (show_continue ? 0 : /*skip*/1);
}

//////////////////////////////////////////////////////////////

void MainMenuContext::OnFocus(bool set)
{
}

void MainMenuContext::OnSize(int w, int h, int fw, int fh)
{
    input_size[0] = w;
    input_size[1] = h;
    font_size[0] = fw;
    font_size[1] = fh;
}

void MainMenuContext::OnKeyb(GAME_KEYB keyb, int key)
{
	printf("[MENU_DEBUG] OnKeyb keyb=%d key=%d depth=%d stack[0]=%d loading=%d\n",
		(int)keyb, key, menu_depth, menu_stack[0], game_loading);
	if (menu_down)
		return; // captured by mouse/touch - WHY: prevent input conflicts when mouse/touch is active

	if (keyb==KEYB_DOWN && (key==A3D_ENTER || key==A3D_NUMPAD_ENTER))
	{
		// handle only char->press! - WHY: KEYB_DOWN is filtered to avoid double-handling enter key
		return;
	}

	// [FLOW:ENTITY] ESC key resets to root menu (depth=0)
	if (keyb==KEYB_CHAR && (key=='\\' || key=='|') ||
		(keyb==KEYB_DOWN || keyb==KEYB_PRESS) && key==A3D_ESCAPE)
	{
        // THERE'S NO CLOSING MAIN MENU - WHY: menu is always visible in current design
		// CloseMenu();

        // mainmenu_context.Init();
        mainmenu_context.Root(true); // WHY: Root(true) resets to depth=0 with default highlight

		return;
	}

	if (keyb==KEYB_CHAR && key==8)
	{
		keyb=KEYB_PRESS;
		key=A3D_BACKSPACE;
	}

	if (keyb==KEYB_CHAR && (key=='\n' || key=='\r'))
	{
		keyb=KEYB_PRESS;
		key=A3D_ENTER;
	}

	if (keyb==KEYB_DOWN || keyb==KEYB_PRESS)
	{
		// WHY: Walk menu tree from root to current depth to get current level's menu array
		const MainMenu* m = MainMenuGetRoot();
		for (int d=0; d<menu_depth; d++)
			m = m[ menu_stack[d] ].sub;

		if (menu_stack[menu_depth]>=0)
		{
			// [FLOW:ENTITY] ENTER or RIGHT on submenu: enter submenu (menu_depth++)
			// [FLOW:ENTITY] ENTER on action: execute action callback
			if (key==A3D_RIGHT && m[ menu_stack[menu_depth] ].sub || key==A3D_ENTER)
			{
                menu_rescroll = true; // WHY: trigger auto-scroll to make new selection visible
				if (m[ menu_stack[menu_depth] ].sub)
				{
                    // WHY: reset dither for visual transition, reset scroll for new submenu
                    mainmenu_dither = mainmenu_dither_hidden;
                    menu_scroll=0;
                    menu_smooth_scroll=0;
					menu_depth++; // WHY: descend into submenu (stack push)
					menu_stack[menu_depth]=0; // WHY: highlight first item in new submenu
					menu_temp = menu_stack[menu_depth]; // WHY: sync temp with keyboard position
				}
				else
				if (m[ menu_stack[menu_depth] ].action)
				{
					// WHY: leaf item with action - execute callback (e.g., start_new_game)
					m[ menu_stack[menu_depth] ].action(this);
				}
				return;
			}
		}
		else
		if (key==A3D_RIGHT || key==A3D_ENTER)
		{
			// WHY: menu_stack[depth]==-1 means mouse hover (no keyboard highlight)
			// ENTER/RIGHT restores keyboard highlight from menu_temp
			menu_stack[menu_depth]=menu_temp;
		}

		// [FLOW:ENTITY] LEFT or BACKSPACE: pop menu level (menu_depth--)
		if (key==A3D_LEFT || keyb==KEYB_PRESS && key==A3D_BACKSPACE)
		{
			if (menu_depth==0)
			{
                // THERE'S NO CLOSING MAIN MENU - WHY: can't go back from root (already at top)
                // CloseMenu();
				return;
			}

            // WHY: reset dither for visual transition when popping level
            mainmenu_dither = mainmenu_dither_hidden;
			menu_depth--; // WHY: ascend to parent menu (stack pop)
			menu_temp = menu_stack[menu_depth]; // WHY: restore keyboard position from parent level

            // TODO:
            // update scroll and smooth scroll so menu_temp appears fully visible
            // ...
			return;
		}

		// [FLOW:ENTITY] DOWN key: navigate down in menu
		if (key==A3D_DOWN)
		{
            menu_rescroll = true; // WHY: trigger auto-scroll to keep highlighted item visible
			if (menu_stack[menu_depth] < 0)
				menu_stack[menu_depth] = menu_temp; // WHY: restore keyboard highlight from temp
			else
			if (m[menu_stack[menu_depth]+1].str)
			{
				menu_stack[menu_depth]++; // WHY: move highlight down one item
				menu_temp = menu_stack[menu_depth]; // WHY: sync temp with new keyboard position
			}
			return;
		}

		// [FLOW:ENTITY] UP key: navigate up in menu
		if (key==A3D_UP)
		{
            menu_rescroll = true; // WHY: trigger auto-scroll to keep highlighted item visible
			if (menu_stack[menu_depth] < 0)
				menu_stack[menu_depth] = menu_temp; // WHY: restore keyboard highlight from temp
			else
			if (menu_stack[menu_depth]>0)
			{
				menu_stack[menu_depth]--; // WHY: move highlight up one item
				menu_temp = menu_stack[menu_depth]; // WHY: sync temp with new keyboard position
			}
			return;
		}
	}
}

void MainMenuContext::OnMouse(GAME_MOUSE mouse, int x, int y)
{
	if (menu_down==2)
		return; // captured by touch - WHY: menu_down states are mutually exclusive (0=released, 1=mouse, 2=touch)

    // [FLOW:ENTITY] Mouse wheel scrolling (direct scroll manipulation)
    if (mouse == GAME_MOUSE::MOUSE_WHEEL_DOWN && !menu_down)
    {
        // WHY: scroll by 5 pixels per wheel notch for responsive feel
        if (menu_scroll < menu_max_scroll - 5)
            menu_scroll += 5;
        else
            menu_scroll = menu_max_scroll;
    }

    if (mouse == GAME_MOUSE::MOUSE_WHEEL_UP && !menu_down)
    {
        // WHY: scroll by 5 pixels per wheel notch for responsive feel
        if (menu_scroll > 5)
            menu_scroll -= 5;
        else
            menu_scroll = 0;
    }

	if (mouse == GAME_MOUSE::MOUSE_MOVE)
	{
		if (menu_down)
		{
			// retest - WHY: update highlight as mouse drags over different items
			int hit = HitMenu(x,y);
			if (hit != menu_stack[menu_depth])
				menu_stack[menu_depth] = -1; // WHY: set to -1 (no highlight) when not over original item

            // handle scroll up/dn - WHY: allow drag-scroll when mouse is down
            int cp[2] = { x, y };
            ScreenToCell(cp);

            int prev = menu_scroll;
            menu_scroll += (cp[1] - menu_scroll) - menu_down_y; // WHY: drag-based scrolling
            if (menu_scroll > menu_max_scroll)
                menu_scroll = menu_max_scroll;
            if (menu_scroll < 0)
                menu_scroll = 0;

            if (prev != menu_scroll)
            {
                menu_stack[menu_depth] = -1; // WHY: clear highlight during scroll drag
            }
		}
	}

	// [FLOW:ENTITY] Mouse button down: capture mouse, set menu_down=1
	if (mouse == GAME_MOUSE::MOUSE_LEFT_BUT_DOWN)
	{
		menu_down = 1; // WHY: set menu_down=1 (mouse_captured state) to block keyboard/touch input

        int cp[2] = { x, y };
        ScreenToCell(cp);

        menu_down_x = cp[0]; // WHY: store initial mouse position for drag-scroll tracking
        menu_down_y = cp[1] - menu_scroll; // WHY: store scroll-relative Y for drag-scroll

		int hit = HitMenu(x,y);
        down_back = hit == -1; // WHY: track if user clicked "back" area (hit=-1 is back item)

        /*
		if (hit<-1)
		{
            // THERE'S NO CLOSING MAIN MENU
            // CloseMenu();

            //mainmenu_context.Init();
            mainmenu_context.Root(false);
			return;
		}
        */

		if (hit>=0)
		{
			menu_stack[menu_depth]=hit; // WHY: set highlight to clicked item
			menu_temp = menu_stack[menu_depth]; // WHY: preserve mouse selection in menu_temp for keyboard restoration
		}
		else
			menu_stack[menu_depth]=-1; // WHY: set to -1 (no highlight) when clicking outside items

		return;
	}

	// [FLOW:ENTITY] Mouse button up: execute action if same item, release capture
	if (mouse == GAME_MOUSE::MOUSE_LEFT_BUT_UP)
	{
		if (menu_down)
		{
			// retest - WHY: verify mouse is still over same item (click-and-drag cancellation)
			int hit = HitMenu(x,y);
			if (hit == menu_stack[menu_depth])
			{
				// [FLOW:ENTITY] Click on back item: pop menu level (menu_depth--)
				if (hit==-1 && down_back)
				{
					// go back - WHY: hit=-1 is the "back" item (shown as left arrow)
					if (menu_depth==0)
					{
                        // THERE'S NO CLOSING MAIN MENU - WHY: can't go back from root
                        // CloseMenu();
						return;
					}
					else
					{
                        mainmenu_dither = mainmenu_dither_hidden; // WHY: reset dither for visual transition
						menu_depth--; // WHY: pop menu stack (ascend to parent level)
						menu_temp = menu_stack[menu_depth]; // WHY: restore parent level's highlight

                        // TODO:
                        // update scroll and smooth scroll so menu_temp appears fully visible
                        // ...
					}
				}
				// [FLOW:ENTITY] Click on menu item: enter submenu or execute action
				else
				if (hit>=0)
				{
					const MainMenu* m = MainMenuGetRoot();
					for (int d=0; d<menu_depth; d++)
						m = m[ menu_stack[d] ].sub;

					// action! - WHY: execute on mouse up (not down) for standard UI behavior
                    menu_rescroll = true;

					if (m[ menu_stack[menu_depth] ].sub)
					{
                        // WHY: descend into submenu (same logic as keyboard ENTER)
                        mainmenu_dither = mainmenu_dither_hidden;
                        menu_scroll=0;
                        menu_smooth_scroll=0;
						menu_depth++; // WHY: push menu stack (descend to submenu)
						menu_stack[menu_depth]=-1; // WHY: clear next highlight (-1 = no keyboard highlight, mouse controls)
						menu_temp = 0; // WHY: reset menu_temp for new submenu level
					}
					else
					if (m[ menu_stack[menu_depth] ].action)
					{
						// WHY: leaf item with action - execute callback (e.g., start_new_game)
						m[ menu_stack[menu_depth] ].action(this);
					}
				}
			}
		}

		menu_down = 0; // WHY: release mouse capture (menu_down=0 means released state)
		menu_stack[menu_depth]=-1; // WHY: clear highlight after mouse release (mouse hovers don't persist highlight)
	}
}

void MainMenuContext::OnTouch(GAME_TOUCH touch, int id, int x, int y)
{
	if (menu_down==1)
		return; // captured by mouse - WHY: menu_down states are mutually exclusive (0=released, 1=mouse, 2=touch)

	if (id==1) // WHY: only handle first touch point (id=1) for menu interaction
	{
		switch(touch)
		{
			// [FLOW:ENTITY] Touch begin: capture touch, set menu_down=2
			case GAME_TOUCH::TOUCH_BEGIN:
			{
				menu_down = 2; // WHY: set menu_down=2 (touch_captured state) to block keyboard/mouse input

                int cp[2] = { x, y };
                ScreenToCell(cp);

                menu_down_x = cp[0]; // WHY: store initial touch position for drag-scroll tracking
                menu_down_y = cp[1] - menu_scroll; // WHY: store scroll-relative Y for drag-scroll

                int hit = HitMenu(x,y);
                down_back = hit == -1; // WHY: track if user touched "back" area (hit=-1 is back item)
                /*
				if (hit<-1)
				{
                    // THERE'S NO CLOSING MAIN MENU
                    // CloseMenu();

                    //mainmenu_context.Init();
                    mainmenu_context.Root(false);
					return;
				}
                */

				if (hit>=0)
				{
					menu_stack[menu_depth]=hit; // WHY: set highlight to touched item
					menu_temp = menu_stack[menu_depth]; // WHY: preserve touch selection in menu_temp
				}
				else
					menu_stack[menu_depth]=-1; // WHY: set to -1 (no highlight) when touching outside items

				break;
			}

			case GAME_TOUCH::TOUCH_MOVE:
				if (menu_down)
				{
                    // handle scroll up/dn - WHY: allow drag-scroll when touch is active
                    int cp[2] = { x, y };
                    ScreenToCell(cp);

                    int prev = menu_scroll;
                    menu_scroll += (cp[1] - menu_scroll) - menu_down_y; // WHY: drag-based scrolling
                    if (menu_scroll > menu_max_scroll)
                        menu_scroll = menu_max_scroll;
                    if (menu_scroll < 0)
                        menu_scroll = 0;

                    if (prev != menu_scroll)
                    {
                        menu_stack[menu_depth] = -1; // WHY: clear highlight during scroll drag
                    }

					// retest - WHY: update highlight as touch drags over different items
					int hit = HitMenu(x,y);
					if (hit != menu_stack[menu_depth])
						menu_stack[menu_depth] = -1; // WHY: set to -1 (no highlight) when not over original item
				}
				break;

			// [FLOW:ENTITY] Touch end: execute action if same item, release capture
			case GAME_TOUCH::TOUCH_END:
			{
				if (menu_down)
				{
					// retest - WHY: verify touch is still over same item (touch-and-drag cancellation)
					int hit = HitMenu(x,y);
					if (hit == menu_stack[menu_depth])
					{
						// [FLOW:ENTITY] Touch on back item: pop menu level (menu_depth--)
						if (hit==-1 && down_back)
						{
							// go back - WHY: hit=-1 is the "back" item (shown as left arrow)
							if (menu_depth==0)
							{
                                // THERE'S NO CLOSING MAIN MENU - WHY: can't go back from root
                                // CloseMenu();
								return;
							}
							else
							{
                                mainmenu_dither = mainmenu_dither_hidden; // WHY: reset dither for visual transition
								menu_depth--; // WHY: pop menu stack (ascend to parent level)
								menu_temp = menu_stack[menu_depth]; // WHY: restore parent level's highlight

                                // TODO:
                                // update scroll and smooth scroll so menu_temp appears fully visible
                                // ...
							}
						}
						// [FLOW:ENTITY] Touch on menu item: enter submenu or execute action
						else
						if (hit>=0)
						{
							const MainMenu* m = MainMenuGetRoot();
							for (int d=0; d<menu_depth; d++)
								m = m[ menu_stack[d] ].sub;

                            menu_rescroll = true;

							// action! - WHY: execute on touch end (not begin) for standard UI behavior
							if (m[ menu_stack[menu_depth] ].sub)
							{
                                // WHY: descend into submenu (same logic as keyboard ENTER)
                                mainmenu_dither = mainmenu_dither_hidden;
                                menu_scroll=0;
                                menu_smooth_scroll=0;
								menu_depth++; // WHY: push menu stack (descend to submenu)
								menu_stack[menu_depth]=-1; // WHY: clear next highlight (-1 = no keyboard highlight, touch controls)
								menu_temp = 0; // WHY: reset menu_temp for new submenu level
							}
							else
							if (m[ menu_stack[menu_depth] ].action)
							{
								// WHY: leaf item with action - execute callback (e.g., start_new_game)
								m[ menu_stack[menu_depth] ].action(this);
							}
						}
					}
				}

				menu_down = 0;
				menu_stack[menu_depth]=-1;				
				break;
			}

			case GAME_TOUCH::TOUCH_CANCEL:
				menu_down = 0;
				menu_stack[menu_depth]=-1;
				break;
		}
	}
}

void MainMenuContext::OnPadMount(bool connected)
{

}

void MainMenuContext::OnPadButton(int b, bool down)
{
	if (menu_down)
		return; // captured by mouse/touch

	if (!down)
		return;

	const MainMenu* m = MainMenuGetRoot();
	for (int d=0; d<menu_depth; d++)
		m = m[ menu_stack[d] ].sub;		

	switch (b)
	{
		case 0:
		{
			if (menu_stack[menu_depth]>=0)
			{
                menu_rescroll = true;

				if (m[ menu_stack[menu_depth] ].sub)
				{
                    mainmenu_dither = mainmenu_dither_hidden;
                    menu_scroll=0;
                    menu_smooth_scroll=0;
					menu_depth++;
					menu_stack[menu_depth]=0;
					menu_temp = menu_stack[menu_depth];
				}
				else
				if (m[ menu_stack[menu_depth] ].action)
				{
					m[ menu_stack[menu_depth] ].action(this);
				}
			}
			else
				menu_stack[menu_depth]=menu_temp;
			break;
		}

		case 1: 
		{
			// jump
			break;
		}

		case 5:
		{
			break;
		}

		case 6:
		{
            // THERE'S NO CLOSING MAIN MENU
            // CloseMenu();
            
            // mainmenu_context.Init();
            mainmenu_context.Root(true);
			break;
		}

		case 9:
		{
			// left shoulder
			break;
		}

		case 10:
		{
			// right shoulder
			break;
		}

		case 11:
		{
			// dir up
            menu_rescroll = true;
			if (menu_stack[menu_depth]<0)
				menu_stack[menu_depth]=menu_temp;
			else
			if (menu_stack[menu_depth]>0)
			{
				menu_stack[menu_depth]--;			
				menu_temp = menu_stack[menu_depth];
			}
			break;
		}
		case 12:
		{
			// dir down
            menu_rescroll = true;
			if (menu_stack[menu_depth]<0)
				menu_stack[menu_depth]=menu_temp;
			else
			if (m[menu_stack[menu_depth]+1].str)
			{
				menu_stack[menu_depth]++;			
				menu_temp = menu_stack[menu_depth];
			}
			break;
		}
		case 13:
		{
			// dir left
            menu_rescroll = true;
			if (menu_depth==0)
			{
                // THERE'S NO CLOSING MAIN MENU
                // CloseMenu();
				return;
			}

            mainmenu_dither = mainmenu_dither_hidden;
			menu_depth--;
			menu_temp = menu_stack[menu_depth];

            // TODO:
            // update scroll and smooth scroll so menu_temp appears fully visible
            // ...

			break;
		}
		case 14:
		{
			if (menu_stack[menu_depth]>=0)
			{
				// dir right
				// only sub, with dir_right
				// action requires main button
				if (m[ menu_stack[menu_depth] ].sub)
				{
                    mainmenu_dither = mainmenu_dither_hidden;
                    menu_scroll=0;
                    menu_smooth_scroll=0;
					menu_depth++;
					menu_stack[menu_depth]=0;
					menu_temp = menu_stack[menu_depth];
				}
			}
			else
				menu_stack[menu_depth]=menu_temp;
			break;
		}
	}
}

void MainMenuContext::OnPadAxis(int a, int16_t pos)
{
}
