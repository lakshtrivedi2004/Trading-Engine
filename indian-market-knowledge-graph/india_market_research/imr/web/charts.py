"""Chart specs (plain dicts) rendered by static/app.js with the locally bundled Chart.js."""
from __future__ import annotations

import math

import pandas as pd

UP, DOWN, MUTED, ACCENT, GOLD, VIOLET, PINK = "#34d399", "#fb7185", "#64748b", "#22d3ee", "#fbbf24", "#a78bfa", "#f472b6"
INK = "@ink"     # replaced in app.js by the theme's text colour (works in light and dark mode)
PALETTE = ["#22d3ee", "#a78bfa", "#34d399", "#fbbf24", "#f472b6", "#60a5fa", "#fb7185", "#94a3b8"]


def _clean(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, 6)


def _thin(index: pd.Index, max_points: int = 1500) -> slice:
    step = max(1, len(index) // max_points)
    return slice(None, None, step)


def line(series: dict[str, pd.Series], title: str = "", unit: str = "", y_title: str = "", colors=None,
         y2: dict[str, pd.Series] | None = None, y2_title: str = "", y_min=None, y_max=None, dashed=(),
         fill=()) -> dict:
    """Time-series line chart; all series share the union date axis (thinned for speed)."""
    frame = pd.DataFrame(series)
    if y2:
        for k, s in y2.items():
            frame[k] = s
    frame = frame.sort_index()
    frame = frame.iloc[_thin(frame.index)]
    labels = [d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d) for d in frame.index]
    ds = []
    for i, k in enumerate(series):
        c = (colors or PALETTE)[i % len(colors or PALETTE)]
        ds.append({"label": k, "data": [_clean(v) for v in frame[k]], "color": c,
                   **({"borderDash": [5, 4]} if k in dashed else {}), **({"fill": True} if k in fill else {})})
    for k in (y2 or {}):
        ds.append({"label": k, "data": [_clean(v) for v in frame[k]], "color": INK, "yAxisID": "y2",
                   "borderDash": [5, 4]})
    return {"type": "line", "labels": labels, "datasets": ds, "title": title, "unit": unit, "yTitle": y_title,
            "y2": y2_title if y2 else None, "yMin": y_min, "yMax": y_max}


def offsets_line(paths: dict[str, pd.Series], title: str = "", unit: str = "pct") -> dict:
    """Event-study paths keyed by label, x = sessions from event."""
    idx = sorted(set().union(*[set(p.index) for p in paths.values()])) if paths else []
    ds = [{"label": k, "data": [_clean(p.get(i)) for i in idx], "color": PALETTE[n % len(PALETTE)], "pointRadius": 2}
          for n, (k, p) in enumerate(paths.items())]
    return {"type": "line", "labels": [str(i) for i in idx], "datasets": ds, "title": title, "unit": unit,
            "xTitle": "sessions from event (0 = event day)", "yTitle": "cumulative excess return, %"}


def hbar(labels: list[str], values: list[float], title: str = "", unit: str = "pct", colors=None) -> dict:
    cols = colors or [UP if (v or 0) >= 0 else DOWN for v in values]
    return {"type": "hbar", "labels": labels, "datasets": [{"label": "", "data": [_clean(v) for v in values],
                                                            "color": cols}], "title": title, "unit": unit}


def bars(series: dict[str, pd.Series], title: str = "", unit: str = "", colors=None) -> dict:
    frame = pd.DataFrame(series).sort_index()
    labels = [d.strftime("%Y-%m-%d") for d in frame.index]
    ds = [{"label": k, "data": [_clean(v) for v in frame[k]], "color": (colors or PALETTE)[i]}
          for i, k in enumerate(series)]
    return {"type": "bar", "labels": labels, "datasets": ds, "title": title, "unit": unit}


def corr_color(v: float) -> str:
    """Background for a correlation cell: blue for positive, red for negative."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "transparent"
    a = min(abs(v), 1) * 0.55
    return f"rgba(34,211,238,{a:.2f})" if v >= 0 else f"rgba(251,113,133,{a:.2f})"
