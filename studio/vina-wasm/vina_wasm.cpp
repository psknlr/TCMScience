// TCMScience: AutoDock Vina 1.2.7's own C++ API (src/lib/vina.h) for JavaScript. One call runs one ligand against one
// receptor in one of three modes, the calls the Python package exposes as Vina.score(), Vina.optimize() and
// Vina.dock() after set_receptor, set_ligand_from_string and compute_vina_maps — with cpu 1 (this build has no
// threads; see parallel.h) and the seed given (0, Vina's "pick a random seed", is refused: a run must be repeatable).
#include <emscripten/bind.h>
#include <emscripten/val.h>

#include <cstdio>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include "vina.h"
#include "file.h"

using emscripten::val;

namespace {

const char* RECEPTOR = "/tmp/receptor.pdbqt";
const char* POSE = "/tmp/pose.pdbqt";

val doubles(const std::vector<double>& v) {
	val a = val::array();
	for (double x : v) a.call<void>("push", x);
	return a;
}

double num(const val& p, const char* key) {
	val x = p[key];
	if (!x.isNumber()) throw std::invalid_argument(std::string(key) + " must be a number");
	return x.as<double>();
}

int whole(const val& p, const char* key) {
	double x = num(p, key);
	if (x != static_cast<double>(static_cast<int>(x))) throw std::invalid_argument(std::string(key) + " must be a whole number");
	return static_cast<int>(x);
}

std::string text(const std::string& path) {
	std::ifstream f(path);
	std::stringstream s;
	s << f.rdbuf();
	return s.str();
}

// mode "score" | "optimize" | "dock"; params {sf, seed, center: [x,y,z], size: [x,y,z], spacing,
// exhaustiveness, n_poses, min_rmsd, max_evals, energy_range, max_steps}
val run(const std::string& mode, const std::string& receptor, const std::string& ligand, const val& p) {
	val out = val::object();
	try {
		const std::string sf = p["sf"].as<std::string>();
		if (sf != "vina" && sf != "vinardo") throw std::invalid_argument("scoring function " + sf + ": this build takes vina or vinardo");
		const int seed = whole(p, "seed");
		if (seed == 0) throw std::invalid_argument("seed 0 asks Vina for a random seed; give one");
		const val c = p["center"];
		const val s = p["size"];
		{
			std::ofstream f(RECEPTOR);
			f << receptor;
		}
		Vina v(sf, 1, seed, 0);
		v.set_receptor(RECEPTOR);
		v.set_ligand_from_string(ligand);
		v.compute_vina_maps(c[0].as<double>(), c[1].as<double>(), c[2].as<double>(),
			s[0].as<double>(), s[1].as<double>(), s[2].as<double>(), num(p, "spacing"));
		if (mode == "score") {
			out.set("energies", doubles(v.score()));
		} else if (mode == "optimize") {
			out.set("energies", doubles(v.optimize(whole(p, "max_steps"))));
			v.write_pose(POSE);
			out.set("pose", text(POSE));
			std::remove(POSE);
		} else if (mode == "dock") {
			const int n = whole(p, "n_poses");
			const double range = num(p, "energy_range");
			v.global_search(whole(p, "exhaustiveness"), n, num(p, "min_rmsd"), whole(p, "max_evals"));
			out.set("poses", v.get_poses(n, range));
			val e = val::array();
			for (const auto& row : v.get_poses_energies(n, range)) e.call<void>("push", doubles(row));
			out.set("energies", e);
		} else {
			throw std::invalid_argument("mode " + mode + ": score, optimize or dock");
		}
		out.set("seed", v.seed());
		out.set("ok", true);
	} catch (const std::exception& e) {  // vina_runtime_error, pdbqt_parse_error and the standard library's
		out.set("ok", false);
		out.set("error", std::string(e.what()));
	} catch (const internal_error& e) {  // VINA_CHECK
		out.set("ok", false);
		out.set("error", std::string("Vina internal error at ") + e.file + ":" + std::to_string(e.line));
	} catch (const file_error& e) {
		out.set("ok", false);
		out.set("error", std::string("Vina could not ") + (e.in ? "read " : "write ") + e.name.string());
	} catch (...) {
		out.set("ok", false);
		out.set("error", "Vina threw an exception of an unknown type");
	}
	std::remove(RECEPTOR);
	return out;
}

}  // namespace

EMSCRIPTEN_BINDINGS(tcms_vina) {
	emscripten::function("run", &run);
}
