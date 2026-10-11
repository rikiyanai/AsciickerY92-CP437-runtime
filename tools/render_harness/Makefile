# Offscreen render harness — real engine renderer, no game_app / V8 / server.
#
# Build from the repo root:
#   make render-harness                 (top-level wrapper)
#   make -f tools/render_harness/Makefile -j
#
# Output: .run/render_harness

BIN := .run/render_harness
OBJDIR := .o_render_harness
DEPDIR := .d_render_harness

# Engine code linked as-is (same files makefile_game_term compiles).
ENGINE_SRCS := \
	engine/render/render_core.cpp \
	engine/render/render_scene.cpp \
	engine/render/render_projection.cpp \
	engine/render/render_stage_shadow.cpp \
	engine/render/render_resolve.cpp \
	engine/render/render_sprite_blit.cpp \
	engine/render/render_world_pass.cpp \
	engine/render/render_hud_overlay.cpp \
	engine/render/render_observation_builder.cpp \
	engine/render/render_debug_observation.cpp \
	engine/render_glyph_sidecar_noop.cpp \
	engine/sprite.cpp \
	engine/glyph_sidecar.cpp \
	engine/glyph_compositor.cpp \
	engine/glyph_coverage_lookup.cpp \
	engine/glyph_manifest.cpp \
	engine/glyph_plane.cpp \
	engine/material_glyph_plane.cpp \
	engine/terrain.cpp \
	engine/world_core.cpp \
	engine/world_instance.cpp \
	engine/world_query.cpp \
	engine/world_serialization_a3d.cpp \
	engine/world_mesh.cpp \
	engine/world_tick.cpp \
	engine/world_minimap_markers.cpp \
	engine/world_editor_ops.cpp \
	engine/a3d_load_context.cpp \
	engine/rgba8.cpp \
	engine/upng.c \
	engine/tinfl.c \
	engine/sha1.c \
	engine/third_party/cjson/cJSON.c

HARNESS_SRCS := \
	tools/render_harness/harness_core.cpp \
	tools/render_harness/harness_stubs.cpp \
	tools/render_harness/main.cpp \
	$(wildcard tools/render_harness/scene_*.cpp)

SRCS := $(ENGINE_SRCS) $(HARNESS_SRCS)
OBJS := $(patsubst %,$(OBJDIR)/%.o,$(basename $(SRCS)))
DEPS := $(patsubst %,$(DEPDIR)/%.d,$(basename $(SRCS)))

CC := gcc
CXX := g++
# Same defines as makefile_game_term (GAME + PURE_TERM); no V8 include path.
CPPFLAGS := -pthread -DGAME -DPURE_TERM -O2 -I . -I engine -I engine/render -I editor -I platform -I server -I tools/render_harness
CFLAGS :=
CXXFLAGS := -std=c++17
LDFLAGS := -pthread
LDLIBS := -lm
DEPFLAGS = -MT $@ -MD -MP -MF $(DEPDIR)/$*.d

.PHONY: all clean
all: $(BIN)

$(BIN): $(OBJS)
	@mkdir -p $(dir $@)
	@echo Linking: $@
	@$(CXX) $(LDFLAGS) -o $@ $^ $(LDLIBS)

$(OBJDIR)/%.o: %.c
	@mkdir -p $(dir $@) $(dir $(DEPDIR)/$*.d)
	@echo Compiling $<
	@$(CC) $(DEPFLAGS) $(CFLAGS) $(CPPFLAGS) -c -o $@ $<

$(OBJDIR)/%.o: %.cpp
	@mkdir -p $(dir $@) $(dir $(DEPDIR)/$*.d)
	@echo Compiling $<
	@$(CXX) $(DEPFLAGS) $(CXXFLAGS) $(CPPFLAGS) -c -o $@ $<

clean:
	@$(RM) -r $(OBJDIR) $(DEPDIR) $(BIN)

-include $(DEPS)
