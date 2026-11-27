"""Reply language for chat actions, including language-neutral date choices."""


def korean(message):
    language = getattr(message, "reply_language", None)
    if language in ("en", "ko"):
        return language == "ko"
    return any("가" <= ch <= "힣" for ch in message.text)
