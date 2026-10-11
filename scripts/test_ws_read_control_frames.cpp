// WS_READ must skip ping/pong control frames instead of returning 0.
// The server's keepalive pings the native client every 30 s; a 0 return is
// read as "connection closed" by the client receive thread.
// Build: g++ -std=c++17 -I. scripts/test_ws_read_control_frames.cpp server/network.cpp -lpthread
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
#include "server/platform_net.h"

static int fails = 0;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)

int main()
{
	int sv[2];
	if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv) != 0)
		return 2;
	// unmasked server->client frames: ping(empty), ping("hi"), binary "abc",
	// pong(empty), then a fragmented binary "de"+"f" with a ping in between.
	const uint8_t wire[] = {
		0x89, 0x00,
		0x89, 0x02, 'h', 'i',
		0x82, 0x03, 'a', 'b', 'c',
		0x8A, 0x00,
		0x02, 0x02, 'd', 'e',
		0x89, 0x00,
		0x80, 0x01, 'f',
	};
	CHECK(write(sv[1], wire, sizeof(wire)) == (ssize_t)sizeof(wire));

	uint8_t buf[64] = {};
	int type = -1;
	int r = WS_READ(sv[0], buf, sizeof(buf), &type);
	CHECK(r == 3);
	CHECK(memcmp(buf, "abc", 3) == 0);
	CHECK(type == 0x2);

	memset(buf, 0, sizeof(buf));
	r = WS_READ(sv[0], buf, sizeof(buf), 0);
	CHECK(r == 3);
	CHECK(memcmp(buf, "def", 3) == 0);

	// three pongs answered the three pings, in order, as unmasked FIN frames
	uint8_t pong[16] = {};
	ssize_t n = read(sv[1], pong, sizeof(pong));
	const uint8_t want[] = { 0x8A, 0x00, 0x8A, 0x02, 'h', 'i', 0x8A, 0x00 };
	CHECK(n == (ssize_t)sizeof(want));
	CHECK(memcmp(pong, want, sizeof(want)) == 0);

	close(sv[1]);
	r = WS_READ(sv[0], buf, sizeof(buf), 0);
	CHECK(r <= 0);
	close(sv[0]);
	printf(fails ? "test_ws_read_control_frames: FAILED\n" : "test_ws_read_control_frames: OK\n");
	return fails ? 1 : 0;
}
