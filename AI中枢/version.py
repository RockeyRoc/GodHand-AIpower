"""Single source of truth for the GodHand product version."""

VERSION = "1.5.0"


def numeric(parts: int = 4) -> tuple[int, ...]:
    """Version tuple for Windows EXE resources (pads with zeros)."""
    values = [int(piece) for piece in VERSION.split(".") if piece.isdigit()]
    return tuple((values + [0] * parts)[:parts])
