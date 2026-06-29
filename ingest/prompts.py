from __future__ import annotations


def text(message: str, *, default: str = "") -> str:
    questionary = _questionary()
    answer = questionary.text(message, default=default).ask()
    return str(answer or "").strip()


def password(message: str) -> str:
    questionary = _questionary()
    answer = questionary.password(message).ask()
    return str(answer or "")


def confirm(message: str, *, default: bool = True) -> bool:
    questionary = _questionary()
    return bool(questionary.confirm(message, default=default).ask())


def _questionary() -> object:
    try:
        import questionary
    except ImportError as exc:
        raise SystemExit("Missing dependency: questionary. Install project dependencies, then rerun auth command.") from exc
    return questionary
