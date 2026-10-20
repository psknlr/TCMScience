/*
   Copyright (c) 2006-2010, The Scripps Research Institute
   Licensed under the Apache License, Version 2.0 (the "License"); see LICENSE.

   TCMScience WebAssembly port: this file replaces AutoDock Vina 1.2.7's src/lib/parallel.h in a build without
   threads. Vina's parallel_for starts its worker threads in its constructor and runs each index i in [0, size) once,
   on some thread; parallel_mc gives every task its own model, output container and generator (seeded in task order
   before the run) and merges the outputs in task order afterwards, so the result does not depend on which thread ran
   which index or when. Here every index runs on the calling thread, in index order. Nothing else differs.
*/

#ifndef VINA_PARALLEL_H
#define VINA_PARALLEL_H

#include <vector>

#include "common.h"

template<typename F, bool Sync = false>
struct parallel_for {
	parallel_for(const F* f, sz num_threads) : m_f(f) { (void)num_threads; }
	void run(sz size) {
		for(sz i = 0; i < size; ++i)
			(*m_f)(i);
	}
private:
	const F* m_f; // does not keep a local copy!
};

template<typename F, typename Container, typename Input, bool Sync = false>
struct parallel_iter {
	parallel_iter(const F* f, sz num_threads) : a(f), pf(&a, num_threads) {}
	void run(Container& v) {
		a.v = &v;
		pf.run(v.size());
	}
private:
	struct aux {
		const F* f;
		Container* v;
		aux(const F* f) : f(f), v(NULL) {}
		void operator()(sz i) const {
			VINA_CHECK(v);
			(*f)((*v)[i]);
		}
	};
	aux a;
	parallel_for<aux, Sync> pf;
};

#endif
