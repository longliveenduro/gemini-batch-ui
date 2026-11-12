"""Gesprächsverlauf als Text (früherer Prompt und deine Antwort dazu), zum Fortsetzen eines Gesprächs."""
import re

from .config import TALK_BEGIN, TALK_END, TALK_INTRO, TALK_MARK, TALK_MINE, TALK_NEW, TALK_YOURS


def entry_group(j):
    """Prompts pro Gespräch eines Eintrags (Batch: 1). Ältere synchrone Einträge kennen nur „chat“ (alles ein Gespräch)."""
    if j.get("mode") != "sync":
        return 1
    return max(int(j.get("group") or (len(j["prompts"]) if j.get("chat") else 1)), 1)


def talk_safe(text):
    """Text für den Gesprächsverlauf vorbereiten. Eine Zeile nur aus --- würde im Formular die Prompts trennen (daraus
    wird ***, in Markdown dieselbe Trennlinie), und Zeilen, die wie eine Marke aussehen, bekommen einen Rückstrich.
    Ein zweites Anwenden ändert nichts mehr."""
    text = re.sub(r"^[^\S\n]*---[^\S\n]*$", "***", text.strip(), flags=re.M)
    return TALK_MARK.sub(lambda m: "\\" + m.group(0).strip(), text)


def conversation_text(turns, new_prompt=""):
    """Gesprächsverlauf als Text, der einem neuen Prompt vorangestellt wird: je Runde mein früherer Prompt und deine
    Antwort dazu (turns = [(Prompt, Antwort), …]), darunter Platz für den neuen Prompt."""
    lines = [TALK_INTRO, "", TALK_BEGIN]
    for n, (prompt, answer) in enumerate(turns, 1):
        lines += ["", TALK_MINE.format(n=n), talk_safe(prompt), "", TALK_YOURS.format(n=n), talk_safe(answer)]
    return "\n".join(lines + ["", TALK_END, "", TALK_NEW, new_prompt])


def parse_conversation(text):
    """Gegenstück zu conversation_text(): ([(Prompt, Antwort), …], neuer Prompt), wenn text einen Gesprächsverlauf
    enthält, sonst ([], text). Ist der Aufbau durcheinander (z. B. von Hand bearbeitet), gilt text als gewöhnlicher Prompt."""
    after = {"begin": ("mine",), "mine": ("yours",), "yours": ("mine", "end"), "end": ("new",)}
    turns, prompt, state, pos, end = [], "", None, 0, None
    for m in TALK_MARK.finditer(text):
        kind, chunk = m.lastgroup, text[pos:m.start()].strip()
        if state is None:
            if kind != "begin":  # vor dem Beginn steht nur die Einleitung
                continue
        elif kind not in after.get(state, ()):
            return [], text
        if state == "mine":
            prompt = chunk
        elif state == "yours":
            turns.append((prompt, chunk))
        state, pos = kind, m.end()
        if kind == "end":
            end = pos
        elif kind == "new":
            return turns, text[pos:].strip()
    if turns and end is not None:  # Marke für den neuen Prompt gelöscht: der Rest zählt als neuer Prompt
        return turns, text[end:].strip()
    return [], text


def history_turns(j, rows, key):
    """Gesprächsverlauf bis einschließlich der Antwort zu key als [(Prompt, Antwort), …], oder None, wenn er sich nicht
    bilden lässt (eine Antwort fehlt). Bei synchronen Gesprächen gehören die früheren Runden desselben Gesprächs dazu.
    Beginnt der erste Prompt selbst mit einem Verlauf (frühere Fortsetzung), wird dieser davor eingereiht statt
    verschachtelt: Bei jeder Fortsetzung wächst der Verlauf um genau eine Runde."""
    keys, group = list(j["prompts"]), entry_group(j)
    if key not in keys:
        return None
    last = keys.index(key)
    done, turns = {r["key"]: r for r in rows}, []
    for k in keys[last // group * group:last + 1]:
        r = done.get(k)
        if not r or not r.get("text"):
            return None
        turns.append((r.get("prompt") or j["prompts"].get(k, ""), r["text"]))
    earlier, own = parse_conversation(turns[0][0])
    if earlier:
        turns[0] = (own, turns[0][1])
    return earlier + turns


def short_prompt(prompt, n=90):
    """Anfang eines Prompts für Überschriften. Setzt er ein früheres Gespräch fort, zählt der neue Prompt (mit ↪)."""
    turns, own = parse_conversation(prompt)
    return "↪ " + own[:n] if turns else prompt[:n]
