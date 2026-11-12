"""Ergebniszeilen: die gemeinsame Zeilenform aller Modi und Anbieter und der Parser für Gemini. Der Parser für OpenAI
(parse_openai_line) liefert dieselbe Form."""
import json


def pick(d, *names, default=0):
    for n in names:
        if d.get(n) is not None:
            return d[n]
    return default


def parse_line(o, prompts):
    """Eine Ergebniszeile (Batch-JSONL oder umgewandelte synchrone Antwort) in eine Tabellenzeile überführen."""
    resp = o.get("response") or {}
    usage = pick(resp, "usageMetadata", "usage_metadata", default={})
    cand = (resp.get("candidates") or [{}])[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    reason = pick(cand, "finishReason", "finish_reason", default="unbekannt")
    return {
        "key": o.get("key", "?"),
        "prompt": prompts.get(o.get("key"), ""),
        "text": text,
        "model": pick(resp, "modelVersion", "model_version", default="unbekannt"),
        "finish": reason,
        "usage": usage,
        "error": o.get("error") or (None if text else f"Keine Antwort (finishReason: {reason})"),
        "in": pick(usage, "promptTokenCount", "prompt_token_count"),
        "cached": pick(usage, "cachedContentTokenCount", "cached_content_token_count",
                       "totalCachedTokens", "total_cached_tokens"),
        "think": pick(usage, "thoughtsTokenCount", "thoughts_token_count"),
        "out": pick(usage, "candidatesTokenCount", "candidates_token_count"),
    }


def parse_results(raw, prompts, parse=parse_line):
    rows = [parse(json.loads(line), prompts) for line in raw.decode("utf-8").splitlines() if line.strip()]
    num = lambda r: int(r["key"].rsplit("-", 1)[-1]) if r["key"].rsplit("-", 1)[-1].isdigit() else 0
    return sorted(rows, key=num)
