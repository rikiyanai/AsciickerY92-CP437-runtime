#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "network_ingest.h"
#include "game.h"
#include "talkbox.h"
#include "game_utility.h"
#include "server/multiplayer_protocol.h"

bool ApplyChatPacket(Server* server, Game* game, const uint8_t* ptr, int size)
{
	(void)game; // reserved for future per-game chat routing
	if (size < 4)
		return true;
	STRUCT_BRC_TALK* talk = (STRUCT_BRC_TALK*)ptr;
	if ((int)(4 + talk->len) != size)
		return true;
	if (talk->id >= server->connection.max_clients)
		return true;
	Human* h = server->authority.others + talk->id;

	if (h->pos[2] > -100)
	{
		TalkBox* box = 0;
		if (h->talks == 3)
		{
			box = h->talk[0].box;
			h->talks--;
			for (int i = 0; i < h->talks; i++)
				h->talk[i] = h->talk[i + 1];
		}
		else
		{
			box = (TalkBox*)malloc(sizeof(TalkBox));
		}

		Human* h2 = server->authority.others + talk->id;
		ChatLog("%s : %.*s\n", h2->name, talk->len, talk->str);

		memset(box, 0, sizeof(TalkBox));
		memcpy(box->buf, talk->str, talk->len);
		box->buf[talk->len] = 0;
		box->len = talk->len;

		box->max_width = 33;
		box->max_height = 7;
		int s[2], p[2];
		box->Reflow(s, p);
		box->size[0] = s[0];
		box->size[1] = s[1];
		box->cursor_xy[0] = p[0];
		box->cursor_xy[1] = p[1];

		int idx = h->talks;
		h->talk[idx].box = box;
		h->talk[idx].pos[0] = h->pos[0];
		h->talk[idx].pos[1] = h->pos[1];
		h->talk[idx].pos[2] = h->pos[2];
		h->talk[idx].stamp = server->connection.stamp;
		h->talks++;
	}
	return true;
}
