// STP1 transport validator. Portable code only; no LCD or board assumptions.
#pragma once
#include <cstddef>
#include <cstdint>
namespace storm {
struct Raster { std::uint16_t width, height, colors; std::size_t pixels; };
inline std::uint16_t le16(const std::uint8_t* p) { return p[0] | (std::uint16_t(p[1]) << 8); }
inline bool parse_raster(const std::uint8_t* p, std::size_t size, Raster& out) {
    if (!p || size < 12 || p[0]!='S' || p[1]!='T' || p[2]!='P' || p[3]!='1') return false;
    Raster candidate{le16(p+4), le16(p+6), le16(p+8), 0};
    if (!candidate.width || candidate.width>240 || !candidate.height || candidate.height>240 ||
        !candidate.colors || candidate.colors>256 || le16(p+10)) return false;
    candidate.pixels=12+std::size_t(candidate.colors)*4;
    if (size != candidate.pixels+std::size_t(candidate.width)*candidate.height) return false;
    for (std::size_t i=candidate.pixels; i<size; ++i) if (p[i]>=candidate.colors) return false;
    out=candidate; return true;
}
// Indexed row access uses no heap and does not imply a full RGB framebuffer.
inline const std::uint8_t* row(const std::uint8_t* p, const Raster& r, std::size_t y) {
    return y<r.height ? p+r.pixels+y*r.width : nullptr;
}
}
