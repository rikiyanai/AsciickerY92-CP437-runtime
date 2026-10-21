// network_lag_telemetry.cpp — IO-thread lag echo measurement and control-frame queue
//
// Owns the IO-thread control frame queue (lag echo + WebSocket pong).
// Extracted from server/server_tick.cpp IO thread functions.
//
// These functions operate on ClientIO and are called exclusively from the IO
// thread (IOThreadEntry in server_tick.cpp). They are NOT thread-safe from
// the tick thread; access to lag echo counters uses relaxed atomics.

#include "connection/network_lag_telemetry.h"

#include <cerrno>
#include <string.h>
#include <stdio.h>
#include <time.h>

#include "server_state.h"
#include "protocol/protocol_lag_probe.h"
#include "platform/time_backend.h"

// =====================================================================
// Control frame queue — lag echo and pong
// =====================================================================

bool IOHasQueuedLagEcho(const ClientIO* cio)
{
    if (!cio)
        return false;
    for (uint32_t i = cio->control_read; i != cio->control_write; i = (i + 1) & SVR_IO_CONTROL_RING_MASK)
    {
        if (cio->control_ring[i & SVR_IO_CONTROL_RING_MASK].lag_echo)
            return true;
    }
    return false;
}

void IONoteControlDrop(ClientIO* cio, bool lag_echo, bool pong)
{
    if (!cio)
        return;
    if (lag_echo)
        cio->lag_echo_queue_drop_count++;
    if (pong)
        cio->control_pong_drop_count++;
    cio->control_queue_drop_count++;
}

bool IODropOldestQueuedLagEcho(ClientIO* cio)
{
    if (!cio)
        return false;
    for (uint32_t i = cio->control_read; i != cio->control_write; i = (i + 1) & SVR_IO_CONTROL_RING_MASK)
    {
        uint32_t idx = i & SVR_IO_CONTROL_RING_MASK;
        if (cio->control_ring[idx].lag_echo)
        {
            // Remove by sliding subsequent entries forward
            uint32_t slide = (cio->control_write - i - 1) & SVR_IO_CONTROL_RING_MASK;
            for (uint32_t j = 0; j < slide; j++)
            {
                uint32_t src = (i + 1 + j) & SVR_IO_CONTROL_RING_MASK;
                uint32_t dst = (i + j) & SVR_IO_CONTROL_RING_MASK;
                cio->control_ring[dst] = cio->control_ring[src];
            }
            cio->control_write = (cio->control_write - 1) & SVR_IO_CONTROL_RING_MASK;
            IONoteControlDrop(cio, true, false);
            return true;
        }
    }
    return false;
}

bool IOQueueControlFrame(ClientIO* cio,
                         const uint8_t* frame, int frame_len,
                         bool lag_echo)
{
    if (!cio || !frame || frame_len <= 0 || frame_len > SVR_IO_CONTROL_FRAME_SIZE)
    {
        IONoteControlDrop(cio, lag_echo, !lag_echo);
        return false;
    }

    uint32_t write = cio->control_write;
    uint32_t next = (write + 1) & SVR_IO_CONTROL_RING_MASK;

    if (next == cio->control_read)
    {
        // Ring full: try to drop oldest lag echo if this is a pong
        if (!lag_echo && IODropOldestQueuedLagEcho(cio))
            return IOQueueControlFrame(cio, frame, frame_len, lag_echo);
        IONoteControlDrop(cio, lag_echo, !lag_echo);
        return false;
    }

    memcpy(cio->control_ring[write].data, frame, (size_t)frame_len);
    cio->control_ring[write].len = frame_len;
    cio->control_ring[write].lag_echo = lag_echo;
    cio->control_write = next;

    // Track queue depth
    uint32_t depth = (cio->control_write - cio->control_read) & SVR_IO_CONTROL_RING_MASK;
    if (depth > cio->control_queue_max_depth)
        cio->control_queue_max_depth = depth;

    if (lag_echo)
    {
        cio->lag_echo_queue_drop_count = 0;
    }

    return true;
}

STRUCT_RSP_LAG* IOLagEchoRspPayload(ClientIO::ControlFrame* frame)
{
    if (!frame || !frame->lag_echo)
        return 0;
    // The STRUCT_RSP_LAG payload is at the end of the frame data
    const int payload_offset = frame->len - (int)sizeof(STRUCT_RSP_LAG);
    if (payload_offset < 0)
        return 0;
    return (STRUCT_RSP_LAG*)(frame->data + payload_offset);
}

static uint32_t IOLagU32Delta(uint32_t newer, uint32_t older)
{
    return (uint32_t)(newer - older);
}

static uint64_t IOLagRealtimeEpochUs()
{
    struct timespec ts;
    if (clock_gettime(CLOCK_REALTIME, &ts) != 0)
        return 0;
    return (uint64_t)ts.tv_sec * 1000000ull + (uint64_t)ts.tv_nsec / 1000ull;
}

bool IOFlushControlFrames(ServerState* state, int ci,
                          ClientIO* cio, int max_frames)
{
    if (!cio)
        return false;

    TCP_SOCKET sock = cio->socket;
    if (sock == INVALID_TCP_SOCKET)
        return true;

    if (cio->send_offset > 0 && cio->send_offset < cio->send_total)
    {
        if (IOHasQueuedLagEcho(cio))
        {
            const uint32_t remaining_bytes =
                (uint32_t)(cio->send_total - cio->send_offset);
            if (!cio->lag_echo_hol_blocked_active)
            {
                cio->lag_echo_hol_block_count++;
                cio->lag_echo_hol_blocked_active = true;
            }
            if (remaining_bytes > cio->lag_echo_hol_remaining_bytes_max)
                cio->lag_echo_hol_remaining_bytes_max = remaining_bytes;
        }
        return false;
    }
    cio->lag_echo_hol_blocked_active = false;

    int flushed = 0;
    while (cio->control_read != cio->control_write && flushed < max_frames)
    {
        uint32_t idx = cio->control_read & SVR_IO_CONTROL_RING_MASK;
        ClientIO::ControlFrame* frame = &cio->control_ring[idx];
        STRUCT_RSP_LAG* lag_payload = IOLagEchoRspPayload(frame);
        uint32_t flush_start_us32 = 0;

        int remaining = frame->len - cio->control_send_offset;
        if (remaining > 0)
        {
	            if (frame->lag_echo)
	            {
	                flush_start_us32 = (uint32_t)a3dGetTime();
	                const uint64_t flush_start_epoch_us = IOLagRealtimeEpochUs();
	                if (lag_payload && lag_payload->server_flush_start_us32 == 0)
	                    lag_payload->server_flush_start_us32 = flush_start_us32;
	                if (lag_payload && lag_payload->server_flush_start_epoch_us == 0)
	                    lag_payload->server_flush_start_epoch_us = flush_start_epoch_us;
	                cio->lag_echo_last_server_flush_start_us32 =
	                    lag_payload && lag_payload->server_flush_start_us32
	                        ? lag_payload->server_flush_start_us32
	                        : flush_start_us32;
	                cio->lag_echo_last_server_flush_start_epoch_us =
	                    lag_payload && lag_payload->server_flush_start_epoch_us
	                        ? lag_payload->server_flush_start_epoch_us
	                        : flush_start_epoch_us;
	                if (lag_payload && lag_payload->server_enqueue_us32 &&
	                    cio->lag_echo_last_server_flush_start_us32)
                {
                    cio->lag_echo_last_server_enqueue_to_flush_start_us =
                        IOLagU32Delta(cio->lag_echo_last_server_flush_start_us32,
                                      lag_payload->server_enqueue_us32);
                }
            }
            int sent = send(sock,
                           frame->data + cio->control_send_offset,
                           remaining,
#ifdef __linux__
                           MSG_NOSIGNAL
#else
                           0
#endif
                           );
            if (sent < 0)
            {
                if (state && ci >= 0 && ci < SVR_MAX_CLIENTS && frame->lag_echo)
                {
                    cio->lag_echo_send_errno_count++;
                    cio->lag_echo_last_errno = errno;
                }
                return false; // socket write stalled
            }
            cio->control_send_offset += sent;
            if (cio->control_send_offset < frame->len)
                return false; // partial write
        }

        // Frame fully sent
	        if (frame->lag_echo && state && ci >= 0 && ci < SVR_MAX_CLIENTS)
	        {
	            const uint32_t flush_finish_us32 = (uint32_t)a3dGetTime();
	            const uint64_t flush_finish_epoch_us = IOLagRealtimeEpochUs();
	            const uint32_t recorded_start_us32 =
	                cio->lag_echo_last_server_flush_start_us32 != 0
	                    ? cio->lag_echo_last_server_flush_start_us32
	                    : flush_start_us32;
            cio->lag_echo_send_success_count++;
	            cio->lag_echo_last_errno = 0;
	            cio->lag_echo_last_server_flush_finish_us32 = flush_finish_us32;
	            cio->lag_echo_last_server_flush_finish_epoch_us = flush_finish_epoch_us;
	            cio->lag_echo_last_server_flush_us =
                recorded_start_us32 != 0
                    ? IOLagU32Delta(flush_finish_us32, recorded_start_us32)
                    : 0u;
        }

        cio->control_send_offset = 0;
        cio->control_read = (cio->control_read + 1) & SVR_IO_CONTROL_RING_MASK;
        flushed++;
    }

    // Update diagnostics
    uint32_t depth = (cio->control_write - cio->control_read) & SVR_IO_CONTROL_RING_MASK;
    cio->control_queue_depth_last = depth;
    cio->control_send_offset_last = (uint32_t)cio->control_send_offset;

    return cio->control_read == cio->control_write;
}
