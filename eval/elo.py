"""Pairwise ELO rating implementation for ranking model/agent outputs.

Standard ELO with K-factor; supports ties (split outcome 0.5/0.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field


DEFAULT_RATING = 1000.0
DEFAULT_K = 32.0


@dataclass
class EloTable:
    """Tracks ELO ratings for any number of named players (models/agents)."""

    ratings: dict[str, float] = field(default_factory=dict)
    games: dict[str, int] = field(default_factory=dict)
    k_factor: float = DEFAULT_K
    default_rating: float = DEFAULT_RATING

    def get(self, name: str) -> float:
        return self.ratings.get(name, self.default_rating)

    def _expected(self, ra: float, rb: float) -> float:
        return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))

    def update(self, a: str, b: str, score_a: float) -> tuple[float, float]:
        """Apply a single match result; score_a in {1.0, 0.5, 0.0} meaning A wins / tie / B wins.

        Returns (new_rating_a, new_rating_b).
        """
        if score_a not in (0.0, 0.5, 1.0):
            raise ValueError("score_a must be 0.0, 0.5, or 1.0")

        ra = self.get(a)
        rb = self.get(b)
        ea = self._expected(ra, rb)
        eb = 1.0 - ea
        new_ra = ra + self.k_factor * (score_a - ea)
        new_rb = rb + self.k_factor * ((1.0 - score_a) - eb)
        self.ratings[a] = new_ra
        self.ratings[b] = new_rb
        self.games[a] = self.games.get(a, 0) + 1
        self.games[b] = self.games.get(b, 0) + 1
        return new_ra, new_rb

    def leaderboard(self) -> list[tuple[str, float, int]]:
        """Return [(name, rating, games)] sorted by rating descending."""
        rows = [(name, rating, self.games.get(name, 0)) for name, rating in self.ratings.items()]
        rows.sort(key=lambda r: r[1], reverse=True)
        return rows


def winner_to_score(winner: str, a: str, b: str) -> float:
    """Convert a judge verdict (winner='A'|'B'|'tie' or a name) to ELO score for A."""
    w = (winner or "").strip()
    if w.lower() in ("tie", "draw", "equal"):
        return 0.5
    if w == "A" or w == a:
        return 1.0
    if w == "B" or w == b:
        return 0.0
    # Unrecognized → treat as tie to avoid biasing
    return 0.5
