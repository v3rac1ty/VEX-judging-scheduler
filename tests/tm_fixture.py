"""Synthetic Tournament Manager match lists for tests."""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from typing import Any

TEAMS = [f"{1000 + i}{'AB'[i % 2]}" for i in range(24)]


def make_matches(
    first_match: datetime,
    teams: list[str] | None = None,
    rounds: int = 6,
    cycle_minutes: int = 5,
    seed: int = 7,
) -> list[dict[str, Any]]:
    """Qualification matches: 2 alliances x 2 teams, every team plays once per round."""
    teams = list(teams or TEAMS)
    rng = random.Random(seed)
    matches: list[dict[str, Any]] = []
    number = 1
    for _ in range(rounds):
        order = teams[:]
        rng.shuffle(order)
        for i in range(0, len(order) - 3, 4):
            red, blue = order[i : i + 2], order[i + 2 : i + 4]
            scheduled = first_match + timedelta(minutes=(number - 1) * cycle_minutes)
            matches.append(
                {
                    "matchInfo": {
                        "timeScheduled": int(scheduled.timestamp()),
                        "matchTuple": {"round": "QUAL", "match": number},
                        "alliances": [
                            {"teams": [{"number": t} for t in red]},
                            {"teams": [{"number": t} for t in blue]},
                        ],
                    }
                }
            )
            number += 1
    return matches


def as_log(matches: list[dict[str, Any]]) -> str:
    """Matches wrapped the way the TM log tool prints them."""
    return (
        "[INFO] connecting to tournament manager\n"
        "[INFO] auth ok [200]\n"
        ">> Fetched matches:\n"
        + json.dumps(matches, indent=2)
        + "\n[INFO] done\n"
    )
