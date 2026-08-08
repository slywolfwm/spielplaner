PRIORITY_LEVELS = ("Hoch", "Mittel", "Niedrig")
DEFAULT_PAIR_PRIORITY = PRIORITY_LEVELS[0]


def normalize_priority(value: object) -> str:
    priority = str(value or DEFAULT_PAIR_PRIORITY)
    return priority if priority in PRIORITY_LEVELS else DEFAULT_PAIR_PRIORITY
