// FL-4062 / Q5.2: this file is a JSON-emit wrapper only.
//
// All enumeration logic and ID vocabulary live in server/actor_visual_reachability.h
// (the server-owned reachability authority). This file's job is to call
// EnumerateReachableKeys() and serialise each key to JSON in a shape that the
// scripts/dump_actor_visual_reachability.py wrapper consumes. It emits only the
// full CompiledActorVisualKey shape; legacy partial fields are intentionally
// absent so validators cannot match by selector-like subsets.

#include "actor_visual_reachability.h"

#include <stdint.h>
#include <stdio.h>

static void EmitJsonString(const char* value)
{
	putchar('"');
	if (value)
	{
		for (const char* p = value; *p; ++p)
		{
			if (*p == '"' || *p == '\\')
				putchar('\\');
			putchar(*p);
		}
	}
	putchar('"');
}

int main()
{
	std::vector<CanonicalActorVisualReachableKey> keys = EnumerateReachableKeys();

	printf("{\n");
	printf("  \"catalog_source\": \"server/actor_visual_catalog_source.h\",\n");
	printf("  \"errors\": [],\n");
	printf("  \"reachable_key_count\": %zu,\n", keys.size());
	printf("  \"reachable_keys\": [\n");
	for (size_t i = 0; i < keys.size(); i++)
	{
		const CanonicalActorVisualReachableKey& key = keys[i];
		printf("    {\n");
		printf("      \"key\": {\n");

		printf("        \"actor_style_id\": %u,\n", (unsigned)key.actor_style_id);
		printf("        \"chest_item_id\": %u,\n", (unsigned)key.chest_item_id);
		printf("        \"chest_style_id\": %u,\n", (unsigned)key.chest_style_id);
		printf("        \"future_slots\": [");
		for (size_t s = 0; s < key.future_slots.size(); s++)
		{
			if (s)
				printf(", ");
			printf("{\"item_id\": %u, \"slot_kind_id\": %u, \"visual_style_id\": %u}",
			       (unsigned)key.future_slots[s].item_id,
			       (unsigned)key.future_slots[s].slot_kind_id,
			       (unsigned)key.future_slots[s].visual_style_id);
		}
		printf("],\n");
		printf("        \"head_item_id\": %u,\n", (unsigned)key.head_item_id);
		printf("        \"head_style_id\": %u,\n", (unsigned)key.head_style_id);

		printf("        \"mount_id\": %u,\n", (unsigned)key.mount_id);
		printf("        \"presentation_kind_id\": %u,\n", (unsigned)key.presentation_kind_id);
		printf("        \"rig_id\": %u,\n", (unsigned)key.rig_id);
		printf("        \"shield_item_id\": %u,\n", (unsigned)key.shield_item_id);
		printf("        \"shield_style_id\": %u,\n", (unsigned)key.shield_style_id);
		printf("        \"skin_id\": %u,\n", (unsigned)key.skin_id);
		printf("        \"variation_id\": %u,\n", (unsigned)key.variation_id);
		printf("        \"weapon_item_id\": %u,\n", (unsigned)key.weapon_item_id);
		printf("        \"weapon_style_id\": %u\n", (unsigned)key.weapon_style_id);
		printf("      },\n");
		printf("      \"server_reason\": ");
		EmitJsonString("server C++ catalog plus server_tick presentation/combat ownership");
		printf("\n");
		printf("    }%s\n", (i + 1 < keys.size()) ? "," : "");
	}
	printf("  ],\n");
	printf("  \"schema_id\": \"asciicker.actor_visual_profiles.server_reachability.v1\",\n");
	printf("  \"server_markers_ok\": true,\n");
	printf("  \"source\": \"server/actor_visual_reachability_dump.cpp\"\n");
	printf("}\n");
	return 0;
}
