// ============================================================================
// Network System - Cross-Platform Networking and Threading Primitives
// ============================================================================
//
// PURPOSE:
// Provides cross-platform abstraction for sockets, threading, and synchronization.
// Abstracts Windows Winsock vs POSIX sockets, Windows CreateThread vs pthreads,
// Windows CRITICAL_SECTION vs pthread_mutex, and Windows SRWLOCK vs pthread_rwlock.
// Includes HTTP header parsing and WebSocket framing for multiplayer networking.
//
// WHY PLATFORM ABSTRACTION:
// Single codebase compiles for both Windows and POSIX platforms with #ifdef _WIN32 toggle.
// Game code uses unified types (TCP_SOCKET, THREAD_HANDLE, MUTEX_HANDLE, RWLOCK_HANDLE)
// without platform-specific conditionals scattered throughout the codebase.
//
// PLATFORM COMPARISON TABLE:
// +-------------------+---------------------------+---------------------------+
// | Feature           | Windows                   | POSIX                     |
// +-------------------+---------------------------+---------------------------+
// | Socket Type       | SOCKET (HANDLE)           | int (file descriptor)     |
// | Invalid Socket    | INVALID_SOCKET            | -1                        |
// | Init/Cleanup      | WSAStartup/WSACleanup     | No-op                     |
// | Close             | closesocket()             | close()                   |
// | Thread Create     | CreateThread()            | pthread_create()          |
// | Thread Join       | WaitForSingleObject()     | pthread_join()            |
// | Mutex             | CRITICAL_SECTION          | pthread_mutex_t           |
// | RW Lock           | SRWLOCK                   | pthread_rwlock_t          |
// | Interlocked       | InterlockedIncrement()    | __sync_add_and_fetch()    |
// +-------------------+---------------------------+---------------------------+
//
// THREADING MODEL:
// - THREAD_CREATE(): Spawn joinable thread
//   - Windows: CreateThread() + THREAD_HANDLE wrapper struct
//   - POSIX: pthread_create() with pthread_t in wrapper
//   - Returns THREAD_HANDLE* for cross-platform thread handle
//
// - THREAD_JOIN(): Wait for thread exit and retrieve return value
//   - Windows: WaitForSingleObject(INFINITE) + CloseHandle()
//   - POSIX: pthread_join() with void** return value
//   - Returns void* from thread entry function
//
// - THREAD_CREATE_DETACHED(): Fire-and-forget thread (no join)
//   - Windows: CreateThread() + immediate CloseHandle()
//   - POSIX: pthread_create() + pthread_detach()
//   - Returns bool (success/failure), caller cannot join
//
// - THREAD_SLEEP(): Cross-platform sleep
//   - Windows: Sleep(ms)
//   - POSIX: usleep(ms*1000)
//
// - WHY THREAD_HANDLE wrapper struct:
//   Windows CreateThread returns HANDLE directly, pthread_create fills out-param.
//   Wrapper struct provides common pointer type for both platforms, stores entry
//   function and arg for Windows trampoline, and encapsulates platform differences.
//
// SYNCHRONIZATION PRIMITIVES:
// - MUTEX: Exclusive lock (one thread at a time)
//   - Windows: CRITICAL_SECTION (InitializeCriticalSection/Enter/Leave/Delete)
//   - POSIX: pthread_mutex_t (pthread_mutex_init/lock/unlock/destroy)
//   - Use case: Protecting single shared resource (e.g., message queue)
//
// - RWLOCK: Read-write lock (multiple readers OR single writer)
//   - Windows: SRWLOCK (AcquireSRWLockShared/Exclusive, ReleaseSRWLock*)
//   - POSIX: pthread_rwlock_t (pthread_rwlock_rdlock/wrlock/unlock)
//   - Use case: World/terrain data (many reads, rare writes)
//
// - INTERLOCKED: Atomic operations (lock-free)
//   - Windows: InterlockedIncrement/Decrement/Add
//   - POSIX: __sync_fetch_and_add/sub (GCC built-ins)
//   - Use case: Reference counting, lockless counters
//
// SOCKET ABSTRACTION:
// - TCP_SOCKET: SOCKET (Windows) or int (POSIX)
// - INVALID_TCP_SOCKET: INVALID_SOCKET (Windows) or -1 (POSIX)
// - TCP_INIT(): WSAStartup() on Windows, no-op on POSIX
// - TCP_CLOSE(): closesocket() on Windows, close() on POSIX
// - TCP_CLEANUP(): WSACleanup() on Windows, no-op on POSIX
// - TCP_WRITE(): send() with retry loop until all bytes sent
// - TCP_READ(): recv() with retry loop until all bytes received
//
// WHY socket wrapper:
// Windows requires WSAStartup before socket calls, uses closesocket() not close().
// Abstraction hides platform differences, ensures proper initialization/cleanup.
//
// HTTP/WEBSOCKET HELPERS:
// - HTTP_READ(): Parse HTTP headers into callback, handles chunked encoding
//   - Reads headers line-by-line (CRLF-delimited)
//   - Invokes callback for each header:value pair
//   - Returns body_overread size (bytes read beyond headers)
//   - WHY callback-based: Avoids allocating large header dictionary
//   - WHY 2KB body_overread buffer: Captures partial body from header read
//
// - WS_WRITE(): WebSocket frame encoding (RFC 6455)
//   - Supports text/binary/close/ping/pong frame types
//   - Handles masking (required for client-to-server frames)
//   - Supports splitting large messages into multiple frames
//   - WHY masking: RFC 6455 mandates masking for client→server
//   - WHY split parameter: Prevents large frames causing latency spikes
//
// - WS_READ(): WebSocket frame decoding
//   - Unmasks frames (XOR with 4-byte mask key)
//   - Handles FIN bit for multi-frame messages
//   - Auto-responds to control frames (close/ping/pong)
//   - WHY unmasking: Server receives masked frames from clients
//   - WHY frame type dispatch: Control frames need immediate handling
//
// [FLOW:NETWORK] All multiplayer communication flows through TCP_*/WS_* functions
// [PLATFORM:WINDOWS] Windows-specific: Winsock, CreateThread, CRITICAL_SECTION, SRWLOCK
// [PLATFORM:POSIX] POSIX-specific: BSD sockets, pthreads, pthread_mutex, pthread_rwlock
// ============================================================================

#include <stdint.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "network.h"

#ifdef _WIN32

#pragma comment(lib,"Ws2_32.lib")

int TCP_INIT()
{
	WSADATA wsaData;
	return WSAStartup(MAKEWORD(2, 2), &wsaData);
}

int TCP_CLOSE(TCP_SOCKET s)
{
	return closesocket(s);
}

int TCP_CLEANUP()
{
	return WSACleanup();
}

struct THREAD_HANDLE
{
	HANDLE th;

	void* (*entry)(void*);
	void* arg;

	static DWORD WINAPI wrap(LPVOID p)
	{
		THREAD_HANDLE* t = (THREAD_HANDLE*)p;
		t->arg = t->entry(t->arg);
		return 0;
	}

	static DWORD WINAPI wrap_detached(LPVOID p)
	{
		THREAD_HANDLE* t = (THREAD_HANDLE*)p;
		void* arg = t->arg;
		void*(*entry)(void*) = t->entry;
		free(t);
		entry(arg);
		return 0;
	}
};

// WHY THREAD_HANDLE wrapper struct:
// Windows CreateThread expects DWORD WINAPI (*)(LPVOID) signature, but we want
// cross-platform void* (*)(void*) signature matching pthreads. The wrapper struct
// stores the portable entry function pointer and arg, then wrap() trampoline converts
// between calling conventions. This allows game code to use single thread signature.
//
// WHY wrap() trampoline:
// Invokes portable entry(arg), stores return value in t->arg for THREAD_JOIN retrieval,
// then returns 0 to satisfy Windows DWORD return requirement.
//
// WHY wrap_detached() frees wrapper:
// Detached threads have no join, so wrapper would leak. Copy entry/arg to stack,
// free wrapper immediately, then invoke entry. Caller never sees THREAD_HANDLE*.
THREAD_HANDLE* THREAD_CREATE(void* (*entry)(void*), void* arg)
{
	HANDLE th;
	DWORD id;

	THREAD_HANDLE* t = (THREAD_HANDLE*)malloc(sizeof(THREAD_HANDLE));
	t->arg = arg;
	t->entry = entry;
	th = CreateThread(0, 0, THREAD_HANDLE::wrap, t, 0, &id);
	if (!th)
	{
		free(t);
		return 0;
	}
	t->th = th;
	return t;
}

// WHY WaitForSingleObject vs pthread_join:
// Windows threads don't have a direct join primitive. WaitForSingleObject(INFINITE)
// blocks until thread exits, then CloseHandle() releases OS resources. Return value
// comes from t->arg (populated by wrap() trampoline). POSIX pthread_join() returns
// thread's return value via out-param, so we unify both paths through wrapper free.
void* THREAD_JOIN(THREAD_HANDLE* thread)
{
	WaitForSingleObject(thread->th, INFINITE);
	CloseHandle(thread->th);
	void* ret = thread->arg;
	free(thread);
	return ret;
}


bool THREAD_CREATE_DETACHED(void* (*entry)(void*), void* arg)
{
	DWORD id;
	THREAD_HANDLE* t = (THREAD_HANDLE*)malloc(sizeof(THREAD_HANDLE));
	t->arg = arg;
	t->entry = entry;
	HANDLE th = CreateThread(0, 0, THREAD_HANDLE::wrap_detached, t, 0, &id);
	if (!th)
	{
		free(t);
		return false;
	}
	CloseHandle(th);
	return true;
}

void THREAD_SLEEP(int ms)
{
	Sleep(ms);
}

struct RWLOCK_HANDLE
{
	SRWLOCK rw;
};

RWLOCK_HANDLE* RWLOCK_CREATE()
{
	RWLOCK_HANDLE* rwl = (RWLOCK_HANDLE*)malloc(sizeof(RWLOCK_HANDLE));
	InitializeSRWLock(&rwl->rw);
	return rwl;
}

void RWLOCK_DELETE(RWLOCK_HANDLE* rwl)
{
	free(rwl);
}

void RWLOCK_READ_LOCK(RWLOCK_HANDLE* rwl)
{
	AcquireSRWLockShared(&rwl->rw);
}

void RWLOCK_READ_UNLOCK(RWLOCK_HANDLE* rwl)
{
	ReleaseSRWLockShared(&rwl->rw);
}

void RWLOCK_WRITE_LOCK(RWLOCK_HANDLE* rwl)
{
	AcquireSRWLockExclusive(&rwl->rw);
}

void RWLOCK_WRITE_UNLOCK(RWLOCK_HANDLE* rwl)
{
	ReleaseSRWLockExclusive(&rwl->rw);
}

unsigned int INTERLOCKED_DEC(volatile unsigned int* ptr)
{
	return InterlockedDecrement(ptr);
}

unsigned int INTERLOCKED_INC(volatile unsigned int* ptr)
{
	return InterlockedIncrement(ptr);
}

unsigned int INTERLOCKED_SUB(volatile unsigned int* ptr, unsigned int sub)
{
	return (unsigned int)InterlockedAdd((volatile LONG*)ptr,-(LONG)sub);
}

unsigned int INTERLOCKED_ADD(volatile unsigned int* ptr, unsigned int add)
{
	return (unsigned int)InterlockedAdd((volatile LONG*)ptr, (LONG)add);
}


struct MUTEX_HANDLE
{
	CRITICAL_SECTION mu;
};

MUTEX_HANDLE* MUTEX_CREATE()
{
	MUTEX_HANDLE* m = (MUTEX_HANDLE*)malloc(sizeof(MUTEX_HANDLE));
	InitializeCriticalSection(&m->mu);
	return m;
}

void MUTEX_DELETE(MUTEX_HANDLE* mutex)
{
	DeleteCriticalSection(&mutex->mu);
	free(mutex);
}

void MUTEX_LOCK(MUTEX_HANDLE* mutex)
{
	EnterCriticalSection(&mutex->mu);
}

void MUTEX_UNLOCK(MUTEX_HANDLE* mutex)
{
	LeaveCriticalSection(&mutex->mu);
}

#else

typedef int TCP_SOCKET;
#define INVALID_TCP_SOCKET (-1)

int TCP_INIT()
{
	return 0;
}

int TCP_CLOSE(TCP_SOCKET s)
{
	return close(s);
}

int TCP_CLEANUP()
{
	return 0;
}

struct THREAD_HANDLE
{
	pthread_t th;
};

// WHY POSIX THREAD_HANDLE wrapper:
// POSIX pthread_t can be used directly with void*(*)(void*) signature, no trampoline
// needed. But we still wrap it in THREAD_HANDLE* for API consistency with Windows
// version. Allows game code to use THREAD_CREATE/JOIN uniformly without #ifdef.
THREAD_HANDLE* THREAD_CREATE(void* (*entry)(void*), void* arg)
{
	pthread_t th;
	int rc = pthread_create(&th, 0, entry, arg);
	if (rc != 0)
		return 0;

	THREAD_HANDLE* t = (THREAD_HANDLE*)malloc(sizeof(THREAD_HANDLE));
	t->th = th;
	return t;
}

// WHY pthread_join out-param vs Windows wrapper storage:
// POSIX pthread_join() writes thread return value to &ret out-param directly.
// Windows version stores return in wrapper->arg (via trampoline). Both paths
// free wrapper after join, return void* to caller. Symmetric cleanup.
void* THREAD_JOIN(THREAD_HANDLE* thread)
{
	void* ret = 0;
	pthread_join(thread->th, &ret);
	free(thread);
	return ret;
}

bool THREAD_CREATE_DETACHED(void* (*entry)(void*), void* arg)
{
	pthread_t th;
	int rc = pthread_create(&th, 0, entry, arg);
	if (rc != 0)
		return false;
	pthread_detach(th);
	return true;
}

void THREAD_SLEEP(int ms)
{
	usleep(ms*1000);
}

struct RWLOCK_HANDLE
{
	pthread_rwlock_t rw;
};

RWLOCK_HANDLE* RWLOCK_CREATE()
{
	RWLOCK_HANDLE* rwl = (RWLOCK_HANDLE*)malloc(sizeof(RWLOCK_HANDLE));
	pthread_rwlock_init(&rwl->rw, 0);
	return rwl;
}

void RWLOCK_DELETE(RWLOCK_HANDLE* rwl)
{
	pthread_rwlock_destroy(&rwl->rw);
	free(rwl);
}

void RWLOCK_READ_LOCK(RWLOCK_HANDLE* rwl)
{
	pthread_rwlock_rdlock(&rwl->rw);
}

void RWLOCK_READ_UNLOCK(RWLOCK_HANDLE* rwl)
{
	pthread_rwlock_unlock(&rwl->rw);
}

void RWLOCK_WRITE_LOCK(RWLOCK_HANDLE* rwl)
{
	pthread_rwlock_wrlock(&rwl->rw);
}

void RWLOCK_WRITE_UNLOCK(RWLOCK_HANDLE* rwl)
{
	pthread_rwlock_unlock(&rwl->rw);
}

struct MUTEX_HANDLE
{
	pthread_mutex_t mu;
};

MUTEX_HANDLE* MUTEX_CREATE()
{
	MUTEX_HANDLE* m = (MUTEX_HANDLE*)malloc(sizeof(MUTEX_HANDLE));
	pthread_mutex_init(&m->mu, 0);
	return m;
}

void MUTEX_DELETE(MUTEX_HANDLE* mutex)
{
	pthread_mutex_destroy(&mutex->mu);
	free(mutex);
}

void MUTEX_LOCK(MUTEX_HANDLE* mutex)
{
	pthread_mutex_lock(&mutex->mu);
}

void MUTEX_UNLOCK(MUTEX_HANDLE* mutex)
{
	pthread_mutex_unlock(&mutex->mu);
}

unsigned int INTERLOCKED_DEC(volatile unsigned int* ptr)
{
	return __sync_fetch_and_sub(ptr, 1) - 1;
}

unsigned int INTERLOCKED_INC(volatile unsigned int* ptr)
{
	return __sync_fetch_and_add(ptr, 1) + 1;
}

unsigned int INTERLOCKED_SUB(volatile unsigned int* ptr, unsigned int sub)
{
	return __sync_fetch_and_sub(ptr, sub) - sub;
}

unsigned int INTERLOCKED_ADD(volatile unsigned int* ptr, unsigned int add)
{
	return __sync_fetch_and_add(ptr, add) + add;
}

#endif

int TCP_WRITE(TCP_SOCKET s, const uint8_t* buf, int size)
{
	int l = size;
	while (l > 0)
	{
		int w = send(s, (const char*)buf, l, 0);
		if (w <= 0)
			return w;
		l -= w;
		buf += w;		
	}
	return size;
}

int TCP_READ(TCP_SOCKET s, uint8_t* buf, int size)
{
	int l = size;
	while (l > 0)
	{
		int r = recv(s, (char*)buf, l, 0);
		if (r <= 0)
			return r;
		l -= r;
		buf += r;
	}
	return size;
}

// WHY callback-based header parsing:
// HTTP headers can be large (cookies, long URLs), callback avoids allocating/copying
// entire header dictionary into memory. Caller processes each header:value pair
// immediately, can reject headers early by returning negative value.
//
// WHY chunked encoding handling:
// HTTP/1.1 allows Transfer-Encoding: chunked where body is split into size-prefixed
// chunks. Parser reads headers until \r\n\r\n (crlf_state==4), then returns bytes
// beyond header boundary as body_overread. Caller handles chunked decode if needed.
//
// WHY 2KB body_overread buffer:
// Socket recv() reads in 2KB chunks. Last recv() call during header parse likely
// reads partial body (first few bytes). body_overread captures this overshoot so
// caller doesn't lose data. Limits overshoot to 2KB (single recv buffer size).
//
// returns body_overread size (so 0 is very fine)
int HTTP_READ(TCP_SOCKET s, int(*cb)(const char* header, const char* value, void* param), void* param, char body_overread[2048])
{
	int crlf_state = 0;
	int header_len = -1;
	int value_len = 0;
	int col = 1; // currently header or value

	uint8_t buf[2048];

	int ret = 0;

	int header_size = 1024;
	int value_size = 1024;
	char* header = (char*)malloc(1024 + 1);
	char* value = (char*)malloc(1024 + 1);

	do
	{
		int r = recv(s, (char*)buf, 2048, 0);
		if (r <= 0)
		{
			free(header);
			free(value);
			if (body_overread)
				body_overread[0] = 0;
			return r;
		}

		for (int i = 0; i < r; i++)
		{
			switch (buf[i])
			{
			case 0x0D:
			{
				if (crlf_state == 0 || crlf_state == 2)
					crlf_state++;
				else
					crlf_state = 0;
				break;
			}

			case 0x0A:
			{
				if (crlf_state == 1 || crlf_state == 3)
				{
					if (col == 1)
					{
						value[value_len] = 0;
						if (header_len < 0)
							ret = cb(0, value, param);
						else
						{
							header[header_len] = 0;
							ret = cb(header, value, param);
						}

						if (ret < 0)
						{
							free(header);
							free(value);
							if (body_overread)
								body_overread[0] = 0;
							return ret;
						}
					}

					value_len = -1;
					header_len = 0;
					crlf_state++;
					col = 0;
				}
				else
					crlf_state = 0;
				break;
			}

			default:
			{
				crlf_state = 0;
				if (col == 0)
				{
					if (buf[i] == ':')
					{
						col = 1;
						value_len = -1;
					}
					else
					{
						if (header_len == header_size)
						{
							if (header_size >= 65536)
							{
								free(header);
								free(value);
								if (body_overread)
									body_overread[0] = 0;
								return -2;
							}
							header_size *= 2;
							header = (char*)realloc(header, header_size + 1);
						}
						header[header_len] = buf[i];
						header_len++;
					}
				}
				else
				{
					if (value_len == -1)
					{
						if (buf[i] != ' ')
						{
							free(header);
							free(value);
							if (body_overread)
								body_overread[0] = 0;
							return -2;
						}
						value_len++;
					}
					else
					{
						if (value_len == value_size)
						{
							if (value_size >= 65536)
							{
								free(header);
								free(value);
								if (body_overread)
									body_overread[0] = 0;
								return -2;
							}

							value_size *= 2;
							value = (char*)realloc(value, value_size + 1);
						}
						value[value_len] = buf[i];
						value_len++;
					}
				}
				break;
			}
			}

			if (crlf_state == 4)
			{
				i++;
				ret = r - i;
				if (body_overread)
					memcpy(body_overread, buf + i, ret);
				break;
			}
		}

	} while (crlf_state != 4);

	free(header);
	free(value);

	return ret;
}

// WHY masking required:
// RFC 6455 mandates client-to-server frames MUST be masked with random 4-byte key
// (security: prevents cache poisoning attacks). Server-to-client frames MUST NOT
// be masked. This implementation is for server (sends unmasked frames to clients).
//
// WHY split parameter for large messages:
// Large messages (megabytes) can be split into multiple frames to prevent latency
// spikes. If split=0 or split>=size, single frame. Otherwise, calculates equal-sized
// frames (last frame may be smaller). FIN bit set only on last frame.
//
// [FLOW:NETWORK]
// WebSocket Write (Frame Encoding)
// Wraps data in WebSocket frames (FIN, Opcode, Masking, Payload Length).
int WS_WRITE(TCP_SOCKET s, const uint8_t* buf, int size, int split, int type)
{
	if (size < 0)
		return size;
	if (size == 0)
	{
		uint8_t frame[2];
		frame[0] = (uint8_t)(0x80 | (type & 0x0F));
		frame[1] = 0x00;
		int w = TCP_WRITE(s, frame, (int)sizeof(frame));
		return w <= 0 ? w : 0;
	}

	if (split <= 0)
		split = size;

	// try making frames of equal size
	int frames = (size + split - 1) / split;
	int frame_size = (size + frames - 1) / frames;

	/*
		  0                   1                   2                   3
		  0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
		 +-+-+-+-+-------+-+-------------+-------------------------------+
		 |F|R|R|R| opcode|M| Payload len |    Extended payload length    |
		 |I|S|S|S|  (4)  |A|     (7)     |             (16/64)           |
		 |N|V|V|V|       |S|             |   (if payload len==126/127)   |
		 | |1|2|3|       |K|             |                               |
		 +-+-+-+-+-------+-+-------------+ - - - - - - - - - - - - - - - +
		 |     Extended payload length continued, if payload len == 127  |
		 + - - - - - - - - - - - - - - - +-------------------------------+
		 |                               |Masking-key, if MASK set to 1  |
		 +-------------------------------+-------------------------------+
		 | Masking-key (continued)       |          Payload Data         |
		 +-------------------------------- - - - - - - - - - - - - - - - +
		 :                     Payload Data continued ...                :
		 + - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - +
		 |                     Payload Data continued ...                |
		 +---------------------------------------------------------------+
	*/

	int offs = 0;

	int fmt = type;

	do
	{
		int len = 0;
		uint8_t frame[10];

		int payload = frame_size;

		if (offs + payload >= size)
		{
			payload = size - offs;
			frame[0] = 0x80/*FIN*/;
		}
		else
		{
			frame[0] = 0x00/*FIN*/;
		}

		if (offs == 0)
			frame[0] |= fmt/*BIN*/;

		if (payload < 126)
		{

			frame[1] = 0x00/*MSK*/ | payload;
			len = 2;
		}
		else
			if (payload < 65536)
			{
				frame[1] = 0x00/*MSK*/ | 126;
				WS_WRITE_U16_BE(frame + 2, (uint16_t)payload);
				len = 4;
			}
			else
			{
				frame[1] = 0x00/*MSK*/ | 127;
				WS_WRITE_U64_BE(frame + 2, (uint64_t)payload);
				len = 10;
			}

		// FL-2896: combine header+payload into a single TCP_WRITE so the
		// entire WS frame reaches the kernel in one send() call.  This
		// prevents interleaving when another thread writes to the same
		// socket (recv-thread pong vs main-thread Send).
		if (payload > 0 && len + payload <= 512)
		{
			uint8_t combined[512];
			memcpy(combined, frame, len);
			memcpy(combined + len, buf + offs, payload);
			int w = TCP_WRITE(s, combined, len + payload);
			if (w <= 0)
				return w;
			offs += payload;
		}
		else
		{
			int w = TCP_WRITE(s, frame, len);
			if (w <= 0)
				return w;

			if (payload)
			{
				int w = TCP_WRITE(s, buf + offs, payload);
				if (w <= 0)
					return w;
				offs += w;
			}
		}
	} while (offs < size);

	return size;
}

// WHY unmasking:
// Client-to-server frames are masked with 4-byte XOR key (RFC 6455 requirement).
// Server must unmask: payload[i] ^= mask[i & 3]. Masking prevents intermediary
// proxies from interpreting WebSocket data as HTTP (security/cache poisoning).
//
// WHY frame type dispatch:
// Control frames (0x8=close, 0x9=ping, 0xA=pong) require immediate handling.
// Close: auto-respond with close frame, return -1 to signal connection end.
// Ping: auto-respond with pong frame (echo payload), continue reading.
// Pong: ignore (response to our ping), continue reading.
// Data frames (0x0=continuation, 0x1=text, 0x2=binary) are returned to caller.
//
// [FLOW:NETWORK]
// WebSocket Read (Frame Decoding)
// Handles reading and unmasking WebSocket frames.
// Supports FIN bit, multi-frame messages, and Close/Ping/Pong control frames.
int WS_READ(TCP_SOCKET s, uint8_t* buf, int size, int* type)
{
	if (size < 0)
		return size;

	int len = 0, tot_data = 0;
	uint8_t frame[14];

	do
	{
		// read first 2 bytes
		{
			int r = TCP_READ(s, frame, 2);
			if (r <= 0)
				return r;
			len += r;
		}

		if (type)
			*type = frame[0] & 0xF;

		uint64_t payload = frame[1] & 0x7F;
		uint8_t* mask = 0;

		if (frame[1] & 0x80) // if mask bit is set
		{

			if (payload == 126)
			{
				// READ next 6 bytes -> first 2 replace payload len, other 4 is xor_mask
				int r = TCP_READ(s, frame + len, 6);
				if (r <= 0)
					return r;
				len += r;

				payload = WS_READ_U16_BE(frame + 2);
				mask = frame + 4;
			}
			else
				if (payload == 127)
				{
					// READ next 12 bytes -> first 8 replace payload len, other 4 is xor_mask
					int r = TCP_READ(s, frame + len, 12);
						if (r <= 0)
							return r;
						len += r;

						payload = WS_READ_U64_BE(frame + 2);

						mask = frame + 10;
					}
				else
				{
					// READ next 4 bytes of xor_mask
					int r = TCP_READ(s, frame + len, 4);
					if (r <= 0)
						return r;
					len += r;

					mask = frame + 2;
				}
		}
		else
		{
			if (payload == 126)
			{
				// READ next 2 bytes->replace payload len
				int r = TCP_READ(s, frame + len, 2);
				if (r <= 0)
					return r;
				len += r;

				payload = WS_READ_U16_BE(frame + 2);
			}
			else
				if (payload == 127)
				{
					// READ next 8 bytes->replace payload len
					int r = TCP_READ(s, frame + len, 8);
						if (r <= 0)
							return r;
						len += r;

						payload = WS_READ_U64_BE(frame + 2);
					}
		}

		// Reject oversized frames (close code 1009 — message too big) before
		// any buffer allocation.  MAX_WS_FRAME_BYTES is checked first so the
		// caller-buffer guard remains a secondary safety net.
		if (payload > (uint64_t)MAX_WS_FRAME_BYTES)
		{
			uint8_t close_frame[4] = { 0x03, 0xF9 }; // 1009 big-endian
			WS_WRITE(s, close_frame, 2, 0, 0x8);
			return -1;
		}
		if (payload > INT_MAX || payload > (uint64_t)size)
		{
			return -1;
		}

		switch (frame[0] & 0xF)
		{
			case 0x0:
			case 0x1:
			case 0x2:
				break;

			case 0x8: // close
			{
				WS_WRITE(s, 0, 0, 0, 0x8);
				return -1;
			}
			case 0x9: // ping
			{
				if (payload > 125)
					return -1; // RFC 6455: control frame payload max 125
				uint8_t ping[125];
				if (payload)
				{
					int r = TCP_READ(s, ping, (int)payload);
					if (r <= 0)
						return r;
					if (mask)
					{
						for (int i = 0; i < payload; i++)
							ping[i] ^= mask[i & 3];
					}
				}
				WS_WRITE(s, ping, (int)payload, 0, 0xA);
				continue;
			}
			case 0xA: // pong
			{
				if (payload > 125)
					return -1; // RFC 6455: control frame payload max 125
				uint8_t ping[125];
				if (payload)
				{
					int r = TCP_READ(s, ping, (int)payload);
					if (r <= 0)
						return r;
				}
				continue;
			}

			default:
				return -1;
		}		

		int r = TCP_READ(s, buf, (int)payload);
		if (r <= 0)
			return r;

		if (mask)
		{
			for (int i = 0; i < payload; i++)
				buf[i] ^= mask[i & 3];
		}

		buf += payload;
		size -= (int)payload;
		tot_data += (int)payload;

	} while (!(frame[0] & 0x80)); //(FIN bit is not set)

	return tot_data;
}
