"""Networks and sets: the descriptive topology of an interaction network, and the overlaps of
named sets with an optional chance test.

Both are pure Python and deterministic (ties are broken by name), so the browser runtime and
the native one give the same answer. Neither claims significance for a network measure: a
node of high degree in an interaction database is, first of all, a well-studied protein.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Mapping, Sequence

from .stats import benjamini_hochberg, hypergeometric_test

__all__ = ["network_topology", "set_overlap"]

#: The pairs of keys an edge record may name its two ends with (STRING's among them).
_END_KEYS = (("a", "b"), ("source", "target"), ("from", "to"), ("node1", "node2"),
             ("preferredName_A", "preferredName_B"), ("gene_a", "gene_b"),
             ("symbol_a", "symbol_b"), ("protein1", "protein2"),
             ("source_genesymbol", "target_genesymbol"), ("stringId_A", "stringId_B"))
_SCORE_KEYS = ("score", "combined_score", "weight", "confidence")
MAX_NODES = 5_000
MAX_EDGES = 100_000
#: Above this many node × edge steps the path-based measures are left out, and said so.
MAX_PATH_WORK = 30_000_000


def _name(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _edge(item: Any, index: int) -> tuple[str, str, float | None]:
    """One edge as (a, b, score): a pair or triple, or a record naming its ends."""
    if isinstance(item, Mapping):
        for ka, kb in _END_KEYS:
            if ka in item and kb in item:
                score = next((_number(item[k]) for k in _SCORE_KEYS if k in item), None)
                return _name(item[ka]), _name(item[kb]), score
        raise ValueError(
            f"edge {index} names its two ends with none of the known key pairs "
            f"({', '.join('/'.join(p) for p in _END_KEYS[:5])}, …); its keys are "
            f"{sorted(str(k) for k in item)[:12]}")
    if isinstance(item, (list, tuple)) and len(item) in (2, 3):
        return _name(item[0]), _name(item[1]), _number(item[2]) if len(item) == 3 else None
    raise ValueError(f"edge {index} must be a pair [a, b], a triple [a, b, score] or a record "
                     f"naming its two ends, not {type(item).__name__}")


def _betweenness(order: list[str], adjacency: Mapping[str, list[str]]) -> dict[str, float]:
    """Brandes' betweenness for an unweighted, undirected graph, normalised to [0, 1]."""
    score = dict.fromkeys(order, 0.0)
    for s in order:
        stack: list[str] = []
        preds: dict[str, list[str]] = {v: [] for v in order}
        sigma = dict.fromkeys(order, 0.0)
        dist = dict.fromkeys(order, -1)
        sigma[s] = 1.0
        dist[s] = 0
        queue = deque([s])
        while queue:
            v = queue.popleft()
            stack.append(v)
            for w in adjacency[v]:
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    queue.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    preds[w].append(v)
        delta = dict.fromkeys(order, 0.0)
        while stack:
            w = stack.pop()
            for v in preds[w]:
                delta[v] += sigma[v] / sigma[w] * (1.0 + delta[w])
            if w != s:
                score[w] += delta[w]
    n = len(order)
    scale = 1.0 / ((n - 1) * (n - 2)) if n > 2 else 0.0       # each pair is counted twice
    return {v: score[v] * scale for v in order}


def network_topology(edges: Sequence[Any], nodes: Sequence[str] | None = None,
                     min_score: float | None = None, top: int = 25) -> dict[str, Any]:
    """Descriptive topology of an undirected network: degree, centralities, clustering, components.

    ``edges`` are pairs ``[a, b]``, triples ``[a, b, score]`` or records that name their two
    ends (``a``/``b``, ``source``/``target``, STRING's ``preferredName_A``/``preferredName_B``
    …) and optionally a ``score``; ``nodes`` adds nodes that have no edge. With ``min_score``
    an edge whose score is lower is left out (one without a score is kept and counted).
    Self-loops are dropped and a repeated edge counts once; both are counted in ``dropped``.

    ``ranking`` lists the ``top`` nodes by degree, then betweenness, then name (``top`` 0:
    every node). Betweenness is Brandes' shortest-path betweenness normalised to [0, 1];
    closeness is the Wasserman–Faust form, which stays comparable between components;
    clustering is the local coefficient. These describe the network that was given: a hub
    of an interaction database is a much-studied protein, and no measure here is a test.
    """
    if isinstance(edges, (str, bytes)) or not isinstance(edges, Sequence):
        raise ValueError("edges must be a list of pairs, triples or records")
    if len(edges) > MAX_EDGES:
        raise ValueError(f"at most {MAX_EDGES} edges ({len(edges)} given)")
    if not isinstance(top, int) or isinstance(top, bool) or top < 0:
        raise ValueError("top must be a non-negative integer")
    threshold = None if min_score is None else _number(min_score)
    if min_score is not None and threshold is None:
        raise ValueError("min_score must be a number")

    adjacency: dict[str, set[str]] = {}
    dropped = {"self_loops": 0, "duplicates": 0, "below_min_score": 0}
    unscored = 0
    count = 0
    for i, item in enumerate(edges):
        a, b, score = _edge(item, i)
        if not a or not b:
            raise ValueError(f"edge {i} has an empty end")
        if threshold is not None:
            if score is None:
                unscored += 1
            elif score < threshold:
                dropped["below_min_score"] += 1
                continue
        if a == b:
            dropped["self_loops"] += 1
            adjacency.setdefault(a, set())
            continue
        if b in adjacency.get(a, ()):
            dropped["duplicates"] += 1
            continue
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
        count += 1
    for extra in nodes or ():
        name = _name(extra)
        if name:
            adjacency.setdefault(name, set())
    if not adjacency:
        raise ValueError("no node: give at least one edge or node")
    if len(adjacency) > MAX_NODES:
        raise ValueError(f"at most {MAX_NODES} nodes ({len(adjacency)} given)")

    order = sorted(adjacency)
    neighbours = {v: sorted(adjacency[v]) for v in order}
    n = len(order)
    degree = {v: len(neighbours[v]) for v in order}

    # components, each numbered by size (1 the largest), ties by its first name
    component: dict[str, int] = {}
    groups: list[list[str]] = []
    for v in order:
        if v in component:
            continue
        group = []
        queue = deque([v])
        component[v] = -1
        while queue:
            x = queue.popleft()
            group.append(x)
            for w in neighbours[x]:
                if w not in component:
                    component[w] = -1
                    queue.append(w)
        groups.append(sorted(group))
    groups.sort(key=lambda g: (-len(g), g[0]))
    for idx, group in enumerate(groups, 1):
        for v in group:
            component[v] = idx

    clustering: dict[str, float] = {}
    for v in order:
        k = degree[v]
        if k < 2:
            clustering[v] = 0.0
            continue
        nb = neighbours[v]
        links = sum(1 for i, x in enumerate(nb) for y in nb[i + 1:] if y in adjacency[x])
        clustering[v] = 2.0 * links / (k * (k - 1))

    paths = n * max(count, 1) <= MAX_PATH_WORK
    betweenness: dict[str, float] = {}
    closeness: dict[str, float] = {}
    if paths:
        betweenness = _betweenness(order, neighbours)
        for v in order:
            dist = {v: 0}
            queue = deque([v])
            total = 0
            while queue:
                x = queue.popleft()
                for w in neighbours[x]:
                    if w not in dist:
                        dist[w] = dist[x] + 1
                        total += dist[w]
                        queue.append(w)
            reach = len(dist) - 1
            closeness[v] = (reach / (n - 1)) * (reach / total) if total and n > 1 else 0.0

    def row(v: str) -> dict[str, Any]:
        out: dict[str, Any] = {"node": v, "degree": degree[v]}
        if paths:
            out["betweenness"] = round(betweenness[v], 6)
            out["closeness"] = round(closeness[v], 6)
        out["clustering"] = round(clustering[v], 6)
        out["component"] = component[v]
        return out

    ranked = sorted(order, key=lambda v: (-degree[v], -betweenness.get(v, 0.0), v))
    shown = ranked if top == 0 else ranked[:top]
    degrees = sorted(degree.values())
    mid = n // 2
    median = float(degrees[mid]) if n % 2 else (degrees[mid - 1] + degrees[mid]) / 2.0
    isolated = [v for v in order if degree[v] == 0]
    result: dict[str, Any] = {
        "nodes": n, "edges": count,
        "density": round(2.0 * count / (n * (n - 1)), 6) if n > 1 else 0.0,
        "degree": {"mean": round(2.0 * count / n, 6), "median": median, "max": degrees[-1]},
        "components": {"count": len(groups), "sizes": [len(g) for g in groups[:20]],
                       "largest": len(groups[0])},
        "isolated": {"count": len(isolated), "nodes": isolated[:100]},
        "mean_clustering": round(sum(clustering.values()) / n, 6),
        "ranking": [row(v) for v in shown], "ranked_by": "degree, then betweenness, then name",
        "shown": len(shown), "dropped": dropped,
        "method": ("undirected, unweighted; betweenness: Brandes, normalised by "
                   "(n-1)(n-2)/2; closeness: Wasserman–Faust; clustering: local coefficient"),
        "interpretation": ("descriptive only: degree and centrality in a network built from "
                           "an interaction database also reflect how much each protein has "
                           "been studied; no significance is claimed")}
    if threshold is not None:
        result["min_score"] = threshold
        result["edges_without_score"] = unscored
    if not paths:
        result["note"] = (f"betweenness and closeness were not computed: {n} nodes × {count} "
                          f"edges is above the limit of {MAX_PATH_WORK} steps")
    return result


def set_overlap(sets: Mapping[str, Sequence[Any]], background: int | None = None,
                case_sensitive: bool = False, max_items: int = 500) -> dict[str, Any]:
    """Overlaps of two to eight named sets, with a chance test when a background is given.

    The result gives each set's size, every pairwise intersection with its Jaccard index, the
    items common to all, and those exclusive to each. Items are compared as text, ignoring
    case and surrounding spaces unless ``case_sensitive``; the first spelling seen is the one
    reported. ``regions`` are the
    exact cells of the Venn diagram (items in just these sets and no other), given for up to
    five sets. With ``background`` (the number of items both sets were drawn from, e.g. the
    annotated genes) each pair also gets the hypergeometric probability of an overlap at
    least as large, its fold enrichment, and a Benjamini–Hochberg q-value over the pairs
    tested. That test holds only when both sets really come from that background: targets
    found only because they were assayed, or genes listed only because they are studied,
    overlap more than chance for that reason alone. Lists of items are cut at ``max_items``;
    counts are always exact.
    """
    if not isinstance(sets, Mapping) or not 2 <= len(sets) <= 8:
        raise ValueError("sets must name two to eight sets: {name: [items]}")
    if not isinstance(max_items, int) or isinstance(max_items, bool) or max_items < 0:
        raise ValueError("max_items must be a non-negative integer")
    spelling: dict[str, str] = {}
    members: dict[str, set[str]] = {}
    for name, items in sets.items():
        if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
            raise ValueError(f"set {name!r} must be a list of items")
        if len(items) > 100_000:
            raise ValueError(f"set {name!r} has more than 100000 items")
        keys = set()
        for item in items:
            text = _name(item)
            if not text:
                continue
            key = text if case_sensitive else text.casefold()
            spelling.setdefault(key, text)
            keys.add(key)
        if not keys:
            raise ValueError(f"set {name!r} is empty")
        members[str(name)] = keys
    names = list(members)
    union = set().union(*members.values())
    if background is not None:
        if not isinstance(background, int) or isinstance(background, bool):
            raise ValueError("background must be an integer")
        if background < len(union):
            raise ValueError(f"background ({background}) is smaller than the union of the "
                             f"sets ({len(union)}): they cannot all come from it")

    def listed(keys: set[str]) -> list[str]:
        return sorted((spelling[k] for k in keys), key=lambda s: (s.casefold(), s))[:max_items]

    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            both = members[a] & members[b]
            either = members[a] | members[b]
            row: dict[str, Any] = {"a": a, "b": b, "intersection": len(both),
                                   "jaccard": round(len(both) / len(either), 6),
                                   "items": listed(both)}
            if background is not None:
                test = hypergeometric_test(len(both), len(members[a]), len(members[b]),
                                           background)
                row["p_value"] = test["p_value"]
                row["fold_enrichment"] = test["fold_enrichment"]
            pairs.append(row)
    if background is not None:
        for row, q in zip(pairs, benjamini_hochberg([r["p_value"] for r in pairs])["q_values"]):
            row["q_value"] = q
    common = set.intersection(*members.values())
    result: dict[str, Any] = {
        "sizes": {name: len(members[name]) for name in names}, "union": len(union),
        "common_to_all": {"count": len(common), "items": listed(common)},
        "pairwise": pairs,
        "exclusive": {name: {"count": len(only), "items": listed(only)}
                      for name in names
                      for only in [members[name] - set().union(
                          *(members[o] for o in names if o != name))]},
        "comparison": "exact text" if case_sensitive else "text, ignoring case"}
    if len(names) <= 5:
        regions = []
        for mask in range(1, 1 << len(names)):
            inside = [names[i] for i in range(len(names)) if mask >> i & 1]
            outside = [names[i] for i in range(len(names)) if not mask >> i & 1]
            cell = set.intersection(*(members[x] for x in inside))
            for x in outside:
                cell = cell - members[x]
            if cell:
                regions.append({"in": inside, "count": len(cell), "items": listed(cell)})
        regions.sort(key=lambda r: (-len(r["in"]), -r["count"], r["in"]))
        result["regions"] = regions
    if background is not None:
        result["background"] = background
        result["test"] = ("hypergeometric, upper tail, for each pair; Benjamini–Hochberg over "
                          f"the {len(pairs)} pair(s) tested")
        result["caution"] = ("the test holds only if both sets were drawn from this "
                             "background; sets selected by what was assayed or studied "
                             "overlap more than chance for that reason alone")
    return result
