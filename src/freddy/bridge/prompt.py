from typing import Any

MAX_HISTORY_MESSAGES = 30
MAX_CHARS = 600


def _text(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        ]
        joined = "\n".join(p for p in parts if p)
        return joined or None
    return None


def build_bridge_prompt(history: list[dict[str, Any]], user_content: str) -> str:
    lines: list[str] = []
    for msg in history[-MAX_HISTORY_MESSAGES:]:
        text = _text(msg["content"])
        if not text:
            continue
        who = "Me" if msg["role"] == "user" else "Freddy"
        text = text.strip()
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + "..."
        lines.append(f"{who}: {text}")
    if not lines:
        return user_content
    transcript = "\n".join(lines)
    return f"Earlier today:\n{transcript}\n\nNew message:\n{user_content}"
