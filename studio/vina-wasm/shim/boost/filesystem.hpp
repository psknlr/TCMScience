// TCMScience WebAssembly port: Boost.Filesystem (a compiled library) as the C++17 standard library's, for the paths
// and file streams Vina opens (in Emscripten's in-memory file system).
#ifndef TCMS_VINA_SHIM_BOOST_FILESYSTEM_HPP
#define TCMS_VINA_SHIM_BOOST_FILESYSTEM_HPP
#include <filesystem>
#include <fstream>
namespace boost { namespace filesystem {
using std::filesystem::path;
using std::filesystem::exists;
typedef std::ifstream ifstream;
typedef std::ofstream ofstream;
} }
#endif
