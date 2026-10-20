// TCMScience WebAssembly port (no threads): vina.h reads boost::thread::hardware_concurrency() when cpu <= 0.
#ifndef TCMS_VINA_SHIM_BOOST_THREAD_THREAD_HPP
#define TCMS_VINA_SHIM_BOOST_THREAD_THREAD_HPP
#include <boost/thread/mutex.hpp>
namespace boost {
struct thread { static unsigned hardware_concurrency() { return 1; } };
}
#endif
