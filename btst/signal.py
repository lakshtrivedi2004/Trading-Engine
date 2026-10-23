"""Is a Telegram message a BTST signal, and what does it say?

The channel's signal, every day (14 of them read from its history, May and
October 2026 - a photo whose caption is):

    #BTST TRADE ☄️☄️
    NIFTY 22800 Call Buy 105++
    🕯 Target   130   160   200+
    Disclaimer - Only for Educational Purposes

1. A cheap pre-check: the text must carry both markers (config "markers",
   default "BTST" and "Disclaimer"); anything else is not a signal and is
   never sent to the model.
2. The model (Gemini, services/vertex_ai_mime.py) answers in JSON: whether it
   is a BTST signal, and {indices, strike_price, side, price}.
3. A pattern read of the same text checks it - and stands in when the model
   fails (ai_fallback_regex, owner 23 Oct 2026):
     strict_read    "<INDEX> <strike> <Call|Put|CE|PE> ... Buy <price>" on a
                    line (the index may be on a line above it)
     template_read  the strict read inside the channel's whole template:
                    the "BTST TRADE" header, the "Buy" line, "Target", the
                    disclaimer
   The model failed  = no model, an error or timeout, an answer that is not
   the JSON asked for, or a signal whose numbers are not in the message: the
   pattern read is used (the template's, else the strict one).
   The model said "not a signal": only the full template overrides it - a
   results post ("BTST hit") is not a signal, but the exact signal template is.
   The model and the pattern read disagree: the template wins (its price is
   the number after "Buy", the model's may be a target); with no template,
   no trade.
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
# Written-out names first, so "BANK NIFTY" is not read as NIFTY.
_ALIASES = [
    (re.compile(r"\bBANK\W{0,3}NIFTY\b|\bNIFTY\W{0,3}BANK\b", re.I), "BANKNIFTY"),
    (re.compile(r"\bFIN\W{0,3}NIFTY\b|\bNIFTY\W{0,3}FIN(?:ANCIAL)?\W{0,3}(?:SERVICES?)?\b", re.I), "FINNIFTY"),
    (re.compile(r"\bMIDCAP\W{0,3}NIFTY\b|\bNIFTY\W{0,3}MID\W{0,3}(?:CAP)?\W{0,3}SELECT\b|\bMIDCP\W{0,3}NIFTY\b", re.I),
     "MIDCPNIFTY"),
    (re.compile(r"\bNIFTY\W{0,3}NEXT\W{0,3}50\b|\bNIFTY\W{0,3}NXT\W{0,3}50\b", re.I), "NIFTYNXT50"),
    (re.compile(r"\bNIFTY\W{0,2}50\b(?!\d)", re.I), "NIFTY"),
]
_IDX = r"(" + "|".join(INDICES) + r")"
# side, then not a letter - except "CallBuy" written together
_SIDE_RX = r"(CE|PE|CALLS?|PUTS?)(?=[^A-Za-z]|BUY|$)"
_LINE = re.compile(r"\b" + _IDX + r"\W{0,8}?(\d{3,6})\W{0,8}?" + _SIDE_RX + r"(.*)", re.I)
_BARE = re.compile(r"(?<![\d.])(\d{3,6})\W{0,8}?" + _SIDE_RX + r"(.*)", re.I)    # index on a line above
_ANY_IDX = re.compile(r"\b" + _IDX + r"\b", re.I)
_BUY = re.compile(r"\bBUY\b\D{0,16}?(\d+(?:\.\d+)?)", re.I)
_NUM = re.compile(r"(\d+(?:\.\d+)?)")
_HEADER = re.compile(r"#?\s*BTST\s*TRADE", re.I)


def has_markers(text, markers=("BTST", "Disclaimer")):
    t = str(text or "").lower()
    return all(str(m).lower() in t for m in markers)


def _normalise(text):
    t = str(text or "").replace(",", "")
    for rx, name in _ALIASES:
        t = rx.sub(name, t)
    return t


def _price(rest):
    """The number after "Buy" (it may say "Buy Above- 100++"), else the first one."""
    m = _BUY.search(rest) or _NUM.search(rest)
    return float(m.group(1)) if m else None


def _read(text, need_buy=False):
    idx_seen = None
    for line in _normalise(text).splitlines():
        m = _LINE.search(line)
        if m:
            idx, strike, side, rest = m.group(1), m.group(2), m.group(3), m.group(4)
        else:
            b = _BARE.search(line)
            named = _ANY_IDX.findall(line)
            if named:
                idx_seen = named[-1]
            if not (b and idx_seen and not named):
                continue
            idx, strike, side, rest = idx_seen, b.group(1), b.group(2), b.group(3)
        if need_buy and not re.search(r"BUY", rest, re.I):
            return None
        px = _price(rest)
        if px is None or px <= 0:
            return None
        return {"indices": idx.upper(), "strike_price": float(strike), "side": _SIDE[side.upper()], "price": px}
    return None


def strict_read(text):
    """{indices, strike_price, side, price} from the first line naming a strike
    and a side (the index on it or on a line above), the price being the number
    after "Buy" (else the first after the side); None when no line reads."""
    return _read(text)


def template_read(text):
    """The strict read, only inside the channel's whole signal template: the
    BTST TRADE header, a "<strike> <side> Buy <price>" line, a Target, the
    disclaimer. None otherwise."""
    t = str(text or "")
    if not (_HEADER.search(t) and re.search(r"\bTARGET", t, re.I) and re.search(r"DISCLAIMER", t, re.I)):
        return None
    return _read(t, need_buy=True)


def _in_text(x, text):
    """The number x is written in the message (105 matches "105" or "105.0")."""
    nums = {float(v) for v in _NUM.findall(str(text or "").replace(",", ""))}
    try:
        return float(x) in nums
    except (TypeError, ValueError):
        return False


def _same(a, b):
    return all((float(a[k]) == float(b[k])) if k in ("strike_price", "price") else a[k] == b[k]
               for k in ("indices", "strike_price", "side", "price"))


def parse(text, llm=None, model=None, markers=("BTST", "Disclaimer"), ai_fallback_regex=True,
          thinking="low"):
    """{"is_signal": bool, "signal": {...} | None, "parsed_by": "ai" | "template" | "regex" | "",
    "reason": str, "ai": the model's answer or error}. Never raises."""
    out = {"is_signal": False, "signal": None, "parsed_by": "", "reason": "", "ai": None}
    if not has_markers(text, markers):
        out["reason"] = f"no {' + '.join(markers)} markers"
        return out
    try:
        tmpl, strict = template_read(text), strict_read(text)
    except Exception as e:                               # never: a bad pattern read is no read
        tmpl = strict = None
        out["reason"] = f"pattern read failed ({e!r}); "
    fallback = tmpl or strict

    def stand_in(why):
        """The model failed: the pattern read stands in, if allowed and there is one."""
        if ai_fallback_regex and fallback:
            out.update(is_signal=True, signal=dict(fallback), parsed_by="template" if tmpl else "regex",
                       reason=f"{why}; the {'template' if tmpl else 'strict'} read stands in")
        else:
            out["reason"] += why + ("" if fallback else " and the message does not read as a signal")
        return out

    ai, ai_err = None, None
    if llm is None:
        return stand_in("no model configured")
    try:
        ai, meta = llm.query_json("MESSAGE:\n" + str(text), schema=SCHEMA, system_instruction=SYSTEM,
                                  model_name=model, thinking_level=thinking)
        out["ai"] = {"answer": ai, "meta": {k: (meta or {}).get(k) for k in ("model", "elapsed_s", "cost_usd")}}
    except Exception as e:
        ai_err = f"{type(e).__name__}: {str(e)[:200]}"
        out["ai"] = {"error": ai_err}
        return stand_in(f"model unavailable ({ai_err})")
    if not isinstance(ai, dict) or not isinstance(ai.get("is_btst_signal"), bool):
        return stand_in(f"model's answer is not the JSON asked for ({str(ai)[:120]})")
    if not ai["is_btst_signal"]:
        if tmpl and ai_fallback_regex:
            out.update(is_signal=True, signal=dict(tmpl), parsed_by="template",
                       reason=f"model said not a signal ({str(ai.get('reason', ''))[:120]}), but the message is "
                              f"the channel's signal template - the template read stands in")
            return out
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
    if problems:
        return stand_in("model's signal unusable: " + "; ".join(problems))
    sig["strike_price"], sig["price"] = float(sig["strike_price"]), float(sig["price"])
    if fallback and not _same(sig, fallback):
        if tmpl and ai_fallback_regex:
            out.update(is_signal=True, signal=dict(tmpl), parsed_by="template",
                       reason=f"model and template disagree ({json.dumps(sig)} vs {json.dumps(tmpl)}) - "
                              f"the template read is used (its price is the number after 'Buy')")
            return out
        out["reason"] = (f"model's signal refused: model and strict read disagree: "
                         f"{json.dumps(sig)} vs {json.dumps(fallback)}")
        return out
    out.update(is_signal=True, signal=sig, parsed_by="ai", reason="model and checks agree")
    return out
