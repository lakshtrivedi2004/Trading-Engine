"""Is a Telegram message a BTST signal, and what does it say?

    #BTST TRADE ...
    NIFTY 22800 Call Buy 105++
    Target 130 160 200+
    Disclaimer - Only for Educational Purposes

1. A cheap pre-check: the text must carry both markers (config "markers",
   default "BTST" and "Disclaimer"); anything else is not a signal and is
   never sent to the model.
2. The model (Gemini, services/vertex_ai_mime.py) answers in JSON: whether it
   is a BTST signal, and {indices, strike_price, side, price}.
3. A strict read of the same line ("<INDEX> <strike> <Call|Put|CE|PE> ... <price>")
   checks it. The model's numbers must appear in the message, and must match
   the strict read when there is one - a disagreement is no trade. With the
   model unavailable, the strict read alone may stand in (ai_fallback_regex).
"""
import json
import re

INDICES = ("BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "NIFTY", "SENSEX", "BANKEX")

SYSTEM = """You read one Telegram message from an Indian stock-market tips channel.
Decide if it is a BTST (buy today, sell tomorrow) index-option trade signal, and if it is, extract it.
A signal names an index (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX, BANKEX...), a strike price,
the option side (Call = CE, Put = PE) and the price to buy at. Targets are NOT the buy price.
Promotions, results, follow-ups ("target hit", "book profit") and questions are not signals.
Copy numbers exactly as written; do not round or invent. Return JSON only."""

SCHEMA = {
    "type": "object",
    "properties": {
        "is_btst_signal": {"type": "boolean"},
        "indices": {"type": "string"},
        "strike_price": {"type": "number"},
        "side": {"type": "string", "enum": ["CE", "PE", ""]},
        "price": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["is_btst_signal", "reason"],
}

_SIDE = {"CE": "CE", "CALL": "CE", "CALLS": "CE", "PE": "PE", "PUT": "PE", "PUTS": "PE"}
_LINE = re.compile(r"\b(" + "|".join(INDICES) + r")\s*(\d{3,6})\s*(CE|PE|CALLS?|PUTS?)\b(.*)", re.I)
_NUM = re.compile(r"(\d+(?:\.\d+)?)")


def has_markers(text, markers=("BTST", "Disclaimer")):
    t = str(text or "").lower()
    return all(str(m).lower() in t for m in markers)


def strict_read(text):
    """{indices, strike_price, side, price} from the first line naming an
    index, a strike and a side, with the first number after the side as the
    price; None when no line reads cleanly."""
    for line in str(text or "").splitlines():
        m = _LINE.search(line.replace(",", ""))
        if not m:
            continue
        rest = m.group(4)
        n = _NUM.search(rest)
        if not n:
            return None
        return {"indices": m.group(1).upper(), "strike_price": float(m.group(2)),
                "side": _SIDE[m.group(3).upper()], "price": float(n.group(1))}
    return None


def _in_text(x, text):
    """The number x is written in the message (105 matches "105" or "105.0")."""
    nums = {float(v) for v in _NUM.findall(str(text or "").replace(",", ""))}
    try:
        return float(x) in nums
    except (TypeError, ValueError):
        return False


def parse(text, llm=None, model=None, markers=("BTST", "Disclaimer"), ai_fallback_regex=True,
          thinking="low"):
    """{"is_signal": bool, "signal": {...} | None, "parsed_by": "ai" | "regex" | "",
    "reason": str, "ai": the model's answer or error}. Never raises."""
    out = {"is_signal": False, "signal": None, "parsed_by": "", "reason": "", "ai": None}
    if not has_markers(text, markers):
        out["reason"] = f"no {' + '.join(markers)} markers"
        return out
    strict = strict_read(text)
    ai, ai_err = None, None
    if llm is not None:
        try:
            ai, meta = llm.query_json("MESSAGE:\n" + str(text), schema=SCHEMA, system_instruction=SYSTEM,
                                      model_name=model, thinking_level=thinking)
            out["ai"] = {"answer": ai, "meta": {k: meta.get(k) for k in ("model", "elapsed_s", "cost_usd")}}
        except Exception as e:
            ai_err = f"{type(e).__name__}: {str(e)[:200]}"
            out["ai"] = {"error": ai_err}
    else:
        ai_err = "no model configured"
    if ai is None:
        if ai_fallback_regex and strict:
            out.update(is_signal=True, signal=strict, parsed_by="regex",
                       reason=f"model unavailable ({ai_err}); the strict read stands in")
        else:
            out["reason"] = f"model unavailable ({ai_err})" + ("" if strict else " and no strict read")
        return out
    if not ai.get("is_btst_signal"):
        out["reason"] = f"model: not a signal ({str(ai.get('reason', ''))[:160]})"
        return out
    idx = str(ai.get("indices", "")).strip().upper().replace(" ", "")
    side = _SIDE.get(str(ai.get("side", "")).strip().upper(), "")
    sig = {"indices": idx, "strike_price": ai.get("strike_price"), "side": side, "price": ai.get("price")}
    problems = []
    if idx not in INDICES:
        problems.append(f"unknown index {idx!r}")
    if not side:
        problems.append("no CE/PE side")
    if not _in_text(sig["strike_price"], text):
        problems.append(f"strike {sig['strike_price']} is not in the message")
    if not _in_text(sig["price"], text):
        problems.append(f"price {sig['price']} is not in the message")
    if strict and not problems:
        diff = [k for k in ("indices", "strike_price", "side", "price")
                if (float(sig[k]) != float(strict[k]) if k in ("strike_price", "price") else sig[k] != strict[k])]
        if diff:
            problems.append(f"model and strict read disagree on {', '.join(diff)}: "
                            f"{json.dumps(sig)} vs {json.dumps(strict)}")
    if problems:
        out["reason"] = "model's signal refused: " + "; ".join(problems)
        return out
    sig["strike_price"], sig["price"] = float(sig["strike_price"]), float(sig["price"])
    out.update(is_signal=True, signal=sig, parsed_by="ai", reason="model and checks agree")
    return out
