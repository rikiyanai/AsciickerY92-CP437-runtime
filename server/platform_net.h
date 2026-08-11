// platform_net.h — Cross-Platform Transport and Threading Primitives
//
// PURPOSE:
// Cross-platform socket transport (TCP, WebSocket, HTTP) and threading/sync
// primitives. Extracted from server/network.h.
//
// This header has NO dependency on protocol struct definitions.  For wire-
// format packet definitions, see protocol_common.h, protocol_snapshot.h, etc.
//
// SEE ALSO: server/network.h (includes this + protocol structs for compat)

#pragma once

#include <stdint.h>
#include <stddef.h>

#ifdef _WIN32

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <ws2def.h>
#include <ws2tcpip.h>
#define INVALID_TCP_SOCKET INVALID_SOCKET
typedef SOCKET TCP_SOCKET;

#else

#include <sys/types.h>
#include <sys/socket.h>
#include <unistd.h>
#include <netdb.h>
#include <pthread.h>
#define INVALID_TCP_SOCKET (-1)
typedef int TCP_SOCKET;

#endif

struct THREAD_HANDLE;
struct RWLOCK_HANDLE;
struct MUTEX_HANDLE;

// ── Socket API ──
int TCP_INIT();
int TCP_CLOSE(TCP_SOCKET s);
int TCP_CLEANUP();
int TCP_WRITE(TCP_SOCKET s, const uint8_t* buf, int size);
int TCP_READ(TCP_SOCKET s, uint8_t* buf, int size);
int HTTP_READ(TCP_SOCKET s, int(*cb)(const char* header, const char* value, void* param), void* param, char body_overread[2048]);

// WebSocket: type 0x1=text, 0x2=bin, 0x8=close, 0x9=ping, 0xA=pong
int WS_WRITE(TCP_SOCKET s, const uint8_t* buf, int size, int split, int type);
int WS_READ(TCP_SOCKET s, uint8_t* buf, int size, int* type);

static const int MAX_WS_FRAME_BYTES = 65536;

static inline uint16_t WS_READ_U16_BE(const uint8_t* src)
{
    return (uint16_t)(((uint16_t)src[0] << 8) | (uint16_t)src[1]);
}

static inline uint64_t WS_READ_U64_BE(const uint8_t* src)
{
    return ((uint64_t)src[0] << 56) | ((uint64_t)src[1] << 48) |
           ((uint64_t)src[2] << 40) | ((uint64_t)src[3] << 32) |
           ((uint64_t)src[4] << 24) | ((uint64_t)src[5] << 16) |
           ((uint64_t)src[6] << 8)  | (uint64_t)src[7];
}

static inline void WS_WRITE_U16_BE(uint8_t* dst, uint16_t value)
{
    dst[0] = (uint8_t)((value >> 8) & 0xFFu);
    dst[1] = (uint8_t)(value & 0xFFu);
}

static inline void WS_WRITE_U64_BE(uint8_t* dst, uint64_t value)
{
    dst[0] = (uint8_t)((value >> 56) & 0xFFu);
    dst[1] = (uint8_t)((value >> 48) & 0xFFu);
    dst[2] = (uint8_t)((value >> 40) & 0xFFu);
    dst[3] = (uint8_t)((value >> 32) & 0xFFu);
    dst[4] = (uint8_t)((value >> 24) & 0xFFu);
    dst[5] = (uint8_t)((value >> 16) & 0xFFu);
    dst[6] = (uint8_t)((value >> 8)  & 0xFFu);
    dst[7] = (uint8_t)(value & 0xFFu);
}

// ── Threading API ──
THREAD_HANDLE* THREAD_CREATE(void* (*entry)(void*), void* arg);
void* THREAD_JOIN(THREAD_HANDLE* thread);
bool THREAD_CREATE_DETACHED(void* (*entry)(void*), void* arg);
void THREAD_SLEEP(int ms);

// ── Synchronization API ──
MUTEX_HANDLE* MUTEX_CREATE();
void MUTEX_DELETE(MUTEX_HANDLE* mutex);
void MUTEX_LOCK(MUTEX_HANDLE* mutex);
void MUTEX_UNLOCK(MUTEX_HANDLE* mutex);

RWLOCK_HANDLE* RWLOCK_CREATE();
void RWLOCK_DELETE(RWLOCK_HANDLE* rwl);
void RWLOCK_READ_LOCK(RWLOCK_HANDLE* rwl);
void RWLOCK_READ_UNLOCK(RWLOCK_HANDLE* rwl);
void RWLOCK_WRITE_LOCK(RWLOCK_HANDLE* rwl);
void RWLOCK_WRITE_UNLOCK(RWLOCK_HANDLE* rwl);

// ── Atomic operations (lock-free) ──
unsigned int INTERLOCKED_DEC(volatile unsigned int* ptr);
unsigned int INTERLOCKED_INC(volatile unsigned int* ptr);
unsigned int INTERLOCKED_SUB(volatile unsigned int* ptr, unsigned int sub);
unsigned int INTERLOCKED_ADD(volatile unsigned int* ptr, unsigned int add);
