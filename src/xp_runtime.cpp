#include <zlib.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <termios.h>
#include <unistd.h>
#include <vector>

namespace fs = std::filesystem;

// The historical runtime's engine/sprite.cpp owns this REXPaint contract:
// gzip payload, int32 version/layer count, then width/height and 10-byte cells
// in column-major order. This adapter retains only that read path.
constexpr std::size_t kMaxDecompressed = 500U * 1024U * 1024U;
constexpr std::uint32_t kMaxLayers = 64;
constexpr std::uint64_t kMaxCells = 10'000'000;

struct Cell {
    std::uint32_t glyph{};
    std::array<std::uint8_t, 3> foreground{};
    std::array<std::uint8_t, 3> background{};
};

struct Layer {
    std::uint32_t width{};
    std::uint32_t height{};
    std::vector<Cell> cells;

    const Cell& at(std::uint32_t x, std::uint32_t y) const {
        return cells.at(static_cast<std::size_t>(y) * width + x);
    }
};

struct XPFile {
    std::int32_t version{};
    std::vector<Layer> layers;
};

std::uint32_t read_u32(const std::vector<std::uint8_t>& bytes, std::size_t& offset) {
    if (offset + 4 > bytes.size()) throw std::runtime_error("truncated 32-bit field");
    const std::uint32_t value = static_cast<std::uint32_t>(bytes[offset]) |
        (static_cast<std::uint32_t>(bytes[offset + 1]) << 8U) |
        (static_cast<std::uint32_t>(bytes[offset + 2]) << 16U) |
        (static_cast<std::uint32_t>(bytes[offset + 3]) << 24U);
    offset += 4;
    return value;
}

std::vector<std::uint8_t> inflate_gzip(const fs::path& path) {
    gzFile handle = gzopen(path.c_str(), "rb");
    if (handle == nullptr) throw std::runtime_error("cannot open gzip XP: " + path.string());
    std::vector<std::uint8_t> bytes;
    std::array<std::uint8_t, 64 * 1024> buffer{};
    int count = 0;
    while ((count = gzread(handle, buffer.data(), static_cast<unsigned int>(buffer.size()))) > 0) {
        if (bytes.size() + static_cast<std::size_t>(count) > kMaxDecompressed) {
            gzclose(handle);
            throw std::runtime_error("XP exceeds decompressed size limit");
        }
        bytes.insert(bytes.end(), buffer.begin(), buffer.begin() + count);
    }
    int error_number = Z_OK;
    const char* raw_message = gzerror(handle, &error_number);
    const std::string message = raw_message ? raw_message : "unknown";
    const int close_status = gzclose(handle);
    if (count < 0 || (error_number != Z_OK && error_number != Z_STREAM_END) || close_status != Z_OK) {
        throw std::runtime_error("gzip read failed: " + message);
    }
    return bytes;
}

XPFile load_xp(const fs::path& path) {
    const auto bytes = inflate_gzip(path);
    std::size_t offset = 0;
    XPFile result;
    result.version = static_cast<std::int32_t>(read_u32(bytes, offset));
    const std::uint32_t layer_count = read_u32(bytes, offset);
    if (layer_count > kMaxLayers) throw std::runtime_error("XP layer limit exceeded");
    for (std::uint32_t layer_index = 0; layer_index < layer_count; ++layer_index) {
        Layer layer;
        layer.width = read_u32(bytes, offset);
        layer.height = read_u32(bytes, offset);
        const std::uint64_t cell_count = static_cast<std::uint64_t>(layer.width) * layer.height;
        if (layer.width == 0 || layer.height == 0 || cell_count > kMaxCells) {
            throw std::runtime_error("invalid XP layer dimensions");
        }
        layer.cells.resize(static_cast<std::size_t>(cell_count));
        for (std::uint32_t x = 0; x < layer.width; ++x) {
            for (std::uint32_t y = 0; y < layer.height; ++y) {
                Cell cell;
                cell.glyph = read_u32(bytes, offset);
                if (offset + 6 > bytes.size()) throw std::runtime_error("truncated XP cell");
                std::copy_n(bytes.begin() + static_cast<std::ptrdiff_t>(offset), 3, cell.foreground.begin());
                offset += 3;
                std::copy_n(bytes.begin() + static_cast<std::ptrdiff_t>(offset), 3, cell.background.begin());
                offset += 3;
                layer.cells[static_cast<std::size_t>(y) * layer.width + x] = cell;
            }
        }
        result.layers.push_back(std::move(layer));
    }
    if (offset != bytes.size()) throw std::runtime_error("XP contains trailing bytes");
    return result;
}

int digit(std::uint32_t glyph) {
    if (glyph >= '0' && glyph <= '9') return static_cast<int>(glyph - '0');
    if (glyph >= 'A' && glyph <= 'Z') return static_cast<int>(glyph - 'A' + 10);
    if (glyph >= 'a' && glyph <= 'z') return static_cast<int>(glyph - 'a' + 10);
    return -1;
}

struct Metadata {
    int angles{1};
    int projections{1};
    std::vector<int> animations{1};
    int frame_width{1};
    int frame_height{1};
};

Metadata metadata(const XPFile& xp) {
    if (xp.layers.empty()) throw std::runtime_error("XP has no layers");
    Metadata result;
    const Layer& header = xp.layers.front();
    const int raw_angles = digit(header.at(0, 0).glyph);
    if (raw_angles > 0) {
        result.angles = raw_angles;
        result.projections = 2;
    }
    result.animations.clear();
    for (std::uint32_t x = 1; x < header.width; ++x) {
        const int length = digit(header.at(x, 0).glyph);
        if (length <= 0) break;
        result.animations.push_back(length);
    }
    if (result.animations.empty()) result.animations.push_back(1);
    const Layer& visual = xp.layers.at(std::min<std::size_t>(2, xp.layers.size() - 1));
    int animation_sum = 0;
    for (const int length : result.animations) animation_sum += length;
    const int columns = result.projections * animation_sum;
    if (visual.width % columns != 0 || visual.height % result.angles != 0) {
        throw std::runtime_error("XP dimensions do not match animation metadata");
    }
    result.frame_width = static_cast<int>(visual.width) / columns;
    result.frame_height = static_cast<int>(visual.height) / result.angles;
    return result;
}

char visible_glyph(const Cell& cell) {
    if (cell.glyph >= 32 && cell.glyph <= 126) return static_cast<char>(cell.glyph);
    if (cell.background == std::array<std::uint8_t, 3>{255, 0, 255} &&
        (cell.glyph == 0 || cell.glyph == 32)) return ' ';
    return '#';
}

struct Selection { int layer{2}; int animation{1}; int frame{0}; int angle{0}; };

class TerminalMode {
public:
    explicit TerminalMode(int descriptor) : descriptor_(descriptor) {
        if (tcgetattr(descriptor_, &original_) != 0) {
            throw std::runtime_error("cannot read terminal mode");
        }
        termios interactive = original_;
        interactive.c_lflag &= static_cast<tcflag_t>(~(ICANON | ECHO | ISIG));
        if (tcsetattr(descriptor_, TCSANOW, &interactive) != 0) {
            throw std::runtime_error("cannot enable interactive terminal mode");
        }
        active_ = true;
    }

    TerminalMode(const TerminalMode&) = delete;
    TerminalMode& operator=(const TerminalMode&) = delete;

    ~TerminalMode() {
        if (active_) static_cast<void>(tcsetattr(descriptor_, TCSANOW, &original_));
    }

private:
    int descriptor_;
    termios original_{};
    bool active_{false};
};

std::string render(const XPFile& xp, const Metadata& meta, Selection& state, const fs::path& asset) {
    state.layer = (state.layer % static_cast<int>(xp.layers.size()) + static_cast<int>(xp.layers.size())) % static_cast<int>(xp.layers.size());
    state.animation = (state.animation % static_cast<int>(meta.animations.size()) + static_cast<int>(meta.animations.size())) % static_cast<int>(meta.animations.size());
    state.frame = (state.frame % meta.animations[state.animation] + meta.animations[state.animation]) % meta.animations[state.animation];
    state.angle = (state.angle % meta.angles + meta.angles) % meta.angles;
    const Layer& layer = xp.layers[state.layer];
    int animation_offset = 0;
    for (int i = 0; i < state.animation; ++i) animation_offset += meta.animations[i];
    const int x0 = (animation_offset + state.frame) * meta.frame_width;
    const int y0 = state.angle * meta.frame_height;
    std::string output = "HISTORICAL ASCIICKER XP RUNTIME (READ-ONLY)\n";
    output += "asset " + asset.filename().string() + "  version " + std::to_string(xp.version) +
        "  layers " + std::to_string(xp.layers.size()) + "\n";
    output += "layer " + std::to_string(state.layer + 1) + "/" + std::to_string(xp.layers.size()) +
        "  animation " + std::to_string(state.animation + 1) + "/" + std::to_string(meta.animations.size()) +
        "  frame " + std::to_string(state.frame + 1) + "/" + std::to_string(meta.animations[state.animation]) +
        "  angle " + std::to_string(state.angle + 1) + "/" + std::to_string(meta.angles) + "\n";
    output += "j/k layer  h/l angle  n/p frame  a animation  q quit\n\n";
    for (int y = 0; y < meta.frame_height; ++y) {
        for (int x = 0; x < meta.frame_width; ++x) {
            output.push_back(visible_glyph(layer.at(static_cast<std::uint32_t>(x0 + x), static_cast<std::uint32_t>(y0 + y))));
        }
        output.push_back('\n');
    }
    return output;
}

int verify_corpus(const fs::path& root) {
    const fs::path sprites = root / "data/normalized-xp/sprites";
    std::size_t files = 0;
    std::size_t layers = 0;
    for (const auto& entry : fs::directory_iterator(sprites)) {
        if (!entry.is_regular_file() || entry.path().extension() != ".xp") continue;
        const XPFile xp = load_xp(entry.path());
        static_cast<void>(metadata(xp));
        ++files;
        layers += xp.layers.size();
    }
    if (files != 115) throw std::runtime_error("expected exactly 115 XP files");
    std::cout << "verified 115 normalized XP files / " << layers << " raw layers\n";
    return 0;
}

int main(int argc, char** argv) {
    try {
        fs::path root;
        bool once = false;
        bool verify = false;
        for (int i = 1; i < argc; ++i) {
            const std::string argument = argv[i];
            if (argument == "--root" && i + 1 < argc) root = fs::path(argv[++i]);
            else if (argument == "--once") once = true;
            else if (argument == "--verify-corpus") verify = true;
            else throw std::runtime_error("unknown argument: " + argument);
        }
        if (root.empty()) throw std::runtime_error("--root is required");
        root = fs::canonical(root);
        if (verify) return verify_corpus(root);
        const fs::path asset = root / "data/normalized-xp/sprites/player-nude.xp";
        const XPFile xp = load_xp(asset);
        const Metadata meta = metadata(xp);
        Selection state;
        state.layer = std::min<int>(2, static_cast<int>(xp.layers.size()) - 1);
        if (once) {
            std::cout << render(xp, meta, state, asset);
            return 0;
        }
        if (!isatty(STDIN_FILENO) || !isatty(STDOUT_FILENO)) {
            throw std::runtime_error("interactive mode requires a terminal; use --once");
        }
        TerminalMode terminal(STDIN_FILENO);
        while (true) {
            std::cout << "\x1b[2J\x1b[H" << render(xp, meta, state, asset) << std::flush;
            char key = 0;
            if (read(STDIN_FILENO, &key, 1) != 1) break;
            if (key == 'q' || key == 3) break;
            if (key == 'j') ++state.layer;
            else if (key == 'k') --state.layer;
            else if (key == 'h') --state.angle;
            else if (key == 'l') ++state.angle;
            else if (key == 'n') ++state.frame;
            else if (key == 'p') --state.frame;
            else if (key == 'a') { ++state.animation; state.frame = 0; }
        }
        std::cout << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
}
