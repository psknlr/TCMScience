// TCMScience WebAssembly port (no threads): boost::mutex for Vina's parallel_progress, which only locks it around
// a counter. With one thread there is nothing to exclude.
#ifndef TCMS_VINA_SHIM_BOOST_THREAD_MUTEX_HPP
#define TCMS_VINA_SHIM_BOOST_THREAD_MUTEX_HPP
namespace boost {
struct mutex {
	struct scoped_lock { explicit scoped_lock(mutex&) {} };
	void lock() {}
	void unlock() {}
};
}
#endif
