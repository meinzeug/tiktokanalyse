"""Gemeinsame Zielzeiträume für Anzeige, CLI und dauerhaftes Lernen."""

HOUR = 3600
DAY = 24 * HOUR
YEAR = 365.25 * DAY

# Modelljahre haben 365,25 Tage, Monate hier 30 Tage.
HORIZON_RANGES = {
    "48h": ("48 Stunden", tuple(hours * HOUR for hours in (1, 2, 6, 12, 24, 48))),
    "4w": ("4 Wochen", tuple(days * DAY for days in (1, 2, 7, 14, 21, 28))),
    "1y": ("1 Jahr", (30 * DAY, 60 * DAY, 90 * DAY, 180 * DAY, 270 * DAY, int(YEAR))),
    "10y": ("10 Jahre", tuple(int(years * YEAR) for years in (1, 2, 3, 5, 7, 10))),
}
DISPLAY_HORIZONS = tuple(sorted({seconds for _, horizons in HORIZON_RANGES.values() for seconds in horizons}))
VIDEO_HORIZONS = tuple(sorted({300, 900, *DISPLAY_HORIZONS}))
CHANNEL_HORIZONS = DISPLAY_HORIZONS


def horizon_label(seconds: int, *, hours_only=False) -> str:
    if seconds >= YEAR:
        return f"{seconds / YEAR:g} J."
    if seconds >= 86400 and not hours_only:
        return f"{seconds / DAY:g} d"
    if seconds >= HOUR:
        return f"{seconds / HOUR:g} h"
    return f"{seconds / 60:g} min"


def add_horizon_argument(parser):
    parser.add_argument("--horizon", choices=tuple(HORIZON_RANGES), default="4w",
                        help="Zeitraum: 48h, 4w (Standard), 1y oder 10y; in der TUI mit -/+ wechseln")
