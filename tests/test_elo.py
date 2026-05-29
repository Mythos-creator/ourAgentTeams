"""Tests for the ELO rating table."""

from __future__ import annotations

import pytest

from eval.elo import EloTable, winner_to_score


def test_default_rating():
    elo = EloTable()
    assert elo.get("nobody") == 1000.0


def test_winner_gains_loser_loses():
    elo = EloTable(k_factor=32.0)
    new_a, new_b = elo.update("A", "B", 1.0)
    assert new_a > 1000.0
    assert new_b < 1000.0
    # zero-sum within rounding
    assert (new_a - 1000.0) == pytest.approx(1000.0 - new_b, abs=1e-6)


def test_tie_keeps_equal_ratings():
    elo = EloTable()
    new_a, new_b = elo.update("A", "B", 0.5)
    assert new_a == pytest.approx(1000.0)
    assert new_b == pytest.approx(1000.0)


def test_underdog_wins_more_points():
    elo = EloTable()
    elo.ratings["champ"] = 1400.0
    elo.ratings["challenger"] = 1000.0
    before = elo.get("challenger")
    new_chal, _ = elo.update("challenger", "champ", 1.0)
    delta = new_chal - before
    # Beating a strong champ should swing more than ~16 points (half K)
    assert delta > 16.0


def test_games_counted():
    elo = EloTable()
    elo.update("A", "B", 1.0)
    elo.update("A", "C", 0.5)
    assert elo.games["A"] == 2
    assert elo.games["B"] == 1
    assert elo.games["C"] == 1


def test_invalid_score_raises():
    elo = EloTable()
    with pytest.raises(ValueError):
        elo.update("A", "B", 0.7)


def test_leaderboard_sorted_desc():
    elo = EloTable()
    elo.update("A", "B", 1.0)
    elo.update("A", "C", 1.0)
    board = elo.leaderboard()
    assert board[0][0] == "A"
    assert board[0][1] >= board[1][1] >= board[2][1]


def test_winner_to_score_conversions():
    assert winner_to_score("A", "alpha", "beta") == 1.0
    assert winner_to_score("B", "alpha", "beta") == 0.0
    assert winner_to_score("tie", "alpha", "beta") == 0.5
    assert winner_to_score("alpha", "alpha", "beta") == 1.0
    assert winner_to_score("beta", "alpha", "beta") == 0.0
    # unrecognized → tie
    assert winner_to_score("???", "alpha", "beta") == 0.5
