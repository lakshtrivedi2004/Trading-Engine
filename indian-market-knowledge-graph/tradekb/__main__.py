"""Query CLI:  python3 -m tradekb <command> ...

  search <query> [-k N] [--type T]   keyword search
  show <id|name>                     full node card
  context <query|id> [--depth D]     LLM-ready markdown memory bundle
  neighbors <id> [--rel REL]         adjacent nodes
  path <a> <b>                       shortest concept path
  rules [--type T] [--module M]      machine-readable rules as JSON
  stats                              graph statistics
"""
from __future__ import annotations

import argparse
import json
import sys

from .kg import KG


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tradekb", description="Query the Indian market trading knowledge graph")
    p.add_argument("--graph", help="path to graph.json")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search"); s.add_argument("query", nargs="+"); s.add_argument("-k", type=int, default=10); s.add_argument("--type")
    s = sub.add_parser("show"); s.add_argument("node", nargs="+")
    s = sub.add_parser("context"); s.add_argument("query", nargs="+"); s.add_argument("--depth", type=int, default=1); s.add_argument("--max", type=int, default=25)
    s = sub.add_parser("neighbors"); s.add_argument("node"); s.add_argument("--rel")
    s = sub.add_parser("path"); s.add_argument("a"); s.add_argument("b")
    s = sub.add_parser("rules"); s.add_argument("--type"); s.add_argument("--module")
    sub.add_parser("stats")
    a = p.parse_args(argv)
    kg = KG(a.graph)

    def need(term):
        nid = kg.resolve(term)
        if not nid:
            hits = kg.search(term, k=5)
            print(f"not found: {term!r}; did you mean: {', '.join(h for h, _ in hits) or '-'}", file=sys.stderr)
            sys.exit(2)
        return nid

    if a.cmd == "search":
        for nid, score in kg.search(" ".join(a.query), a.k, a.type):
            n = kg[nid]
            print(f"{score:7.2f}  {nid:40s} [{n['type']}] {n['name']}")
    elif a.cmd == "show":
        print(kg.card(need(" ".join(a.node))))
    elif a.cmd == "context":
        print(kg.context(" ".join(a.query), depth=a.depth, max_nodes=a.max))
    elif a.cmd == "neighbors":
        for rel, d, o in kg.neighbors(need(a.node), a.rel):
            print(f"{'-' if d == 'out' else '<'}{rel}{'->' if d == 'out' else '-'} {o}  ({kg[o]['name']})")
    elif a.cmd == "path":
        path = kg.path(need(a.a), need(a.b))
        print(" -> ".join(path) if path else "no path")
    elif a.cmd == "rules":
        print(json.dumps([{k: n.get(k) for k in ("id", "type", "name", "formula", "conditions", "params", "bot_use")
                           if n.get(k)} for n in kg.rules(a.type, a.module)], indent=1, ensure_ascii=False))
    elif a.cmd == "stats":
        print(json.dumps(kg.meta, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
