#!/usr/bin/env python3
"""Compile kg/extract/M*.yaml into the knowledge graph artifacts.

Outputs (all under kg/out/):
  graph.json              full graph {meta, nodes, edges}
  nodes.jsonl, edges.jsonl one record per line (+ `text` field for embeddings / RAG)
  memory.md               compact single-file memory for LLM context windows
  rules.json              only nodes carrying machine-readable conditions/formulas (bot rules)
  neo4j/nodes.csv, neo4j/edges.csv, neo4j/import.cypher
  vault/                  Obsidian-style markdown vault, one note per node with [[wikilinks]]
  viewer.html             interactive graph explorer (open in a browser)
  BUILD_REPORT.md         validation report (dangling links, stubs, duplicates, stats)

Usage: python3 kg/build.py
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
EXTRACT = ROOT / "extract"
OUT = ROOT / "out"

NODE_TYPES = {
    "Module", "Concept", "Instrument", "Participant", "Regulation", "Tax", "Process", "Indicator",
    "CandlestickPattern", "ChartPattern", "Formula", "Ratio", "Greek", "Strategy", "Rule",
    "Psychology", "Model", "Product", "Event", "Contract",
}
REL_TYPES = {
    "IS_A", "PART_OF", "USES", "COMPUTED_FROM", "CONFIRMS", "CONTRADICTS", "SIGNALS", "VARIANT_OF",
    "OPPOSITE_OF", "PREREQUISITE", "HEDGES", "AFFECTS", "REGULATED_BY", "TAXED_AS", "APPLIES_TO",
    "RELATED_TO", "EXAMPLE_OF", "MEASURES",
    "COVERED_IN",  # build-generated: node -> Module
}
SYMMETRIC = {"RELATED_TO", "OPPOSITE_OF", "CONTRADICTS"}
LIST_FIELDS = ("aliases", "facts", "caveats")
DICT_FIELDS = ("formula", "conditions", "params")
TEXT_FIELDS = ("summary", "bot_use", "example")

# Targets referenced in links but never defined in any module: minimal hand-written definitions
# so the graph has no empty stubs for core trading ideas.
STUB_DEFS = {
    "stop_loss": ("Rule", "Stop Loss", "A pre-defined exit price that caps the loss on a trade; placed at a technical level (recent low/high, pattern low) or volatility-based (ATR/SD multiple) and never widened once placed."),
    "expectancy": ("Formula", "Expectancy", "Average P&L per trade = win_rate * avg_win - loss_rate * avg_loss; a system must have positive expectancy after costs."),
    "quantitative_analysis": ("Concept", "Quantitative Analysis", "Systematic, data-driven analysis of prices and fundamentals using statistics; basis of trading systems and backtesting."),
    "execution": ("Process", "Trade Execution", "Turning a signal into fills - order type choice, slippage, impact cost and timing."),
    "pivot_points": ("Indicator", "Pivot Points", "Support/resistance levels derived from prior period H/L/C: P=(H+L+C)/3, R1=2P-L, S1=2P-H."),
    "dividend_yield": ("Ratio", "Dividend Yield", "Dividend per share / market price per share; income return of a stock."),
    "averaging_down": ("Rule", "Averaging Down", "Adding to a losing position to lower average cost; Varsity warns against it for traders since it enlarges risk on a failed thesis."),
}


def load_modules():
    modules = []
    for path in sorted(EXTRACT.glob("M*.yaml")):
        with path.open(encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        doc["_file"] = path.name
        modules.append(doc)
    return modules


def as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def as_dict(x, default_key):
    if x is None:
        return {}
    if isinstance(x, dict):
        return {str(k): v for k, v in x.items()}
    return {default_key: x}


def stringify(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def build():
    problems = defaultdict(list)
    modules = load_modules()
    nodes: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}

    def add_edge(src, dst, rel, origin, note=None):
        key = (src, dst, rel)
        if rel in SYMMETRIC and (dst, src, rel) in edges:
            return
        if key not in edges:
            edges[key] = {"source": src, "target": dst, "rel": rel, "modules": [origin]}
            if note:
                edges[key]["note"] = note
        elif origin not in edges[key]["modules"]:
            edges[key]["modules"].append(origin)

    for mod in modules:
        mid = mod["module"]
        mnode_id = f"module_{mid.lower()}"
        nodes[mnode_id] = {
            "id": mnode_id, "type": "Module", "name": f"{mid} - {mod.get('title', '')}",
            "modules": [mid], "summary": mod.get("summary", ""),
        }
        if mod.get("global_caveat"):
            nodes[mnode_id]["caveats"] = [mod["global_caveat"]]

        for raw in mod.get("nodes") or []:
            nid = raw.get("id")
            if not nid or not re.fullmatch(r"[a-z0-9_]+", nid):
                problems["bad_id"].append(f"{mod['_file']}: {nid!r}")
                continue
            ntype = raw.get("type")
            if ntype not in NODE_TYPES:
                problems["bad_type"].append(f"{mod['_file']}: {nid} type={ntype}")
            for req in ("name", "summary"):
                if not raw.get(req):
                    problems["missing_field"].append(f"{mod['_file']}: {nid} lacks {req}")

            chapters = [f"{mid}:{c}" for c in as_list(raw.get("chapter"))]
            if nid in nodes:
                n = nodes[nid]
                problems["merged_duplicate"].append(f"{nid}: {','.join(n['modules'])} + {mid}")
                if mid not in n["modules"]:
                    n["modules"].append(mid)
                n["chapters"] = n.get("chapters", []) + chapters
                for f in LIST_FIELDS:
                    extra = [x for x in as_list(raw.get(f)) if x not in n.get(f, [])]
                    if extra:
                        n[f] = n.get(f, []) + extra
                for f in DICT_FIELDS:
                    d = as_dict(raw.get(f), "main")
                    if d:
                        tgt = n.setdefault(f, {})
                        for k, v in d.items():
                            tgt[k if k not in tgt else f"{k}_{mid}"] = v
                for f in TEXT_FIELDS:
                    if raw.get(f):
                        if not n.get(f):
                            n[f] = raw[f]
                        elif raw[f] != n[f]:
                            n[f] = f"{n[f]} | [{mid}] {raw[f]}"
                if n.get("stub"):
                    n.pop("stub")
                    n["type"] = ntype or n["type"]
                    n["name"] = raw.get("name", n["name"])
            else:
                n = {"id": nid, "type": ntype, "name": raw.get("name", nid), "modules": [mid],
                     "chapters": chapters}
                for f in LIST_FIELDS:
                    if raw.get(f):
                        n[f] = as_list(raw[f])
                for f in DICT_FIELDS:
                    if raw.get(f):
                        n[f] = as_dict(raw[f], "main")
                for f in TEXT_FIELDS:
                    if raw.get(f):
                        n[f] = raw[f] if isinstance(raw[f], str) else stringify(raw[f])
                nodes[nid] = n

            add_edge(nid, mnode_id, "COVERED_IN", mid)
            for link in raw.get("links") or []:
                if not isinstance(link, dict) or "to" not in link:
                    problems["bad_link"].append(f"{mod['_file']}: {nid} -> {link!r}")
                    continue
                rel = link.get("rel", "RELATED_TO")
                if rel not in REL_TYPES:
                    problems["bad_rel"].append(f"{mod['_file']}: {nid} -{rel}-> {link['to']}")
                    rel = "RELATED_TO"
                if link["to"] == nid:
                    continue
                add_edge(nid, link["to"], rel, mid, link.get("note"))

    # dangling targets -> stubs
    for (src, dst, rel) in list(edges):
        if dst not in nodes:
            problems["dangling_target"].append(f"{dst} (from {src} {rel})")
            if dst in STUB_DEFS:
                t, name, summ = STUB_DEFS[dst]
                nodes[dst] = {"id": dst, "type": t, "name": name, "modules": [], "summary": summ,
                              "stub": "curated"}
            else:
                nodes[dst] = {"id": dst, "type": "Concept", "name": dst.replace("_", " ").title(),
                              "modules": [], "summary": "(stub - referenced but not defined in source modules)",
                              "stub": "auto"}

    # degree + neighbours
    out_adj, in_adj = defaultdict(list), defaultdict(list)
    for e in edges.values():
        out_adj[e["source"]].append(e)
        in_adj[e["target"]].append(e)
    for nid, n in nodes.items():
        n["degree"] = len(out_adj[nid]) + len(in_adj[nid])
        n["text"] = node_text(n)

    return modules, nodes, list(edges.values()), out_adj, in_adj, problems


def node_text(n):
    """Flat text for embeddings / retrieval."""
    parts = [f"{n['name']} ({n['type']})"]
    if n.get("aliases"):
        parts.append("Also: " + ", ".join(map(str, n["aliases"])))
    parts.append(str(n.get("summary", "")))
    for f in ("formula", "conditions", "params"):
        if n.get(f):
            parts.append(f"{f.title()}: " + "; ".join(f"{k}: {stringify(v)}" for k, v in n[f].items()))
    for f in ("facts", "caveats"):
        if n.get(f):
            parts.append(f"{f.title()}: " + " ".join(f"- {stringify(x)}" for x in n[f]))
    for f in ("example", "bot_use"):
        if n.get(f):
            parts.append(f"{f.replace('_', ' ').title()}: {n[f]}")
    return "\n".join(parts)


# ---------------------------------------------------------------- emitters

def emit_json(nodes, edges, modules, problems):
    meta = {
        "name": "Indian Market Knowledge Graph (Zerodha Varsity M1-M14)",
        "node_count": len(nodes), "edge_count": len(edges),
        "node_types": dict(Counter(n["type"] for n in nodes.values()).most_common()),
        "rel_types": dict(Counter(e["rel"] for e in edges).most_common()),
        "modules": {m["module"]: m.get("title", "") for m in modules},
        "warning": "Regulatory/tax/contract-spec details reflect the source's publication dates - see node caveats.",
    }
    (OUT / "graph.json").write_text(json.dumps(
        {"meta": meta, "nodes": list(nodes.values()), "edges": edges}, ensure_ascii=False, indent=1), encoding="utf-8")
    with (OUT / "nodes.jsonl").open("w", encoding="utf-8") as fh:
        for n in nodes.values():
            fh.write(json.dumps(n, ensure_ascii=False) + "\n")
    with (OUT / "edges.jsonl").open("w", encoding="utf-8") as fh:
        for e in edges:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    rules = [{k: n[k] for k in ("id", "type", "name", "summary", "formula", "conditions", "params",
                                  "bot_use", "caveats", "modules") if k in n}
             for n in nodes.values() if n.get("conditions") or n.get("formula") or n.get("bot_use")]
    (OUT / "rules.json").write_text(json.dumps(rules, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta, len(rules)


def emit_neo4j(nodes, edges):
    d = OUT / "neo4j"
    d.mkdir(parents=True, exist_ok=True)
    cols = ["id:ID", "name", "type:LABEL", "modules", "summary", "formula", "conditions", "params",
            "bot_use", "caveats", "facts", "stub"]
    with (d / "nodes.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for n in nodes.values():
            w.writerow([n["id"], n["name"], n["type"], ";".join(n["modules"]), n.get("summary", ""),
                        stringify(n.get("formula", "")), stringify(n.get("conditions", "")),
                        stringify(n.get("params", "")), n.get("bot_use", ""),
                        stringify(n.get("caveats", "")), stringify(n.get("facts", "")), n.get("stub", "")])
    with (d / "edges.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([":START_ID", ":END_ID", ":TYPE", "modules"])
        for e in edges:
            w.writerow([e["source"], e["target"], e["rel"], ";".join(e["modules"])])
    (d / "import.cypher").write_text("""// Option A: neo4j-admin database import full --nodes=nodes.csv --relationships=edges.csv <db>
// Option B: copy nodes.csv/edges.csv into Neo4j's import dir, then run:
CREATE CONSTRAINT kg_id IF NOT EXISTS FOR (n:KG) REQUIRE n.id IS UNIQUE;
LOAD CSV WITH HEADERS FROM 'file:///nodes.csv' AS r
CALL apoc.create.node(['KG', r.`type:LABEL`], {id: r.`id:ID`, name: r.name, modules: r.modules,
  summary: r.summary, formula: r.formula, conditions: r.conditions, params: r.params,
  bot_use: r.bot_use, caveats: r.caveats, facts: r.facts, stub: r.stub}) YIELD node RETURN count(node);
LOAD CSV WITH HEADERS FROM 'file:///edges.csv' AS r
MATCH (a:KG {id: r.`:START_ID`}), (b:KG {id: r.`:END_ID`})
CALL apoc.create.relationship(a, r.`:TYPE`, {modules: r.modules}, b) YIELD rel RETURN count(rel);
// Example: MATCH p=(s:Strategy)-[:USES*1..2]->(i:Indicator) RETURN p LIMIT 50;
""", encoding="utf-8")


def md_value(v):
    return stringify(v).replace("\n", " ")


def emit_vault(nodes, out_adj, in_adj):
    d = OUT / "vault"
    if d.exists():
        for p in d.rglob("*.md"):
            p.unlink()
    d.mkdir(parents=True, exist_ok=True)
    by_type = defaultdict(list)
    for n in nodes.values():
        by_type[n["type"]].append(n)
        lines = ["---", f"id: {n['id']}", f"type: {n['type']}", f"modules: [{', '.join(n['modules'])}]"]
        if n.get("aliases"):
            lines.append("aliases: [" + ", ".join(json.dumps(str(a)) for a in n["aliases"]) + "]")
        lines += [f"tags: [{n['type'].lower()}]", "---", f"# {n['name']}", "", str(n.get("summary", "")), ""]
        for f, title in (("formula", "Formula"), ("conditions", "Conditions (machine rules)"), ("params", "Parameters")):
            if n.get(f):
                lines.append(f"## {title}")
                lines += [f"- **{k}**: `{md_value(v)}`" for k, v in n[f].items()]
                lines.append("")
        for f, title in (("facts", "Facts"), ("caveats", "Caveats")):
            if n.get(f):
                lines.append(f"## {title}")
                lines += [f"- {md_value(x)}" for x in n[f]]
                lines.append("")
        for f, title in (("example", "Example"), ("bot_use", "Bot use")):
            if n.get(f):
                lines += [f"## {title}", str(n[f]), ""]
        if out_adj[n["id"]]:
            lines.append("## Links")
            lines += [f"- {e['rel']} → [[{e['target']}]]" for e in out_adj[n["id"]]]
            lines.append("")
        if in_adj[n["id"]]:
            lines.append("## Backlinks")
            lines += [f"- [[{e['source']}]] {e['rel']} →" for e in in_adj[n["id"]]]
            lines.append("")
        (d / f"{n['id']}.md").write_text("\n".join(lines), encoding="utf-8")
    idx = ["# Knowledge Graph Index", ""]
    for t in sorted(by_type):
        idx.append(f"## {t} ({len(by_type[t])})")
        idx += [f"- [[{n['id']}]] — {n['name']}" for n in sorted(by_type[t], key=lambda x: x["name"])]
        idx.append("")
    (d / "INDEX.md").write_text("\n".join(idx), encoding="utf-8")


def emit_memory(nodes, out_adj, modules):
    """One compact markdown file: the whole graph as an LLM-loadable memory."""
    lines = ["# Indian Market Trading Memory (compiled from Zerodha Varsity M1-M14)",
             "Format: `id` [Type] Name: summary | F: formulas | C: conditions | P: params | Bot: usage | ⚠ caveats | → links",
             "Dated rules (tax, settlement, lot sizes, specs) must be re-verified against current SEBI/IT rules.", ""]
    order = [m["module"] for m in modules]
    groups = defaultdict(list)
    for n in nodes.values():
        if n["type"] == "Module":
            continue
        groups[n["modules"][0] if n["modules"] else "STUB"].append(n)
    titles = {m["module"]: m.get("title", "") for m in modules}
    for g in order + ["STUB"]:
        if not groups.get(g):
            continue
        lines.append(f"## {g} {titles.get(g, 'Stubs')}")
        for n in groups[g]:
            s = f"- `{n['id']}` [{n['type']}] {n['name']}: {n.get('summary', '')}"
            if n.get("formula"):
                s += " | F: " + "; ".join(f"{k}={md_value(v)}" for k, v in n["formula"].items())
            if n.get("conditions"):
                s += " | C: " + "; ".join(f"{k}: {md_value(v)}" for k, v in n["conditions"].items())
            if n.get("params"):
                s += " | P: " + md_value(n["params"])
            if n.get("bot_use"):
                s += f" | Bot: {n['bot_use']}"
            if n.get("caveats"):
                s += " | ⚠ " + " ".join(md_value(c) for c in n["caveats"])
            links = [f"{e['rel']}:{e['target']}" for e in out_adj[n["id"]] if e["rel"] != "COVERED_IN"]
            if links:
                s += " | → " + ", ".join(links)
            lines.append(s)
        lines.append("")
    (OUT / "memory.md").write_text("\n".join(lines), encoding="utf-8")


def emit_viewer(nodes, edges, meta):
    """Self-contained interactive viewer (d3 from cdnjs) with the graph embedded."""
    tpl = (ROOT / "viewer_template.html").read_text(encoding="utf-8")
    keep = ("id", "type", "name", "modules", "aliases", "summary", "formula", "conditions", "params",
            "bot_use", "facts", "example", "caveats")
    data = {"meta": {"modules": meta["modules"]},
            "nodes": [{k: n[k] for k in keep if k in n} for n in nodes.values()],
            "edges": [{"source": e["source"], "target": e["target"], "rel": e["rel"]} for e in edges]}
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    (OUT / "viewer.html").write_text(tpl.replace("/*__GRAPH__*/null", blob), encoding="utf-8")


def emit_report(meta, problems, nodes, n_rules):
    lines = ["# Build report", "", f"- nodes: {meta['node_count']}", f"- edges: {meta['edge_count']}",
             f"- bot rule nodes (formula/conditions/bot_use): {n_rules}", "",
             "## Node types", *[f"- {k}: {v}" for k, v in meta["node_types"].items()], "",
             "## Relationship types", *[f"- {k}: {v}" for k, v in meta["rel_types"].items()], ""]
    hubs = sorted((n for n in nodes.values() if n["type"] != "Module"), key=lambda n: -n["degree"])[:25]
    lines += ["## Top hubs", *[f"- {n['id']} ({n['degree']})" for n in hubs], ""]
    for k in sorted(problems):
        lines += [f"## {k} ({len(problems[k])})", *[f"- {p}" for p in sorted(set(problems[k]))], ""]
    (OUT / "BUILD_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    OUT.mkdir(exist_ok=True)
    modules, nodes, edges, out_adj, in_adj, problems = build()
    meta, n_rules = emit_json(nodes, edges, modules, problems)
    emit_neo4j(nodes, edges)
    emit_vault(nodes, out_adj, in_adj)
    emit_memory(nodes, out_adj, modules)
    emit_viewer(nodes, edges, meta)
    emit_report(meta, problems, nodes, n_rules)
    print(f"nodes={meta['node_count']} edges={meta['edge_count']} rules={n_rules}")
    for k in sorted(problems):
        print(f"  {k}: {len(set(problems[k]))}")
    hard = [k for k in ("bad_id", "bad_type", "bad_rel", "bad_link", "missing_field") if problems.get(k)]
    if hard:
        print("validation errors:", hard, "- see kg/out/BUILD_REPORT.md", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
