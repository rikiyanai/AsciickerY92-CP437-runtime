// =============================================================================
// platform/backend/terminal_input_parser.cpp — Terminal Input Byte Parser
// =============================================================================

#include "terminal_input_parser.h"
#include "platform/input_backend.h"

#include <cstdlib>
#include <cstring>

bool TerminalInputParser::Feed(const char* bytes, int len, InputSink* sink)
{
    if (len <= 0)
        return false;

    // Append fresh bytes to internal buffer.
    int copy = len;
    if (buf_len + copy > 256)
        copy = 256 - buf_len;
    memcpy(buf + buf_len, bytes, copy);
    buf_len += copy;

    int i = 0;
    while (i < buf_len)
    {
        int j = i;
        int type = 0;
        int mods = 0;

        // Lone ESC at end of buffer → emit ESCAPE and preserve for next read
        // (matches original host_terminal_ansi.cpp behaviour).
        if (buf[i] == 27 && i == buf_len - 1)
        {
            sink->OnKey({A3D_ESCAPE, 0, KeyAction::Press});
            break;
        }

        // Plain ASCII / control characters.
        if ((buf[i] >= ' ' && buf[i] <= 127) || buf[i] == 8 ||
            buf[i] == '\r' || buf[i] == '\n' || buf[i] == '\t')
        {
            if (buf[i] == ' ')
                sink->OnText({(uint32_t)' '});
            else if (buf[i] == '\t')
                sink->OnKey({A3D_TAB, 0, KeyAction::Press});
            else if (buf[i] == 127)
                sink->OnText({8});
            else
                sink->OnText({(uint32_t)buf[i]});
            i++;
            continue;
        }

        // ANSI F5-F8 with tilde: \x1b[1{5,7,8,9}~
        if (buf_len - i >= 5 && buf[i] == 0x1B && buf[i + 1] == 0x5B &&
            buf[i + 2] == 0x31 &&
            (buf[i + 3] == 0x35 || buf[i + 3] == 0x37 ||
             buf[i + 3] == 0x38 || buf[i + 3] == 0x39))
        {
            int a3d_mods = 0;
            int a3d_fkey = buf[i + 3] == 0x35 ? A3D_F5 : buf[i + 3] - 0x37 + A3D_F6;

            if (buf[i + 4] == 0x7E)
            {
                sink->OnKey({a3d_fkey, 0, KeyAction::Press});
                i += 5;
                continue;
            }

            if (buf_len - i >= 7 && buf[i + 4] == 0x3B && buf[i + 6] == 0x7E)
            {
                int seq_mods = buf[i + 5] - 0x31;
                if (seq_mods & 1) a3d_mods |= 0x1 << 8;
                if (seq_mods & 2) a3d_mods |= 0x2 << 8;
                if (seq_mods & 4) a3d_mods |= 0x4 << 8;
                sink->OnKey({a3d_fkey, a3d_mods, KeyAction::Press});
                i += 7;
                continue;
            }
        }

        // ANSI F5-F8 single char prefix: \x1b[<n>~
        if (buf_len - i >= 5 && buf[i] == 0x1B && buf[i + 1] == 0x5B &&
            buf[i + 2] == 0x31 && buf[i + 4] == 0x7E)
        {
            if (buf[i + 3] == 0x35) sink->OnKey({A3D_F5, 0, KeyAction::Press});
            if (buf[i + 3] == 0x37) sink->OnKey({A3D_F6, 0, KeyAction::Press});
            if (buf[i + 3] == 0x38) sink->OnKey({A3D_F7, 0, KeyAction::Press});
            if (buf[i + 3] == 0x39) sink->OnKey({A3D_F8, 0, KeyAction::Press});
            i += 5;
            continue;
        }

        // ANSI F1-F2 via O-sequence: \x1bOP, \x1bOQ
        if (buf_len - i >= 3 && buf[i] == 0x1B && buf[i + 1] == 'O')
        {
            if (buf[i + 2] == 'P') sink->OnKey({A3D_F1, 0, KeyAction::Press});
            if (buf[i + 2] == 'Q') sink->OnKey({A3D_F2, 0, KeyAction::Press});
            i += 3;
            continue;
        }

        // Tilde sequences: Delete, Insert, PageUp, PageDown.
        if (buf_len - i >= 4 && buf[i] == 0x1B && buf[i + 1] == '[')
        {
            if (buf[i + 2] == '3' && buf[i + 3] == '~') { sink->OnText({127}); i += 4; continue; }
            if (buf[i + 2] == '2' && buf[i + 3] == '~') { sink->OnKey({A3D_INSERT, 0, KeyAction::Press}); i += 4; continue; }
            if (buf[i + 2] == '5' && buf[i + 3] == '~') { sink->OnKey({A3D_PAGEUP, 0, KeyAction::Press}); i += 4; continue; }
            if (buf[i + 2] == '6' && buf[i + 3] == '~') { sink->OnKey({A3D_PAGEDOWN, 0, KeyAction::Press}); i += 4; continue; }
        }

        // Arrow keys and Home/End.
        if (buf_len - i >= 3 && buf[i] == 0x1B && buf[i + 1] == '[')
        {
            if (buf[i + 2] == 'D') { sink->OnKey({A3D_LEFT, 0, KeyAction::Down}); i += 3; continue; }
            if (buf[i + 2] == 'C') { sink->OnKey({A3D_RIGHT, 0, KeyAction::Down}); i += 3; continue; }
            if (buf[i + 2] == 'A') { sink->OnKey({A3D_UP, 0, KeyAction::Down}); i += 3; continue; }
            if (buf[i + 2] == 'B') { sink->OnKey({A3D_DOWN, 0, KeyAction::Down}); i += 3; continue; }
            if (buf[i + 2] == 'H') { sink->OnKey({A3D_HOME, 0, KeyAction::Press}); i += 3; continue; }
            if (buf[i + 2] == 'F') { sink->OnKey({A3D_END, 0, KeyAction::Press}); i += 3; continue; }
        }

        // Modified keys: \x1b[1;<mod><key>
        if (buf_len - i >= 4 && buf[i] == 0x1B && buf[i + 1] == '[' &&
            buf[i + 2] == '1' && buf[i + 3] == ';')
        {
            if (buf_len - i < 6)
                break;

            int seq_mods = buf[i + 4] - 0x31;
            int a3d_mods = 0;
            if (seq_mods & 1) a3d_mods |= 0x1 << 8;
            if (seq_mods & 2) a3d_mods |= 0x2 << 8;
            if (seq_mods &4 ) a3d_mods |= 0x4 << 8;

            switch (buf[i + 5])
            {
                case 'P': sink->OnKey({A3D_F1, a3d_mods, KeyAction::Press}); break;
                case 'Q': sink->OnKey({A3D_F2, a3d_mods, KeyAction::Press}); break;
                case '3': sink->OnKey({A3D_DELETE, a3d_mods, KeyAction::Press}); break;
                case '2': sink->OnKey({A3D_INSERT, a3d_mods, KeyAction::Press}); break;
                case '5': sink->OnKey({A3D_PAGEUP, a3d_mods, KeyAction::Press}); break;
                case '6': sink->OnKey({A3D_PAGEDOWN, a3d_mods, KeyAction::Press}); break;
                case 'A': sink->OnKey({A3D_UP, a3d_mods, KeyAction::Press}); break;
                case 'B': sink->OnKey({A3D_DOWN, a3d_mods, KeyAction::Press}); break;
                case 'C': sink->OnKey({A3D_RIGHT, a3d_mods, KeyAction::Press}); break;
                case 'D': sink->OnKey({A3D_LEFT, a3d_mods, KeyAction::Press}); break;
            }

            i += 6;
            continue;
        }

        // SGR mouse: \x1b[<...
        if (buf_len - i >= 3 && buf[i] == 0x1B && buf[i + 1] == '[' && buf[i + 2] == '<')
        {
            int k = i + 3;
            int val[3] = {0, 0, 0};
            int fields = 0;
            int offset = 0;
            while (buf_len - k > 0)
            {
                if (buf[k] < '0' || buf[k] > '9')
                {
                    int c = buf[k];
                    val[fields] = atoi(buf + k - offset);
                    fields++;
                    offset = 0;
                    k++;

                    if (c == ';' && fields < 3)
                        continue;

                    if ((c == 'm' || c == 'M') && fields == 3)
                    {
                        int but = val[0] & 0x3;
                        int mx = val[1] - 1;
                        int my = val[2] - 1;
                        if (val[0] >= 64)
                        {
                            if (but == 0) sink->OnMouse({MouseAction::Wheel, mx, my, 0, +1});
                            else if (but == 1) sink->OnMouse({MouseAction::Wheel, mx, my, 0, -1});
                        }
                        else if (val[0] >= 32)
                        {
                            sink->OnMouse({MouseAction::Move, mx, my, 0, 0});
                        }
                        else if (c == 'M')
                        {
                            if (but == 0) sink->OnMouse({MouseAction::ButtonDown, mx, my, 0, 0});
                            else if (but == 2) sink->OnMouse({MouseAction::ButtonDown, mx, my, 2, 0});
                            else sink->OnMouse({MouseAction::Move, mx, my, 0, 0});
                        }
                        else
                        {
                            if (but == 0) sink->OnMouse({MouseAction::ButtonUp, mx, my, 0, 0});
                            else if (but == 2) sink->OnMouse({MouseAction::ButtonUp, mx, my, 2, 0});
                            else sink->OnMouse({MouseAction::Move, mx, my, 0, 0});
                        }
                    }

                    i = k;
                    break;
                }

                offset++;
                k++;
            }

            if (buf_len - k <= 0)
                break; // incomplete mouse sequence, preserve for next read

            continue;
        }

        // Kitty keyboard protocol: \x1b_K[p/t/r]<mod><code><ESC>\ or \x1b_K[p/t/r]<mod>B<code><ESC>\
        if (buf_len - i >= 3 && buf[i] == 0x1B && buf[i + 1] == '_' && buf[i + 2] == 'K')
        {
            if (buf_len - i < 8)
                break;

            if (buf_len - i == 8 && buf[i + 6] != 0x1B)
                break;

            switch (buf[i + 3])
            {
                case 'p': type = +1; break;
                case 't': type = 0; break;
                case 'r': type = -1; break;
            }

            if (buf[i + 4] >= 'A' && buf[i + 4] <= 'P')
                mods = buf[i + 4] - 'A';

            int codelen = 0;
            if (buf[i + 6] == 0x1B && buf[i + 7] == '\\') codelen = 1;
            else if (buf_len - i >= 9 && buf[i + 7] == 0x1B && buf[i + 8] == '\\') codelen = 2;

            // Host quit: kitty alt+U
            if ((mods & 4) && buf[i + 5] == 'U' && codelen == 1)
            {
                buf_len = 0;
                return true;
            }

            int host_mods = 0;
            if (mods & 1) host_mods |= 0x1 << 8;
            if (mods & 2) host_mods |= 0x2 << 8;
            if (mods & 4) host_mods |= 0x4 << 8;

            if (codelen == 2 && buf[i + 5] == 'B')
            {
                if (type != 0)
                    sink->OnKey({128 + (int)(unsigned char)buf[i + 6], host_mods,
                                 type > 0 ? KeyAction::Down : KeyAction::Up,
                                 HostKeyDomain::TerminalByte});
                i += 9;
                continue;
            }

            if (codelen == 1)
            {
                if (type != 0)
                    sink->OnKey({(int)(unsigned char)buf[i + 5], host_mods,
                                 type > 0 ? KeyAction::Down : KeyAction::Up,
                                 HostKeyDomain::TerminalByte});
                i += 8;
                continue;
            }
        }

        // No sequence consumed — preserve remaining bytes and exit.
        if (j == i)
            break;
    }

    // Preserve unconsumed bytes (matching original host_terminal_ansi.cpp behaviour).
    if (i)
    {
        int remaining = buf_len - i;
        memmove(buf, buf + i, remaining);
        buf_len = remaining;
    }
    else if (buf_len > 0 && buf[0] == 27)
    {
        // Preserve ESC-starting partial sequences (arrow keys, kitty, etc.)
        // for the next read.  Fixes a bug in the original inline parser which
        // discarded partial sequences like \x1b[ when no byte was consumed.
    }
    else
    {
        buf_len = 0;
    }

    return false;
}

void TerminalInputParser::Reset()
{
    buf_len = 0;
}
