"""Grandma/Dracula/Witch selectable skins (profiles 203-205, skins 104-106).

Covers the catalog/reachability/binding chain, exact asset bytes, the
server-owned skin request parser and cycle order (compiled from the real
server header), the JOIN_V2 wire layout shared with the web builder, and the
client/server wiring of the in-game cycle request.
"""

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SPRITES = REPO_ROOT / "assets" / "sprites"
PIPELINE_COMMIT = "fa40f3394497d2d445b75f946708d31f69b2fbc5"

SKIN_ASSETS = {
    "2026-10-08-grandma-body.xp": "4d7d910e784e4e74990050af4d4fd365013cec21fa540dfbdbaaa501214da180",
    "2026-10-08-grandma-plydie.xp": "37dde6a79feef7face4af96f8adc47d0b211ce3f26aa9413a1ea2c66b21c35dd",
    "2026-10-08-dracula-body.xp": "b3127c8dbc156a6acba65f9ecf82a2c323ff6e7ded76e51192fa35c1f9926205",
    "2026-10-08-dracula-plydie.xp": "5a60cf64d1a7a838efedbd5dfe30dd0da89954333f7ce09f427883422c6f3206",
    "2026-10-08-witch-body.xp": "7ea87d660a9748e51988cf626b2b23b1bb3aac1507a57e4f5a4589465fdb30eb",
    "2026-10-08-witch-plydie.xp": "84b45f1199374b29b65c7d2fdc2744e9b32c40b995a253961e916ee952b4fa65",
}
# name -> (profile id, skin id)
SKINS = {"grandma": (203, 104), "dracula": (204, 105), "witch": (205, 106)}
# presentation kind -> sheet part. 601 (ATTACK) reuses the body sheet because
# no attack sheet exists; see docs/FAILURE_LOG.md P0C-09.
KIND_SHEET = {600: "body", 601: "body", 602: "plydie"}
FIXED_PLAYER_POLICY = 1


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(rel: str) -> dict:
    return json.loads((REPO_ROOT / rel).read_text(encoding="utf-8"))


def _is_bare_key(key: dict) -> bool:
    slot_fields = (
        "head_item_id", "head_style_id", "chest_item_id", "chest_style_id",
        "weapon_item_id", "weapon_style_id", "shield_item_id", "shield_style_id",
        "mount_id", "rig_id", "variation_id", "actor_style_id",
    )
    return all(key[field] == 0 for field in slot_fields)


def test_spooky_skin_assets_are_exact() -> None:
    for name, digest in SKIN_ASSETS.items():
        assert _sha256(SPRITES / name) == digest, name


def test_catalog_declares_fixed_player_profiles() -> None:
    catalog = (REPO_ROOT / "server/actor_visual_catalog_source.h").read_text(encoding="utf-8")
    for name, (profile_id, skin_id) in SKINS.items():
        assert re.search(
            rf"APPEARANCE_CATALOG_{name.upper()}_PROFILE_ID = {profile_id};", catalog
        )
        assert re.search(
            rf"\{{APPEARANCE_CATALOG_{name.upper()}_PROFILE_ID, {skin_id}, \"{name}_player\", "
            r"nullptr, 0,\s*APPEARANCE_PROFILE_REACHABILITY_FIXED_PLAYER\}",
            catalog,
        ), name

    reach = _json("assets/actor_visual_profiles/current/server_reachable_keys.json")
    profiles = {profile["id"]: profile for profile in reach["catalog_profiles"]}
    assert set(profiles) == {200, 201, 202, 203, 204, 205}
    for name, (profile_id, skin_id) in SKINS.items():
        profile = profiles[profile_id]
        assert profile["skin_definition_id"] == skin_id
        assert profile["slug"] == f"{name}_player"
        assert profile["reachability_policy"] == FIXED_PLAYER_POLICY
        assert profile["starter_entries"] == []


def test_reachable_keys_are_exactly_three_bare_kinds_per_skin() -> None:
    reach = _json("assets/actor_visual_profiles/current/server_reachable_keys.json")
    assert reach["reachable_key_count"] == len(reach["reachable_keys"]) == 205
    for _name, (_profile_id, skin_id) in SKINS.items():
        keys = [row["key"] for row in reach["reachable_keys"] if row["key"]["skin_id"] == skin_id]
        assert sorted(key["presentation_kind_id"] for key in keys) == [600, 601, 602]
        assert all(_is_bare_key(key) and key["future_slots"] == [] for key in keys)


def test_custom_contract_and_bindings_bind_body_and_plydie_sheets() -> None:
    contract = _json("assets/actor_visual_profiles/source/custom_source_contract.json")
    assert contract["entry_count"] == len(contract["entries"]) == 8
    entries = {entry["source_xp"]: entry for entry in contract["entries"]}
    for name, digest in SKIN_ASSETS.items():
        entry = entries[f"assets/sprites/{name}"]
        assert entry["source_sha256"] == digest
        assert entry["source_commit"] == PIPELINE_COMMIT
        assert entry["source_kind"] == "VERIFIED_STATE_LAYER"
        assert entry["visual_layer_index"] == 2
        assert entry["render_operations"] == ["seed_l2_base_accumulator"]

    bindings = _json("assets/actor_visual_profiles/source/profile_bindings.json")
    reach = _json("assets/actor_visual_profiles/current/server_reachable_keys.json")
    assert bindings["row_count"] == len(bindings["rows"]) == 205
    assert bindings["server_reachability_hash"] == reach["server_reachability_hash"]
    for name, (_profile_id, skin_id) in SKINS.items():
        rows = {
            row["key"]["presentation_kind_id"]: row
            for row in bindings["rows"]
            if row["key"]["skin_id"] == skin_id
        }
        assert set(rows) == set(KIND_SHEET)
        for kind, part in KIND_SHEET.items():
            row = rows[kind]
            assert row["profile_id"] == f"{name}_p{kind}_m0"
            assert row["source_xp"] == f"assets/sprites/2026-10-08-{name}-{part}.xp"
            assert row["source_xp_id"] == f"2026-10-08-{name}-{part}"
            assert row["source_contract_kind"] == "custom"
            assert row["source_commit"] == PIPELINE_COMMIT
            assert row["composition_kind"] == "target_authored_standalone"
            assert _is_bare_key(row["key"])


def test_generated_table_and_provenance_contain_spooky_rows() -> None:
    header = (REPO_ROOT / "engine/actor_visual_profile_table.generated.h").read_text(
        encoding="utf-8"
    )
    provenance = _json("assets/actor_visual_profiles/current/actor_visual_profile_provenance.json")
    assert provenance["row_count"] == 205
    provenance_ids = {row["profile_id"] for row in provenance["rows"]}
    for name in SKINS:
        for kind in KIND_SHEET:
            assert f"{name}_p{kind}_m0" in provenance_ids
            assert f'"{name}_p{kind}_m0"' in header


_HARNESS = r"""
#include "appearance_skin_selection.h"
#include "protocol/protocol_join.h"
#include <stddef.h>
#include <stdio.h>
#include <string.h>

static_assert(offsetof(STRUCT_REQ_JOIN_V2, name) == 327, "name offset");
static_assert(offsetof(STRUCT_REQ_JOIN_V2, lut_hash) == 358, "lut offset");
static_assert(offsetof(STRUCT_REQ_JOIN_V2, page_atlas_chain_hash) == 423, "atlas offset");
static_assert(offsetof(STRUCT_REQ_JOIN_V2, requested_skin) == 488, "skin offset");
static_assert(sizeof(STRUCT_REQ_JOIN_V2) == 504, "join size");

static void Case(const char* label, const char* bytes, size_t len)
{
    char field[APPEARANCE_REQUESTED_SKIN_CAP];
    memset(field, 0, sizeof(field));
    memcpy(field, bytes, len < sizeof(field) ? len : sizeof(field));
    uint8_t outcome = 0xFF;
    int index = ResolveAppearanceSkinRequest(field, sizeof(field), &outcome);
    printf("resolve %s %u %s\n", label,
           (unsigned)kAppearanceSelectableSkins[index].appearance_profile_id,
           AppearanceSkinRequestOutcomeString(outcome));
}

int main()
{
    Case("empty", "", 0);
    Case("default", "default", 7);
    Case("wallace", "wallace", 7);
    Case("grandma", "grandma", 7);
    Case("DRACULA", "DRACULA", 7);
    Case("witch", "witch", 5);
    Case("zombie", "zombie", 6);
    Case("unterminated", "aaaaaaaaaaaaaaaa", 16);
    Case("grandma_trailing", "grandma\0junk", 12);
    Case("witchy", "witchy", 6);
    uint8_t outcome = 0xFF;
    int index = ResolveAppearanceSkinRequest(0, 0, &outcome);
    printf("resolve null %u %s\n",
           (unsigned)kAppearanceSelectableSkins[index].appearance_profile_id,
           AppearanceSkinRequestOutcomeString(outcome));
    const unsigned starts[] = {200, 201, 202, 203, 204, 205, 999};
    for (unsigned start : starts)
        printf("next %u %u\n", start,
               (unsigned)kAppearanceSelectableSkins[NextAppearanceSelectableSkinIndex((uint16_t)start)]
                   .appearance_profile_id);
    for (unsigned start : starts)
        printf("equip %u %d\n", start, AppearanceCatalogProfileAllowsEquipment((uint16_t)start) ? 1 : 0);
    printf("size cycle_struct %zu\n", sizeof(STRUCT_REQ_SKIN_CYCLE));
    return 0;
}
"""


@pytest.fixture(scope="module")
def harness_output(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("c++ compiler unavailable")
    tmp = tmp_path_factory.mktemp("skin-selection")
    source = tmp / "harness.cpp"
    source.write_text(_HARNESS, encoding="utf-8")
    exe = tmp / "harness"
    subprocess.run(
        [compiler, "-std=c++17", "-Wall", "-Werror", "-I", str(REPO_ROOT / "server"),
         str(source), "-o", str(exe)],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    )
    run = subprocess.run([str(exe)], check=True, capture_output=True, text=True)
    out: dict[str, str] = {}
    for line in run.stdout.splitlines():
        kind, label, rest = line.split(" ", 2)
        out[f"{kind} {label}"] = rest
    return out


def test_join_skin_request_parsing_and_invalid_fallback(harness_output: dict[str, str]) -> None:
    expected = {
        "empty": "201 none",
        "null": "201 none",
        "default": "201 default",
        "wallace": "201 matched",
        "grandma": "203 matched",
        "DRACULA": "204 matched",
        "witch": "205 matched",
        "grandma_trailing": "203 matched",
        "zombie": "201 invalid",
        "witchy": "201 invalid",
        "unterminated": "201 invalid",
    }
    for label, value in expected.items():
        assert harness_output[f"resolve {label}"] == value, label


def test_skin_cycle_order_and_equipment_guard(harness_output: dict[str, str]) -> None:
    assert harness_output["next 201"] == "203"
    assert harness_output["next 203"] == "204"
    assert harness_output["next 204"] == "205"
    assert harness_output["next 205"] == "201"
    # Non-selectable profiles (default full player, companion, unknown) cycle
    # back to the default skin instead of failing.
    assert harness_output["next 200"] == "201"
    assert harness_output["next 202"] == "201"
    assert harness_output["next 999"] == "201"
    assert harness_output["equip 200"] == "1"
    for profile_id in (201, 202, 203, 204, 205, 999):
        assert harness_output[f"equip {profile_id}"] == "0"
    assert harness_output["size cycle_struct"] == "2"


def test_join_v2_capacity_still_fits_web_outbound_budget() -> None:
    web_header = (REPO_ROOT / "web/web_network_client.h").read_text(encoding="utf-8")
    budget = int(re.search(r"WEB_OUTBOUND_MSG_MAX = (\d+);", web_header).group(1))
    assert 504 <= budget
    assert re.search(r"static_assert\(WEB_OUTBOUND_MSG_MAX >= sizeof\(STRUCT_REQ_JOIN_V2\)", web_header)
    state = (REPO_ROOT / "server/server_state.h").read_text(encoding="utf-8")
    assert re.search(r"sizeof\(STRUCT_REQ_JOIN_V2\) \+ 64u", state)
    # Stale 488-byte clients get a visible contract reject, not a silent drop.
    tick = (REPO_ROOT / "server/server_tick.cpp").read_text(encoding="utf-8")
    assert "copy == (int)offsetof(STRUCT_REQ_JOIN_V2, requested_skin)" in tick


def test_web_join_builder_writes_requested_skin() -> None:
    html = (REPO_ROOT / "web/game_web.html").read_text(encoding="utf-8")
    assert "var payload = new Uint8Array(504);" in html
    assert "writeAscii(488, 15, RequestedJoinSkinFromUrl(fl4131JoinParams));" in html
    start = html.index("function RequestedJoinSkinFromUrl(params)")
    end = html.index("// --- Multiplayer WebSocket state ---", start)
    function_source = html[start:end]
    node = shutil.which("node")
    if node is None:
        pytest.skip("node unavailable")
    script = function_source + r"""
const cases = ["", "?skin=grandma", "?skin=Dracula", "?skin=witch&name=x",
               "?skin=default", "?skin=%3Cscript%3E", "?skin=abcdefghijklmnopqrstuvwxyz"];
console.log(JSON.stringify(cases.map(c => RequestedJoinSkinFromUrl(new URLSearchParams(c)))));
"""
    run = subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(run.stdout) == [
        "", "grandma", "dracula", "witch", "default", "script", "abcdefghijklmno",
    ]


def test_native_client_forwards_skin_flag_and_env() -> None:
    app = (REPO_ROOT / "engine/game_app.cpp").read_text(encoding="utf-8")
    assert 'SetRequestedJoinSkin(getenv("ASCIICKER_SKIN"), "ASCIICKER_SKIN");' in app
    assert '(strcmp(argv[p], "-skin") == 0 || strcmp(argv[p], "--skin") == 0) && p + 1 < argc' in app
    assert "strncpy(out->requested_skin, g_requested_skin, sizeof(out->requested_skin) - 1);" in app
    # Env is read before the argv loop so the flag wins.
    assert app.index('getenv("ASCIICKER_SKIN")') < app.index('strcmp(argv[p], "--skin")')


def test_cycle_key_sends_server_validated_request() -> None:
    game_input = (REPO_ROOT / "engine/game_input.cpp").read_text(encoding="utf-8")
    block = game_input[game_input.index("if (key == 'k' || key == 'K')"):]
    block = block[: block.index("HandlePlaceHeightDebugKey")]
    assert "req_skin.token = 'S';" in block
    assert "server->Send((const uint8_t*)&req_skin, sizeof(req_skin))" in block

    tick = (REPO_ROOT / "server/server_tick.cpp").read_text(encoding="utf-8")
    assert "case 'S': SvrProcessSkinCycle(state, ci, pm->data, pm->size);" in tick
    handler = tick[tick.index("static void SvrProcessSkinCycle("):]
    handler = handler[: handler.index("\n}\n")]
    for needle in (
        "size != (int)sizeof(STRUCT_REQ_SKIN_CYCLE)",
        "ps->phase != CPHASE_ALIVE || ps->death_tick != 0",
        "SVR_SKIN_CYCLE_COOLDOWN_TICKS",
        "ps->appearance.mount_definition_id != 0 || ps->appearance.entry_count != 0",
        "NextAppearanceSelectableSkinIndex(prior_profile_id)",
        "SvrFindAppearanceProfileById(",
        "SvrApplyProfileToAppearance(&ps->appearance,",
        "SvrRefreshPlayerPresentationKind(state, ps);",
    ):
        assert needle in handler, needle


def test_join_uses_requested_skin_and_fixed_profiles_refuse_equipment() -> None:
    tick = (REPO_ROOT / "server/server_tick.cpp").read_text(encoding="utf-8")
    assert "SvrPrepareJoinName(state, ci, req->name, req->requested_skin)" in tick
    select = tick[tick.index("static bool SvrSelectJoinAppearanceProfile("):]
    select = select[: select.index("\n}\n")]
    assert "ResolveAppearanceSkinRequest(" in select
    assert "SvrCopyAppearanceSubjectKey(out_subject_key, selected_profile->slug);" in select
    assert "APPEARANCE_CATALOG_WALLACE_PROFILE_ID" not in select  # default lives in the table
    # Both player equip owners consult the fixed-profile guard.
    for owner in ("static bool SvrTryMutateAppearanceEquipState(", "static bool SvrPickupEquippableItem("):
        body = tick[tick.index(owner):]
        body = body[: body.index("SvrResolveAppearanceEntryForItemState")]
        assert "SvrPlayerAppearanceAllowsEquipment(" in body, owner


def test_init_game_keeps_server_appearance_received_in_main_menu() -> None:
    # The server sends the local appearance_v2 once, right after JOIN accept,
    # while the native client still sits in the main menu. InitGame (NEW GAME)
    # used to zero LocalPlayerState and drop it, so the local player fell back
    # to the bundle default skin 101 whatever the server selected.
    game = (REPO_ROOT / "engine/game.cpp").read_text(encoding="utf-8")
    body = game[game.index("void InitGame(Game* g,"):]
    body = body[: body.index("\n}\n")]
    keep = body.index("const AppearanceStateV2 server_appearance_v2 = g->player.appearance_v2;")
    wipe = body.index("memset(&g->player,0,sizeof(LocalPlayerState));")
    restore = body.index("if (server && server_appearance_v2.valid)\n\t\tg->player.appearance_v2 = server_appearance_v2;")
    ensure = body.index("EnsureLocalPlayerAppearance(g->player);")
    assert keep < wipe < restore < ensure
    dispatch = (REPO_ROOT / "engine/network_ingest_dispatch.cpp").read_text(encoding="utf-8")
    assert "case 'a': // server-owned V2 appearance packets are baseline-like" in dispatch
