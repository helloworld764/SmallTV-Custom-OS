#include "../firmware/protocol/raster.h"
#include <cassert>
#include <fstream>
#include <iterator>
#include <vector>
int main(int argc, char** argv) {
    assert(argc==2); std::ifstream file(argv[1],std::ios::binary);
    std::vector<std::uint8_t> raw((std::istreambuf_iterator<char>(file)),{});
    storm::Raster r{};
    assert(storm::parse_raster(raw.data(),raw.size(),r));
    assert(r.width==128 && r.height==128 && r.colors<=256);
    assert(storm::row(raw.data(),r,127) && !storm::row(raw.data(),r,128));
    assert(!storm::parse_raster(nullptr,0,r));
    assert(!storm::parse_raster(raw.data(),raw.size()-1,r));
    raw[0]='X';assert(!storm::parse_raster(raw.data(),raw.size(),r));
    raw[0]='S';raw[4]=255;raw[5]=255;assert(!storm::parse_raster(raw.data(),raw.size(),r));
}
