// platform/backend/host_terminal_ansi.cpp — Terminal ANSI polling driver
// =============================================================================
//
// PURPOSE:
// Owns the PURE_TERM polling loop extracted from engine/game_app.cpp.
// Input parsing and ANSI presentation stay host-owned in this pass.
// Game lifecycle/render work is delegated through HostPollInterface.

#include "host_terminal_ansi.h"
#include "host_input.h"
#include "terminal_input_parser.h"

#include "a3d_load_context.h"
#include "audio.h"
#include "game.h"
#include "game_api.h"

#include <errno.h>
#include <math.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <unistd.h>

#ifdef USE_GPM
#include <gpm.h>
#endif

extern void Print(AnsiCell* buf, int w, int h, const char utf[256][4]);

#if defined(__linux__)
static int find_tty()
{
    char buf[256];
    char* ptr;
    FILE* f;
    int r;
    char stat;
    int ppid, pgrp, sess, tty_dev;

    int pid = getpid();
    while (pid > 0)
    {
        sprintf(buf, "/proc/%d/stat", pid);
        f = fopen(buf, "r");
        if (!f)
            return 0;
        r = (int)fread(buf, 1, 255, f);
        fclose(f);
        if (r <= 0)
            return 0;
        buf[r] = 0;
        ptr = strchr(buf, ')');
        if (!ptr || !ptr[1])
            return 0;
        r = sscanf(ptr + 2, "%c %d %d %d %d", &stat, &ppid, &pgrp, &sess, &tty_dev);
        if (r != 5)
            return 0;
        if ((tty_dev & ~63) == 1024 && (tty_dev & 63))
            return tty_dev & 63;
        pid = ppid;
    }
    return 0;
}
#else
static int find_tty()
{
    return 0;
}
#endif

template <uint16_t C> static int UTF8(char* buf)
{
    if (C < 0x0080)
    {
        buf[0] = C & 0xFF;
        return 1;
    }

    if (C < 0x0800)
    {
        buf[0] = (char)(0xC0 | ((C >> 6) & 0x1F));
        buf[1] = (char)(0x80 | (C & 0x3F));
        return 2;
    }

    buf[0] = (char)(0xE0 | ((C >> 12) & 0x0F));
    buf[1] = (char)(0x80 | ((C >> 6) & 0x3F));
    buf[2] = (char)(0x80 | (C & 0x3F));
    return 3;
}

static int (* const CP437[256])(char*) =
{
    UTF8<0x0020>, UTF8<0x263A>, UTF8<0x263B>, UTF8<0x2665>, UTF8<0x2666>, UTF8<0x2663>, UTF8<0x2660>, UTF8<0x2022>,
    UTF8<0x25D8>, UTF8<0x25CB>, UTF8<0x25D9>, UTF8<0x2642>, UTF8<0x2640>, UTF8<0x266A>, UTF8<0x266B>, UTF8<0x263C>,
    UTF8<0x25BA>, UTF8<0x25C4>, UTF8<0x2195>, UTF8<0x203C>, UTF8<0x00B6>, UTF8<0x00A7>, UTF8<0x25AC>, UTF8<0x21A8>,
    UTF8<0x2191>, UTF8<0x2193>, UTF8<0x2192>, UTF8<0x2190>, UTF8<0x221F>, UTF8<0x2194>, UTF8<0x25B2>, UTF8<0x25BC>,
    UTF8<0x0020>, UTF8<0x0021>, UTF8<0x0022>, UTF8<0x0023>, UTF8<0x0024>, UTF8<0x0025>, UTF8<0x0026>, UTF8<0x0027>,
    UTF8<0x0028>, UTF8<0x0029>, UTF8<0x002A>, UTF8<0x002B>, UTF8<0x002C>, UTF8<0x002D>, UTF8<0x002E>, UTF8<0x002F>,
    UTF8<0x0030>, UTF8<0x0031>, UTF8<0x0032>, UTF8<0x0033>, UTF8<0x0034>, UTF8<0x0035>, UTF8<0x0036>, UTF8<0x0037>,
    UTF8<0x0038>, UTF8<0x0039>, UTF8<0x003A>, UTF8<0x003B>, UTF8<0x003C>, UTF8<0x003D>, UTF8<0x003E>, UTF8<0x003F>,
    UTF8<0x0040>, UTF8<0x0041>, UTF8<0x0042>, UTF8<0x0043>, UTF8<0x0044>, UTF8<0x0045>, UTF8<0x0046>, UTF8<0x0047>,
    UTF8<0x0048>, UTF8<0x0049>, UTF8<0x004A>, UTF8<0x004B>, UTF8<0x004C>, UTF8<0x004D>, UTF8<0x004E>, UTF8<0x004F>,
    UTF8<0x0050>, UTF8<0x0051>, UTF8<0x0052>, UTF8<0x0053>, UTF8<0x0054>, UTF8<0x0055>, UTF8<0x0056>, UTF8<0x0057>,
    UTF8<0x0058>, UTF8<0x0059>, UTF8<0x005A>, UTF8<0x005B>, UTF8<0x005C>, UTF8<0x005D>, UTF8<0x005E>, UTF8<0x005F>,
    UTF8<0x0060>, UTF8<0x0061>, UTF8<0x0062>, UTF8<0x0063>, UTF8<0x0064>, UTF8<0x0065>, UTF8<0x0066>, UTF8<0x0067>,
    UTF8<0x0068>, UTF8<0x0069>, UTF8<0x006A>, UTF8<0x006B>, UTF8<0x006C>, UTF8<0x006D>, UTF8<0x006E>, UTF8<0x006F>,
    UTF8<0x0070>, UTF8<0x0071>, UTF8<0x0072>, UTF8<0x0073>, UTF8<0x0074>, UTF8<0x0075>, UTF8<0x0076>, UTF8<0x0077>,
    UTF8<0x0078>, UTF8<0x0079>, UTF8<0x007A>, UTF8<0x007B>, UTF8<0x007C>, UTF8<0x007D>, UTF8<0x007E>, UTF8<0x2302>,
    UTF8<0x00C7>, UTF8<0x00FC>, UTF8<0x00E9>, UTF8<0x00E2>, UTF8<0x00E4>, UTF8<0x00E0>, UTF8<0x00E5>, UTF8<0x00E7>,
    UTF8<0x00EA>, UTF8<0x00EB>, UTF8<0x00E8>, UTF8<0x00EF>, UTF8<0x00EE>, UTF8<0x00EC>, UTF8<0x00C4>, UTF8<0x00C5>,
    UTF8<0x00C9>, UTF8<0x00E6>, UTF8<0x00C6>, UTF8<0x00F4>, UTF8<0x00F6>, UTF8<0x00F2>, UTF8<0x00FB>, UTF8<0x00F9>,
    UTF8<0x00FF>, UTF8<0x00D6>, UTF8<0x00DC>, UTF8<0x00A2>, UTF8<0x00A3>, UTF8<0x00A5>, UTF8<0x20A7>, UTF8<0x0192>,
    UTF8<0x00E1>, UTF8<0x00ED>, UTF8<0x00F3>, UTF8<0x00FA>, UTF8<0x00F1>, UTF8<0x00D1>, UTF8<0x00AA>, UTF8<0x00BA>,
    UTF8<0x00BF>, UTF8<0x2310>, UTF8<0x00AC>, UTF8<0x00BD>, UTF8<0x00BC>, UTF8<0x00A1>, UTF8<0x00AB>, UTF8<0x00BB>,
    UTF8<0x2591>, UTF8<0x2592>, UTF8<0x2593>, UTF8<0x2502>, UTF8<0x2524>, UTF8<0x2561>, UTF8<0x2562>, UTF8<0x2556>,
    UTF8<0x2555>, UTF8<0x2563>, UTF8<0x2551>, UTF8<0x2557>, UTF8<0x255D>, UTF8<0x255C>, UTF8<0x255B>, UTF8<0x2510>,
    UTF8<0x2514>, UTF8<0x2534>, UTF8<0x252C>, UTF8<0x251C>, UTF8<0x2500>, UTF8<0x253C>, UTF8<0x255E>, UTF8<0x255F>,
    UTF8<0x255A>, UTF8<0x2554>, UTF8<0x2569>, UTF8<0x2566>, UTF8<0x2560>, UTF8<0x2550>, UTF8<0x256C>, UTF8<0x2567>,
    UTF8<0x2568>, UTF8<0x2564>, UTF8<0x2565>, UTF8<0x2559>, UTF8<0x2558>, UTF8<0x2552>, UTF8<0x2553>, UTF8<0x256B>,
    UTF8<0x256A>, UTF8<0x2518>, UTF8<0x250C>, UTF8<0x2588>, UTF8<0x2584>, UTF8<0x258C>, UTF8<0x2590>, UTF8<0x2580>,
    UTF8<0x03B1>, UTF8<0x00DF>, UTF8<0x0393>, UTF8<0x03C0>, UTF8<0x03A3>, UTF8<0x03C3>, UTF8<0x00B5>, UTF8<0x03C4>,
    UTF8<0x03A6>, UTF8<0x0398>, UTF8<0x03A9>, UTF8<0x03B4>, UTF8<0x221E>, UTF8<0x03C6>, UTF8<0x03B5>, UTF8<0x2229>,
    UTF8<0x2261>, UTF8<0x00B1>, UTF8<0x2265>, UTF8<0x2264>, UTF8<0x2320>, UTF8<0x2321>, UTF8<0x00F7>, UTF8<0x2248>,
    UTF8<0x00B0>, UTF8<0x2219>, UTF8<0x00B7>, UTF8<0x221A>, UTF8<0x207F>, UTF8<0x00B2>, UTF8<0x25A0>, UTF8<0x0020>
};

// xterm_kitty flag — moved from game_app.cpp; file-local to this adapter.
// True when TERM contains "xterm-kitty".  Affects terminal reset sequencing.
static bool xterm_kitty;

// ---------------------------------------------------------------------------
// SetScreen — terminal alternate-screen entry/exit
// ---------------------------------------------------------------------------
// Moved from engine/game_app.cpp.  Owned by this adapter.

void SetScreen(bool alt)
{
    // kitty kitty ...
    const char* term = getenv("TERM");
    if (term && strcmp(term,"xterm-kitty")==0)
    {
        int w = write(STDOUT_FILENO, alt?"\x1B[?2017h":"\x1B[?2017l", 8);
    }
    
    // // \x1B[?1002h only drags \x1B[?1003h all mouse events
    // \x1B[?1006h enable extended mouse encodings in SGR < Bc;Px;Pym|M format
    static const char* on_str = "\x1B[?1049h" "\x1B[H" "\x1B[?7l" "\x1B[?25l" "\x1B[?1002h" "\x1B[?1006h"; // +home, -wrap, -cursor, +mouse
    static const char* off_str = "\x1B[39m;\x1B[49m" "\x1B[?1049l" "\x1B[?7h" "\x1B[?25h" "\x1B[?1002l" "\x1B[?1006l"; // +def_fg/bg, +wrap, +cursor, -mouse
    static int on_len = strlen(on_str);
    static int off_len = strlen(off_str);

    static struct termios old;

    if (alt)
    {
        tcgetattr(STDIN_FILENO, &old);
        struct termios t = old;
        t.c_lflag |=  ISIG;
        t.c_iflag &= ~IGNBRK;
        t.c_iflag |=  BRKINT;
        t.c_lflag &= ~ICANON; /* disable buffered i/o */
        t.c_lflag &= ~ECHO; /* disable echo mode */

        tcsetattr(STDIN_FILENO, TCSANOW, &t);
        int w = write(STDOUT_FILENO,on_str,on_len);
    }
    else
    {
        tcsetattr(STDIN_FILENO, TCSANOW, &old);
        int w = write(STDOUT_FILENO,off_str,off_len);

        if (tty>=0)
        {
            int wh[2];
            GetWH(wh);
            char jump[64]; // jump to last line, reset palette then clear it line
            int len = sprintf(jump,"\x1B[%d;%df\x1B]R\x1B[2K",wh[1],1);
            w = write(STDOUT_FILENO,jump,len);
        }
    }
}

void a3dRunPolling(HostPollInterface* hpi, InputSink* sink)
{
    if (!hpi || !sink)
        return;
#if defined(__linux__) || defined(__APPLE__)

    MakeStamp = a3dGetTime;

    const char* term_env = getenv("TERM");
    if (!term_env)
        term_env = "";

    printf("TERM=%s\n", term_env);

    if (strcmp(term_env, "linux") == 0)
        tty = find_tty();

    if (tty > 0)
    {
        int errlvl;
        char cmd[1024 + 40];
        const char* temp_dir = getenv("SNAP_USER_DATA");
        if (!temp_dir || !temp_dir[0])
            temp_dir = "/tmp";
        sprintf(cmd, "setfont -O %s/asciicker.%d.psf;", temp_dir, tty);
        errlvl = system(cmd);

        sprintf(cmd, "setfont %sassets/fonts/cp437_%dx%d.png.psf", base_path, 14, 14);
        errlvl = system(cmd);

#ifdef USE_GPM
        Gpm_Connect conn;
        conn.eventMask = ~0;
        conn.defaultMask = 0;
        conn.minMod = 0;
        conn.maxMod = ~0;

        gpm_handler = 0;
        gpm_visiblepointer = 0;
        gpm = Gpm_Open(&conn, tty);

        if (gpm >= 0)
        {
            int wh[2];
            GetWH(wh);
            mouse_x = wh[0] / 2;
            mouse_y = wh[1] / 2;
            printf("connected to gpm\n");
        }
        else
        {
            printf("failed to connect to gpm\n");
        }
#endif
    }
    else if (strncmp(term_env, "xterm", 5) == 0)
    {
        printf("VIRTUAL TERMINAL EMULGLATOR\n");
    }
    else
    {
        printf("UNKNOWN TERMINAL\n");
    }

    int gamepad_axes = 0;
    int gamepad_buttons = 0;
    char gamepad_name[256] = {0};
    uint8_t gamepad_mapping[256] = {0};
    int jsfd = scan_js(gamepad_name, &gamepad_axes, &gamepad_buttons, gamepad_mapping);

    SetScreen(true);

    int signals[] = {SIGTERM, SIGHUP, SIGINT, SIGTRAP, SIGILL, SIGABRT, SIGKILL, 0};
    struct sigaction new_action, old_action;
    new_action.sa_handler = exit_handler;
    sigemptyset(&new_action.sa_mask);
    new_action.sa_flags = 0;

    for (int i = 0; signals[i]; i++)
    {
        sigaction(signals[i], NULL, &old_action);
        if (old_action.sa_handler != SIG_IGN)
            sigaction(signals[i], &new_action, NULL);
    }

    running = true;

    AnsiCell* buf = 0;
    int wh[2] = {-1, -1};

    uint64_t begin = a3dGetTime();
    uint64_t stamp = begin;
    uint64_t frames = 0;
    bool perf_init = false;
    bool perf_enabled = false;
    FILE* perf_out = nullptr;
    uint64_t perf_window_start = 0;
    uint64_t perf_render_sum = 0;
    uint64_t perf_print_sum = 0;
    int perf_frames = 0;

    char CP437_UTF8[256][4];
    for (int i = 0; i < 256; i++)
        CP437_UTF8[i][CP437[i](CP437_UTF8[i])] = 0;

    if (!hpi->Init())
        goto exit;

    if (jsfd >= 0)
        GamePadMount(gamepad_name, gamepad_axes, gamepad_buttons, gamepad_mapping);

    while (running)
    {
        if (jsfd < 0)
        {
            jsfd = scan_js(gamepad_name, &gamepad_axes, &gamepad_buttons, gamepad_mapping);
            if (jsfd >= 0)
                GamePadMount(gamepad_name, gamepad_axes, gamepad_buttons, gamepad_mapping);
        }

        uint64_t now = a3dGetTime();
        int dt = (int)(now - stamp);
        stamp = now;

        struct pollfd pfds[3] = {0};
        if (gpm >= 0)
        {
            pfds[0].fd = STDIN_FILENO;
            pfds[0].events = POLLIN;
            pfds[1].fd = gpm;
            pfds[1].events = POLLIN;

            if (jsfd >= 0)
            {
                pfds[2].fd = jsfd;
                pfds[2].events = POLLIN | POLLHUP | POLLERR;
                poll(pfds, 3, 0);

                if (pfds[2].revents & (POLLHUP | POLLERR))
                {
                    GamePadUnmount();
                    close(jsfd);
                    jsfd = -1;
                }
                else if (pfds[2].revents & POLLIN)
                {
                    if (!read_js(jsfd))
                    {
                        GamePadUnmount();
                        close(jsfd);
                        jsfd = -1;
                    }
                }
            }
            else
            {
                poll(pfds, 2, 0);
            }

#ifdef USE_GPM
            if (pfds[1].revents & POLLIN)
            {
                static int mouse_read = 0;
                static int mouse_write = 0;
                static Gpm_Event mouse_buf[64];
                int bytes = read(gpm, (char*)mouse_buf + mouse_write, 32 * sizeof(Gpm_Event));
                mouse_write += bytes;

                int events = mouse_write / (int)sizeof(Gpm_Event) - mouse_read;
                while (events)
                {
                    Gpm_Event* event = mouse_buf + mouse_read;
                    events--;
                    mouse_read++;

                    mouse_x += event->dx;
                    mouse_y += event->dy;

                    if (mouse_x >= wh[0])
                        mouse_x = wh[0] - 1;
                    if (mouse_x < 0)
                        mouse_x = 0;
                    if (mouse_y >= wh[1])
                        mouse_y = wh[1] - 1;
                    if (mouse_y < 0)
                        mouse_y = 0;

                    bool xy_processed = false;
                    if (event->wdy > 0)
                    {
                        xy_processed = true;
                        sink->OnMouse({MouseAction::Wheel, mouse_x, mouse_y, 0, +1});
                    }
                    else if (event->wdy < 0)
                    {
                        xy_processed = true;
                        sink->OnMouse({MouseAction::Wheel, mouse_x, mouse_y, 0, -1});
                    }

                    if (event->type & GPM_DOWN)
                    {
                        if (!(mouse_down & GPM_B_LEFT) && (event->buttons & GPM_B_LEFT))
                        {
                            xy_processed = true;
                            mouse_down |= GPM_B_LEFT;
                            sink->OnMouse({MouseAction::ButtonDown, mouse_x, mouse_y, 0, 0});
                        }
                        if (!(mouse_down & GPM_B_MIDDLE) && (event->buttons & GPM_B_MIDDLE))
                        {
                            xy_processed = true;
                            mouse_down |= GPM_B_MIDDLE;
                            sink->OnMouse({MouseAction::ButtonDown, mouse_x, mouse_y, 1, 0});
                        }
                        if (!(mouse_down & GPM_B_RIGHT) && (event->buttons & GPM_B_RIGHT))
                        {
                            xy_processed = true;
                            mouse_down |= GPM_B_RIGHT;
                            sink->OnMouse({MouseAction::ButtonDown, mouse_x, mouse_y, 2, 0});
                        }
                    }

                    if (event->type & GPM_UP)
                    {
                        if ((mouse_down & GPM_B_LEFT) && (event->buttons & GPM_B_LEFT))
                        {
                            xy_processed = true;
                            mouse_down &= ~GPM_B_LEFT;
                            sink->OnMouse({MouseAction::ButtonUp, mouse_x, mouse_y, 0, 0});
                        }
                        if ((mouse_down & GPM_B_MIDDLE) && (event->buttons & GPM_B_MIDDLE))
                        {
                            xy_processed = true;
                            mouse_down &= ~GPM_B_MIDDLE;
                            sink->OnMouse({MouseAction::ButtonUp, mouse_x, mouse_y, 1, 0});
                        }
                        if ((mouse_down & GPM_B_RIGHT) && (event->buttons & GPM_B_RIGHT))
                        {
                            xy_processed = true;
                            mouse_down &= ~GPM_B_RIGHT;
                            sink->OnMouse({MouseAction::ButtonUp, mouse_x, mouse_y, 2, 0});
                        }
                    }

                    if (!xy_processed && (event->type & (GPM_MOVE | GPM_DRAG)))
                        sink->OnMouse({MouseAction::Move, mouse_x, mouse_y, 0, 0});
                }

                if (mouse_write >= 32 * (int)sizeof(Gpm_Event))
                {
                    size_t tail = mouse_write - sizeof(Gpm_Event) * mouse_read;
                    if (tail)
                        memcpy(mouse_buf, mouse_buf + mouse_read, tail);
                    mouse_write = (int)tail;
                    mouse_read = 0;
                }
            }
#endif
        }
        else
        {
            pfds[0].fd = STDIN_FILENO;
            pfds[0].events = POLLIN;

            if (jsfd >= 0)
            {
                pfds[1].fd = jsfd;
                pfds[1].events = POLLIN | POLLHUP | POLLERR;
                poll(pfds, 2, 0);

                if (pfds[1].revents & (POLLHUP | POLLERR))
                {
                    GamePadUnmount();
                    close(jsfd);
                    jsfd = -1;
                }
                else if (pfds[1].revents & POLLIN)
                {
                    if (!read_js(jsfd))
                    {
                        GamePadUnmount();
                        close(jsfd);
                        jsfd = -1;
                    }
                }
            }
            else
            {
                poll(pfds, 1, 0);
            }
        }

        if (pfds[0].revents & POLLIN)
        {
            static TerminalInputParser parser;

            char fresh[256];
            int fresh_bytes = read(STDIN_FILENO, fresh, 256);
            if (fresh_bytes <= 0)
                continue;

            FILE* kl = fopen("keylog.txt", "a");
            for (int i = 0; i < fresh_bytes; i++)
                fprintf(kl, "0x%02X, ", fresh[i]);
            fprintf(kl, "\n");
            fclose(kl);

            if (parser.Feed(fresh, fresh_bytes, sink))
            {
                running = false;
                break;
            }
        }

        int nwh[2] = {0, 0};
        GetWH(nwh);
        if (nwh[0] != wh[0] || nwh[1] != wh[1])
        {
            sink->OnResize({nwh[0], nwh[1], 1, 1});
            wh[0] = nwh[0];
            wh[1] = nwh[1];
            buf = (AnsiCell*)realloc(buf, wh[0] * wh[1] * sizeof(AnsiCell));
        }

        hpi->Tick((uint64_t)dt);

        if (!perf_init)
        {
            perf_init = true;
            perf_enabled = getenv("ASCIICKER_PROFILE") != nullptr;
            if (perf_enabled)
            {
                const char* perf_path = getenv("ASCIICKER_PROFILE_LOG");
                if (perf_path && perf_path[0])
                    perf_out = fopen(perf_path, "a");
                if (!perf_out)
                    perf_out = stderr;
            }
        }

        if (wh[0] > 0 && wh[1] > 0)
        {
            if (perf_enabled)
            {
                uint64_t t0 = a3dGetTime();
                hpi->Render(stamp, buf, wh[0], wh[1]);
                uint64_t t1 = a3dGetTime();
                Print(buf, wh[0], wh[1], CP437_UTF8);
                uint64_t t2 = a3dGetTime();

                perf_render_sum += (t1 - t0);
                perf_print_sum += (t2 - t1);
                perf_frames++;
                if (perf_window_start == 0)
                    perf_window_start = t2;
                if (t2 - perf_window_start >= 1000000)
                {
                    double window_us = (double)(t2 - perf_window_start);
                    double avg_render_ms = (double)perf_render_sum / (double)perf_frames / 1000.0;
                    double avg_print_ms = (double)perf_print_sum / (double)perf_frames / 1000.0;
                    double fps = (double)perf_frames * 1000000.0 / window_us;
                    fprintf(perf_out, "[perf] render %.2fms print %.2fms fps %.1f (%dx%d)\n",
                        avg_render_ms, avg_print_ms, fps, wh[0], wh[1]);
                    fflush(perf_out);
                    perf_window_start = t2;
                    perf_render_sum = 0;
                    perf_print_sum = 0;
                    perf_frames = 0;
                }
            }
            else
            {
                hpi->Render(stamp, buf, wh[0], wh[1]);
                Print(buf, wh[0], wh[1], CP437_UTF8);
            }
        }

        frames++;
    }

exit:
    if (perf_out && perf_out != stderr)
    {
        fclose(perf_out);
        perf_out = nullptr;
    }

    if (jsfd >= 0)
    {
        GamePadUnmount();
        close(jsfd);
        jsfd = -1;
    }

#ifdef USE_GPM
    if (gpm >= 0)
        Gpm_Close();
#endif

    if (buf)
        free(buf);

    // Host-owned terminal resources are gone before we hand teardown back to the
    // game/runtime adapter, making this callsite the visible shutdown ordering owner.
    hpi->Shutdown();

    SetScreen(false);

    uint64_t end = a3dGetTime();
    printf("FPS: %f (%dx%d)\n", frames * 1000000.0 / (end - begin), wh[0], wh[1]);
#else
    (void)hpi;
    printf("Currently -term parameter is unsupported on Windows\n");
#endif
}
