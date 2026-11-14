"""Ergebniszeilen: die gemeinsame Zeilenform aller Modi und Anbieter und die Parser für Gemini (parse_line für Batch und
Synchron, parse_interaction für den Hintergrund-Modus). Der Parser für OpenAI (parse_openai_line) liefert dieselbe Form."""
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


def as_dict(obj):
    """SDK-Objekt (pydantic) oder dict als dict. Ein dict ohne leere Felder ist unabhängiger von Klassen und Version."""
    if isinstance(obj, dict):
        return obj
    dump = getattr(obj, "model_dump", None)
    return dump(mode="json", exclude_none=True) if dump else {}


def interaction_text(d):
    """Antworttext einer Interaktion: der Text aller Schritte vom Typ model_output, in ihrer Reihenfolge. Bei Long Decode
    kommt mit jedem Teilschritt (Hop) ein weiterer Schritt dazu, output_text enthielte nur den letzten. Ohne Schritte
    (z. B. ältere SDK-Version) gilt output_text."""
    parts = []
    for step in d.get("steps") or []:
        if step.get("type") == "model_output":
            parts += [c.get("text", "") for c in step.get("content") or [] if c.get("type") == "text"]
    text = "".join(parts)
    if not text and not d.get("steps"):
        text = d.get("output_text") or ""
    return text


def interaction_errors(d):
    """Fehlermeldungen einer Interaktion als ein Text: Fehlerliste der Interaktion und Fehler einzelner Schritte."""
    found = list(d.get("errors") or []) + [s["error"] for s in d.get("steps") or [] if s.get("error")]
    texts = []
    for e in found:
        e = e if isinstance(e, dict) else {"message": e}
        text = str(e.get("message") or e.get("code") or "")
        if text and text not in texts:
            texts.append(text)
    return "; ".join(texts)


def parse_interaction(o, key, prompts):
    """Eine beendete Interaktion (Interactions API, background=true) in eine Tabellenzeile überführen, dieselbe Form wie
    parse_line(). Status completed gilt als STOP, incomplete (alle max_output_tokens verbraucht) als MAX_TOKENS, jeder
    andere Status erscheint in Großbuchstaben als Abschlussgrund. Die Token stehen in usage (bei Long Decode über alle
    Teilschritte: total_input_tokens zählt die Eingabe jedes Teilschritts erneut mit)."""
    d = as_dict(o)
    status = d.get("status") or "unbekannt"
    usage = d.get("usage") or {}
    text = interaction_text(d)
    finish = {"completed": "STOP", "incomplete": "MAX_TOKENS"}.get(status, status.upper())
    error = interaction_errors(d) or ("Die Interaktion ist fehlgeschlagen." if status == "failed" else "")
    return {
        "key": key,
        "prompt": prompts.get(key, ""),
        "text": text,
        "model": d.get("model") or "unbekannt",
        "finish": finish,
        "usage": usage,
        "error": error or (None if text else f"Keine Antwort (finishReason: {finish})"),
        "in": pick(usage, "total_input_tokens", "totalInputTokens"),
        "cached": pick(usage, "total_cached_tokens", "totalCachedTokens"),
        "think": pick(usage, "total_thought_tokens", "totalThoughtTokens"),
        "out": pick(usage, "total_output_tokens", "totalOutputTokens"),
    }
