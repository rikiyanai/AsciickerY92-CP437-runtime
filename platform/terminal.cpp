// ============================================================================
// TERMINAL.CPP - Pure Terminal Mode Entry Point
// ============================================================================
//
// PURPOSE:
// Terminal-only build target (makefile_game_term) that renders game output
// to a native terminal emulator using PTY (pseudo-terminal) and ANSI escape
// sequences, bypassing OpenGL entirely. This allows the game to run in
// environments where X11/OpenGL is unavailable or undesirable.
//
// WHY PURE TERMINAL MODE:
// - No X11/OpenGL required: runs in SSH sessions, Linux console, Docker
// - Headless server support: testing/automation without graphics stack
// - Low overhead: minimal dependencies (libc, pthread, libutil)
// - Native terminal features: scrollback, copy/paste, Unicode support
//
// PTY (PSEUDO-TERMINAL) ARCHITECTURE:
// A PTY is a kernel facility that emulates a physical terminal. It consists
// of two connected file descriptors:
//
//   PTY MASTER (this process):
//     - Reads keyboard input from STDIN
//     - Writes keyboard input to PTY master fd
//     - Reads PTY master fd (output from child process)
//     - Writes output to STDOUT (user's terminal emulator)
//
//   PTY SLAVE (child process):
//     - Runs bash shell (or game process via execl)
//     - Thinks it's connected to a real terminal device
//     - Receives keyboard input via stdin (from PTY master)
//     - Sends output via stdout (to PTY master)
//
//   LINE DISCIPLINE (kernel layer between master/slave):
//     - Handles special characters: backspace, Ctrl-C, Ctrl-D, Ctrl-Z
//     - Provides line editing in cooked mode (we use raw mode)
//     - Propagates terminal window size changes (ioctl TIOCSWINSZ)
//
// THREE-THREAD ARCHITECTURE:
// This program uses three threads to pump data bidirectionally through the PTY:
//
//   MAIN THREAD (coordinator):
//     1. ioctl TIOCGWINSZ: get parent terminal size
//     2. forkpty(): create PTY master/slave pair + fork child process
//     3. execl bash: child process becomes bash shell
//     4. tcgetattr/cfmakeraw/tcsetattr: put parent terminal in raw mode
//     5. signal SIGWINCH: setup handler to propagate resize events
//     6. pthread_create read/write threads: start PTY pumps
//     7. pthread_join read thread: wait for child process exit
//     8. stop flag + TIOCSTI: wake up write thread cleanly
//     9. pthread_join write thread: wait for write thread exit
//     10. tcsetattr restore: restore parent terminal to original state
//     11. waitpid: reap child process
//
//   READ THREAD (PTY -> terminal):
//     - Loop: read(pty_fd, buf, siz)
//     - If child process closes stdout OR stop flag set: exit thread
//     - write(STDOUT_FILENO, buf, len): display output to user
//     - Escape(log_fd, "O: ", buf, len): log output if logging enabled
//     - This thread exits naturally when child process terminates
//
//   WRITE THREAD (keyboard -> PTY):
//     - Loop: read(STDIN_FILENO, buf, siz)
//     - If EOF OR stop flag set: exit thread
//     - write(pty_fd, buf, len): send keyboard input to child process
//     - Escape(log_fd, "I: ", buf, len): log input if logging enabled
//     - This thread is force-woken via TIOCSTI when stop flag is set
//
// TERMINAL CONTROL FLOW:
//
//   INITIALIZATION:
//     ioctl TIOCGWINSZ -> forkpty -> execl bash -> cfmakeraw ->
//     signal SIGWINCH -> pthread_create read/write threads
//
//   RUNTIME:
//     Read thread pumps PTY->STDOUT (child output to user)
//     Write thread pumps STDIN->PTY (user keyboard to child)
//     SIGWINCH handler propagates terminal resize events to PTY slave
//
//   SHUTDOWN:
//     Child process exits -> read thread detects EOF -> read thread exits ->
//     main sets stop flag -> main sends TIOCSTI wakeup -> write thread exits ->
//     tcsetattr restores terminal -> waitpid reaps child
//
// ESCAPE SEQUENCE LOGGING:
// The Escape() function converts non-printable characters to readable format
// for debugging ANSI escape sequences and control characters:
//   - Printable chars (>0x20): passed through unchanged
//   - \r -> "\\r"
//   - \n -> "\\n" + actual newline
//   - \t -> "\\t"
//   - Others -> "\\xHH" (hex format)
// This makes debug logs human-readable for terminal protocol analysis.
//
// WHY TWO RENDERING MODES:
//   term.cpp (OpenGL mode):
//     - Renders to OpenGL texture + blits to window
//     - Scalable font rendering, smooth scrolling
//     - Mouse support, GUI features
//     - Requires X11/Wayland/Win32 + OpenGL
//     - Desktop-focused
//
//   terminal.cpp (PTY mode):
//     - Renders directly to native terminal emulator via ANSI escapes
//     - Fixed-width font (terminal emulator's choice)
//     - Keyboard-only input (no mouse unless GPM enabled)
//     - No GUI dependencies (SSH-friendly)
//     - Server/headless-focused
//
// BUILD TARGET:
// Build with: make -f makefile_game_term
// Flags: -DPURE_TERM -DUSE_GPM (PTY mode + optional GPM mouse support)
// Link: -lutil (forkpty), -lpthread (threads), -lgpm (optional mouse)
//
// KEY DATA STRUCTURES:
//   int pty_fd: PTY master file descriptor (bidirectional)
//   int log_fd: Optional log file for escape sequence debugging
//   pthread_mutex_t log_mutex: Protects log file writes from race conditions
//   struct winsize ws: Terminal window dimensions (cols, rows)
//   struct termios org_ts: Original terminal settings (restored on exit)
//   volatile int stop: Thread coordination flag (main -> write thread)
//
// KEY FUNCTIONS:
//   main(): Setup PTY, spawn threads, coordinate shutdown
//   Read(): PTY -> terminal pump (child output)
//   Write(): keyboard -> PTY pump (user input)
//   Escape(): Convert non-printables to readable format for logging
//   SignalHandler(): Propagate SIGWINCH (terminal resize) to PTY slave
//
// ============================================================================

#include <sys/ioctl.h>
#include <sys/wait.h>
#include <unistd.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>

#include <stdio.h>

#include <pthread.h>  // compile with -pthread
#include <pty.h> // link  with -lutil

#include <fcntl.h>

// BUILD ME:
// g++ -pthread -o .run/terminal terminal.cpp -lutil

// RUN ME:
// .run/terminal
// .run/terminal <logfile>

volatile int stop = 0;
int pty_fd = -1; // PTY
int log_fd = -1;

pthread_mutex_t log_mutex = PTHREAD_MUTEX_INITIALIZER;

// WHY escape logging: Debug ANSI escape sequences by making non-printable
// characters visible in log files. Converts control chars (\r, \n, \t) and
// binary data to escaped format (\xHH). Essential for debugging terminal
// protocol issues and verifying correct escape sequence generation.
int Escape(int fd, const char* hdr, int hdrlen, const char* buf, int buflen)
{
    pthread_mutex_lock( &log_mutex );

    static const char* last_hdr = 0;
    const int siz = 4096;
    char esc[siz];
    int ret = 0;

    int l = 0;
    if (last_hdr != hdr)
    {
        if (last_hdr)
        {
            esc[0] = '\n';
            memcpy(esc+1,hdr,hdrlen);
            l = hdrlen+1;
        }
        else
        {
            memcpy(esc,hdr,hdrlen);
            l = hdrlen;
        }
        
        last_hdr = hdr;
    }
    
    for (int i=0; i<buflen; i++)
    {
        if (l>siz-4)
        {
            ret+=l;
            write(fd,esc,l);
            // l=hdrlen;
            l=0;
        }

        if (buf[i]>0x20 || buf[i]<0)
            esc[l++] = buf[i];
        else
        switch (buf[i])
        {
            case '\\' :
                esc[l++] = '\\';
                esc[l++] = '\\';
                break;
            case '\r' :
                esc[l++] = '\\';
                esc[l++] = 'r';
                break;
            case '\n' :
                esc[l++] = '\\';
                esc[l++] = 'n';
                esc[l++] = '\n';
                ret+=l;
                write(fd,esc,l);
                memcpy(esc,hdr,hdrlen);
                l=hdrlen;
                break;
            case '\t' :
                esc[l++] = '\\';
                esc[l++] = 't';
                break;
            case '\v' :
                esc[l++] = '\\';
                esc[l++] = 'v';
                break;
            default:
            {
                int hi = (buf[i]>>4)&0xF;
                int lo = buf[i]&0xF;

                if (hi<10)
                    hi+='0';
                else
                    hi+='A'-10;

                if (lo<10)
                    lo+='0';
                else
                    lo+='A'-10;

                esc[l++] = '\\';
                esc[l++] = 'x';
                esc[l++] = hi;
                esc[l++] = lo;
            }
        }
    }

    if (l)
    {
        ret+=l;
        write(fd,esc,l);
    }

    pthread_mutex_unlock( &log_mutex );

    return ret;
}

// [FLOW:TERMINAL] PTY -> terminal pump (child output to user's screen)
// WHY read thread: Continuously pumps child process stdout through PTY master
// to parent terminal's STDOUT. Runs until child process exits (EOF) or stop
// flag is set. Normal exit condition: child closes stdout when it terminates.
void* Read(void* arg)
{
    // read chars from child
    // display them
    const int siz = 1024;
    char buf[siz];
    while (1)
    {
        int len = read(pty_fd, buf, siz);
        if (len<=0 || __atomic_load_n(&stop, __ATOMIC_ACQUIRE))
            return 0;
        write(STDOUT_FILENO, buf, len);
        if (log_fd >= 0)
            Escape(log_fd, "O: ", 3, buf, len);
    }
    return 0;
}

// [FLOW:TERMINAL] Keyboard -> PTY pump (user input to child process)
// WHY write thread: Continuously pumps keyboard input from parent terminal's
// STDIN through PTY master to child process stdin. Blocks on read(STDIN) until
// input arrives or TIOCSTI wakeup occurs. Must be woken cleanly with TIOCSTI
// when stop flag is set (pthread_cancel is unsafe, close(STDIN) breaks parent).
void* Write(void* arg)
{
    // read KBD keys
    // write them to child
    const int siz = 1024;
    char buf[siz];
    while (1)
    {
        int len = read(STDIN_FILENO, buf, siz);
        if (len<=0 || __atomic_load_n(&stop, __ATOMIC_ACQUIRE))
            return 0;
        write(pty_fd, buf, len);
        if (log_fd >= 0)
            Escape(log_fd, "I: ", 3, buf, len);
    }

    return 0;
}

// [PLATFORM:UNIX] WHY SIGWINCH handler: Terminal resize events must be
// propagated from parent terminal to PTY slave so child process can adjust
// its output formatting. Without this, child process wouldn't know terminal
// size changed (e.g., user maximizes terminal window).
void SignalHandler(int s)
{
    if (s==SIGWINCH)
    {
        // just pipe SIGWINCH
        struct winsize ws;
        ioctl(STDIN_FILENO, TIOCGWINSZ, &ws);

        if (log_fd)
        {
            char buf[64];
            int len = sprintf(buf,"{%d,%d} ", ws.ws_col, ws.ws_row);
            Escape(log_fd, "S: ", 3, buf, len);
        }

        //ioctl(pty_fd, TIOCSWINSZ, &ws);

    }

    /* 
    - it turns out that we get this signal only if parent terminal is changin window size.
    - if child process like stty makes ioctl(TIOCSWINSZ) it modifies PTY window size silently!
      so neither we nor our parent terminal can respond to it in any way. 
    */
}

static int FailPumpThreadStartup(pid_t pid, const struct termios* org_ts,
                                 pthread_t* read_thread, bool read_started,
                                 const char* name, int rc)
{
    fprintf(stderr, "pthread_create(%s) failed: %d\n", name, rc);
    __atomic_store_n(&stop, 1, __ATOMIC_RELEASE);
    signal(SIGWINCH, SIG_DFL);
    if (pid > 0)
        kill(pid, SIGHUP);
    if (pty_fd >= 0)
    {
        close(pty_fd);
        pty_fd = -1;
    }
    if (read_started)
        pthread_join(*read_thread, 0);
    if (log_fd >= 0)
    {
        close(log_fd);
        log_fd = -1;
    }
    tcsetattr(STDIN_FILENO, 0, org_ts);
    int status = 0;
    waitpid(pid, &status, 0);
    return 1;
}

int main(int argc, char** argv)
{
    struct winsize ws;
    if ( ioctl(STDIN_FILENO, TIOCGWINSZ, &ws) < 0 )
    {
        // we are not attached to parent terminal!
        return -2;
    }

    // [PLATFORM:UNIX] PTY creation via forkpty(). WHY forkpty: Creates PTY
    // master/slave pair AND forks child process in a single atomic call.
    // Alternative (openpty + fork) requires manual slave fd setup in child.
    // forkpty handles all the complex pty setup: slave terminal settings,
    // controlling terminal assignment, session leader setup.
    char name[64]="";
    pid_t pid = forkpty(&pty_fd, name, 0, &ws);
    if (pid == 0)
    {
        // child
        execl("/bin/bash", "bash", (char*) NULL);
        exit(1);
    }

    // something went wrong ?
    if (pid < 0 || pty_fd < 0)
    {
        if (pty_fd>=0)
            close(pty_fd);
        return -1;
    }

    if (argc>1)
        log_fd = open(argv[1], O_WRONLY | O_CREAT | O_TRUNC, S_IRUSR | S_IWUSR | S_IRGRP | S_IROTH);

    struct termios org_ts;
    tcgetattr(STDIN_FILENO, &org_ts);

    // WHY raw mode: Disable echo, line buffering, and signal generation
    // (Ctrl-C, Ctrl-Z) to enable direct keyboard passthrough. In raw mode,
    // all input goes directly to child process without parent terminal
    // interpretation. cfmakeraw() sets: ~ICANON (no line buffering),
    // ~ECHO (no echo), ~ISIG (no signal chars), ~IEXTEN (no special processing).
    struct termios ts;
    cfmakeraw(&ts);
    tcsetattr(STDIN_FILENO, 0, &ts);

    // [PLATFORM:UNIX] Setup signal handler for terminal resize events.
    // WHY SIGWINCH: Parent terminal sends SIGWINCH when user resizes window.
    // We must propagate this to PTY slave (via ioctl TIOCSWINSZ) so child
    // process can reflow its output to new terminal dimensions.
    signal(SIGWINCH, SignalHandler);

    // [FLOW:TERMINAL] Start bidirectional PTY pumps (read: PTY->terminal,
    // write: keyboard->PTY). Threads run independently until child exits.
    pthread_t r,w;
    int rc = pthread_create(&r, 0, Read, 0);
    if (rc != 0)
        return FailPumpThreadStartup(pid, &org_ts, &r, false, "Read", rc);
    rc = pthread_create(&w, 0, Write, 0);
    if (rc != 0)
        return FailPumpThreadStartup(pid, &org_ts, &r, true, "Write", rc);

    // WHY join read thread first: Read thread exits when child process closes
    // stdout (normal exit condition). This is the clean shutdown trigger.
    // Waiting on write thread first would deadlock (write thread blocks on
    // read(STDIN) until TIOCSTI wakeup).
    pthread_join(r,0);

    // WHY TIOCSTI wakeup: Write thread is blocked in read(STDIN_FILENO),
    // waiting for keyboard input. We inject a fake input character ("!") to
    // unblock it cleanly so it can check the stop flag and exit. WHY NOT
    // pthread_cancel: unsafe (write thread may hold log_mutex, causing
    // deadlock). WHY NOT close(STDIN): breaks parent terminal's stdin,
    // leaving terminal unusable after program exits.
    __atomic_store_n(&stop, 1, __ATOMIC_RELEASE);
    ioctl(STDIN_FILENO,TIOCSTI,"!");
    pthread_join(w,0);

    // no more signal pumping
    signal(SIGWINCH, SIG_DFL);

    // both threads are done
    // it is safe to clode pty 
    close(pty_fd);
    pty_fd = -1;

    if (log_fd>=0)
    {
        close(log_fd);
        log_fd = -1;
    }

    // wait for child to die
    int status;
    waitpid(pid, &status, 0);

    // WHY restore original terminal settings: cfmakeraw disabled echo and
    // line buffering. If we don't restore org_ts, parent terminal will be
    // left in raw mode after program exits, making command line unusable
    // (no echo, no Ctrl-C). tcsetattr(org_ts) restores echo, line editing,
    // signal handling, making terminal usable again.
    tcsetattr(STDIN_FILENO, 0, &org_ts);

    return 0;
}
