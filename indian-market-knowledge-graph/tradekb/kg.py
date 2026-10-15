"""Read-only access to the compiled knowledge graph (kg/out/graph.json).

    from tradekb import KG
    kg = KG()
    kg.search("pair trading z-score")
    kg.context("pair_trading_regression_method", depth=1)   # markdown bundle for an LLM prompt
    kg.rules(type="Strategy")
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter, deque
from functools import cached_property
from pathlib import Path

DEFAULT_GRAPH = Path(__file__).resolve().parent.parent / "kg" / "out" / "graph.json"
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class KG:
    def __init__(self, path: str | os.PathLike | None = None):
        path = Path(path or os.environ.get("TRADEKB_GRAPH", DEFAULT_GRAPH))
        if not path.exists():
            raise FileNotFoundError(f"{path} missing - run: python3 kg/build.py")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.meta = data["meta"]
        self.nodes: dict[str, dict] = {n["id"]: n for n in data["nodes"]}
        self.edges: list[dict] = data["edges"]
        self.out: dict[str, list[dict]] = {k: [] for k in self.nodes}
        self.inc: dict[str, list[dict]] = {k: [] for k in self.nodes}
        for e in self.edges:
            self.out[e["source"]].append(e)
            self.inc[e["target"]].append(e)

    # ------------------------------------------------------------ lookup
    def __getitem__(self, node_id: str) -> dict:
        return self.nodes[node_id]

    def __contains__(self, node_id: str) -> bool:
        return node_id in self.nodes

    def resolve(self, term: str) -> str | None:
        """id, exact name or alias (case-insensitive) -> id."""
        if term in self.nodes:
            return term
        t = term.lower().strip()
        for n in self.nodes.values():
            if n["name"].lower() == t or t in (str(a).lower() for a in n.get("aliases", [])):
                return n["id"]
        slug = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
        return slug if slug in self.nodes else None

    @cached_property
    def _index(self):
        docs = {nid: Counter(_tokens(n["id"].replace("_", " ") + " " + n.get("text", n["name"])))
                for nid, n in self.nodes.items()}
        df = Counter(t for c in docs.values() for t in c)
        return docs, df

    def search(self, query: str, k: int = 10, type: str | None = None) -> list[tuple[str, float]]:
        """Simple TF-IDF keyword search over node text (name/id/aliases weighted up)."""
        import math
        docs, df = self._index
        q = _tokens(query)
        n_docs = len(docs)
        scores = []
        for nid, c in docs.items():
            node = self.nodes[nid]
            if type and node["type"] != type:
                continue
            title = set(_tokens(node["name"] + " " + nid.replace("_", " ") + " " + " ".join(map(str, node.get("aliases", [])))))
            s = sum((1 + math.log(c[t])) * math.log(n_docs / df[t]) * (3 if t in title else 1) for t in q if c[t])
            if s:
                scores.append((nid, round(s, 3)))
        return sorted(scores, key=lambda x: -x[1])[:k]

    # ------------------------------------------------------------ graph
    def neighbors(self, node_id: str, rel: str | None = None, direction: str = "both",
                  include_modules: bool = False) -> list[tuple[str, str, str]]:
        """[(rel, direction('out'|'in'), other_id)]"""
        res = []
        if direction in ("out", "both"):
            res += [(e["rel"], "out", e["target"]) for e in self.out[node_id]]
        if direction in ("in", "both"):
            res += [(e["rel"], "in", e["source"]) for e in self.inc[node_id]]
        if rel:
            res = [r for r in res if r[0] == rel]
        if not include_modules:
            res = [r for r in res if r[0] != "COVERED_IN"]
        return res

    def path(self, a: str, b: str, max_depth: int = 6) -> list[str] | None:
        """Shortest undirected path between two nodes (ignoring module hub edges)."""
        prev = {a: None}
        q = deque([a])
        while q:
            cur = q.popleft()
            if cur == b:
                out = []
                while cur is not None:
                    out.append(cur)
                    cur = prev[cur]
                return out[::-1]
            if len(self._depth(prev, cur)) > max_depth:
                continue
            for _, _, nxt in self.neighbors(cur):
                if nxt not in prev:
                    prev[nxt] = cur
                    q.append(nxt)
        return None

    @staticmethod
    def _depth(prev, cur):
        chain = []
        while cur is not None:
            chain.append(cur)
            cur = prev[cur]
        return chain

    def subgraph(self, seeds: list[str], depth: int = 1) -> set[str]:
        seen = set(seeds)
        frontier = set(seeds)
        for _ in range(depth):
            nxt = set()
            for nid in frontier:
                nxt |= {o for _, _, o in self.neighbors(nid)}
            frontier = nxt - seen
            seen |= nxt
        return seen

    def rules(self, type: str | None = None, module: str | None = None) -> list[dict]:
        """Nodes with machine-readable formula/conditions."""
        return [n for n in self.nodes.values() if (n.get("conditions") or n.get("formula"))
                and (not type or n["type"] == type) and (not module or module in n["modules"])]

    # ------------------------------------------------------------ LLM memory
    def card(self, node_id: str, full: bool = True) -> str:
        n = self.nodes[node_id]
        lines = [f"### {n['name']} (`{n['id']}`, {n['type']}, {', '.join(n['modules']) or 'stub'})",
                 str(n.get("summary", ""))]
        if full:
            for f in ("formula", "conditions", "params"):
                if n.get(f):
                    lines.append(f"**{f}:** " + "; ".join(f"{k} = {v}" for k, v in n[f].items()))
            for f in ("facts", "caveats"):
                if n.get(f):
                    lines.append(f"**{f}:** " + " | ".join(map(str, n[f])))
            for f in ("example", "bot_use"):
                if n.get(f):
                    lines.append(f"**{f}:** {n[f]}")
        rel = [f"{r}{'→' if d == 'out' else '←'}{o}" for r, d, o in self.neighbors(node_id)]
        if rel:
            lines.append("**links:** " + ", ".join(rel))
        return "\n".join(lines)

    def context(self, query_or_id: str, depth: int = 1, k: int = 5, max_nodes: int = 25) -> str:
        """Markdown memory bundle for an LLM: matched nodes in full + neighbours in brief."""
        nid = self.resolve(query_or_id)
        seeds = [nid] if nid else [i for i, _ in self.search(query_or_id, k=k)]
        if not seeds:
            return f"No knowledge-graph match for: {query_or_id}"
        ring = [x for x in self.subgraph(seeds, depth) if x not in seeds and self.nodes[x]["type"] != "Module"]
        ring.sort(key=lambda x: -len(self.out[x]) - len(self.inc[x]))
        parts = [f"## Trading knowledge for: {query_or_id}", *[self.card(s) for s in seeds]]
        if ring:
            parts.append("## Related")
            parts += [f"- `{r}` {self.nodes[r]['name']}: {self.nodes[r].get('summary', '')}"
                      for r in ring[: max(0, max_nodes - len(seeds))]]
        return "\n\n".join(parts)
