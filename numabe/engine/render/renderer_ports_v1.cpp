// engine/render/renderer_ports_v1.cpp
// Version anchor + ABI guards for the neutral product ports.
// No logic lives here; Classic-safe (compile-time only).

#include "renderer_ports_v1.h"

#include <stddef.h>

// Version anchor: exactly one TU names the v1 port layouts so they keep
// link-time identity. `version` stays the first field of every table.
static_assert(offsetof(struct RendererProductV1, version) == 0,
    "neutral ports: RendererProductV1.version must stay first");
static_assert(offsetof(struct PresentationBackendV1, version) == 0,
    "neutral ports: PresentationBackendV1.version must stay first");
static_assert(offsetof(struct InspectionPortV1, version) == 0,
    "neutral ports: InspectionPortV1.version must stay first");
static_assert(offsetof(struct ProductStartupV1, version) == 0,
    "neutral ports: ProductStartupV1.version must stay first");
static_assert(offsetof(struct AsciickerProductPortsV1, version) == 0,
    "neutral ports: AsciickerProductPortsV1.version must stay first");
static_assert(sizeof(struct RendererProductV1) > sizeof(uint32_t),
    "neutral ports: renderer table must carry slots beyond version");
static_assert(sizeof(uint32_t) == 4,
    "neutral ports: version field must be 32-bit");
static_assert(sizeof(uint64_t) == 8,
    "neutral ports: frame/epoch hook params must be 64-bit");
