#!/usr/bin/env python3
"""Remove static function definitions from server_tick.cpp.
Finds by text rfind (last occurrence) for definitions, find (first) for forward decls."""

PATH = "server/server_tick.cpp"

with open(PATH, "r") as f:
    text = f.read()

def remove_func(text, sig, extra_brace_lines=0):
    """Remove function body. Returns new text. Finds last occurrence of sig."""
    idx = text.rfind(sig)  # last occurrence = definition, not forward decl
    if idx < 0:
        print(f"  NOT FOUND: {sig[:60]}")
        return text
    start = text.rfind('\n', 0, idx) + 1
    
    # Find opening brace
    brace = text.find('{', idx)
    if brace < 0:
        print(f"  WARN: no brace found for: {sig[:60]}")
        return text
    
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == '{': depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                removed = text[start:end].split('\n')[0].strip()[:60]
                print(f"  Removed {removed}")
                comment = f"// REMOVED: {removed}\n"
                return text[:start] + comment + text[end:]
    return text

def remove_fwd_decl(text, sig):
    """Remove a forward declaration (single line ending with ;)."""
    idx = text.find(sig)
    if idx < 0:
        print(f"  NOT FOUND fwd decl: {sig[:60]}")
        return text
    start = text.rfind('\n', 0, idx) + 1
    end = text.find(';', idx) + 1
    if end < len(text) and text[end] == '\n':
        end += 1
    removed = text[start:end].split('\n')[0].strip()[:60]
    print(f"  Removed fwd decl: {removed}")
    comment = f"// REMOVED fwd decl: {removed}\n"
    return text[:start] + comment + text[end:]

# IO functions (at bottom of file)
# Multi-line signature: find by first line (ends with comma)
text = remove_func(text, "static bool IOFlushControlFrames(ServerState* state, int ci, ClientIO* cio,")
text = remove_func(text, "static STRUCT_RSP_LAG* IOLagEchoRspPayload(ClientIO::ControlFrame* frame)")
text = remove_func(text, "static bool IOQueueControlFrame(ClientIO* cio, const uint8_t* frame, int frame_len,")
text = remove_func(text, "static bool IODropOldestQueuedLagEcho(ClientIO* cio)")
text = remove_func(text, "static void IONoteControlDrop(ClientIO* cio, bool lag_echo, bool pong)")
text = remove_func(text, "static bool IOHasQueuedLagEcho(const ClientIO* cio)")

# Session
text = remove_func(text, "static bool SvrHasAnyAlivePlayer(const ServerState* state)")
text = remove_func(text, "static bool SvrHasAnyActiveSession(const ServerState* state)")

# Contract functions
text = remove_func(text, "static uint8_t SvrValidateJoinV2Claims(const ServerState* state,")
text = remove_func(text, "static const char* SvrAppearanceContractRejectReasonString(uint8_t reason_code)")
text = remove_func(text, "static uint16_t SvrAppearanceContractVersion(const ServerState* state)")
text = remove_fwd_decl(text, "static uint16_t SvrAppearanceContractVersion(const ServerState* state);")
text = remove_func(text, "bool SvrLoadStartupAppearanceContract(ServerState* state, char* error, size_t error_cap)")

# SvrAppearanceContractError
idx = text.rfind("fill-error-and-return")
if idx >= 0:
    start = text.rfind('\n', 0, idx) + 1
    brace = text.find('{', idx)
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == '{': depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                print(f"  Removed SvrAppearanceContractError (comment+func)")
                text = text[:start] + "// REMOVED: SvrAppearanceContractError\n" + text[end:]
                break

text = remove_func(text, "static bool SvrIsLowerHexHash64(const char* value)")

# Bundle lookup functions
text = remove_func(text, "static const SvrAppearanceBundleItemDef* SvrFindAppearanceItemById(")
text = remove_func(text, "static const SvrAppearanceBundleMountDef* SvrFindAppearanceMountById(")
text = remove_func(text, "static const SvrAppearanceBundleMountDef* SvrFindAppearanceMountBySlug(")
text = remove_func(text, "static const SvrAppearanceBundleItemDef* SvrFindAppearanceItemBySlug(")
text = remove_func(text, "static const SvrAppearanceBundleSeatDef* SvrFindAppearanceSeatByAlias(")
text = remove_func(text, "static const SvrAppearanceBundleProfileDef* SvrFindAppearanceProfileById(")
text = remove_func(text, "static bool SvrLoadAppearanceBundleCache(SvrAppearanceBundleCache* out_cache)")

# Small helpers
text = remove_func(text, "static void SvrClearAppearanceEntries(SvrAuthoritativeAppearanceState* appearance, bool bump_revision)")
text = remove_func(text, "static bool SvrRemoveAppearanceEntryBySlot(SvrAuthoritativeAppearanceState* appearance,")
text = remove_func(text, "static bool SvrUpsertAppearanceEntry(SvrAuthoritativeAppearanceState* appearance,")
text = remove_func(text, "static const SvrAppearanceLoadoutEntry* SvrFindEquippedAppearanceEntryForItem(")
text = remove_func(text, "static const SvrAppearanceLoadoutEntry* SvrFindAppearanceEntryByItemInstanceId(")
text = remove_func(text, "static int SvrFindAppearanceEntryIndexBySlot(const SvrAuthoritativeAppearanceState* appearance,")
text = remove_func(text, "static void SvrSetAppearanceIdentity(SvrAuthoritativeAppearanceState* appearance,")
text = remove_func(text, "static void SvrBumpAppearanceRevision(SvrAuthoritativeAppearanceState* appearance)")
text = remove_func(text, "static void SvrCopyAppearanceSubjectKey(char dst[32], const char* src)")
text = remove_func(text, "static uint8_t SvrPresentationVariantFromAppearanceVisualStyle(uint16_t visual_style_id)")
text = remove_func(text, "static uint16_t SvrAppearanceVisualStyleFromPresentationVariant(uint8_t variant)")

# Forward declarations
text = remove_fwd_decl(text, "static bool SvrLoadAppearanceBundleCache(SvrAppearanceBundleCache* out_cache);")
# Also remove the 'struct SvrAppearanceBundleCache;' forward decl
idx = text.find("struct SvrAppearanceBundleCache;")
if idx >= 0:
    start = text.rfind('\n', 0, idx) + 1
    end = idx + len("struct SvrAppearanceBundleCache;")
    if end < len(text) and text[end] == '\n': end += 1
    text = text[:start] + "// REMOVED: struct SvrAppearanceBundleCache (now in header)\n" + text[end:]
    print("  Removed: struct SvrAppearanceBundleCache;")

with open(PATH, "w") as f:
    f.write(text)
print("\nDone.")
