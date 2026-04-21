"""
Random Tournament Generator (RTG) — FIDE C.04.A §A.5

Generates simulated Swiss tournaments with realistic probability-based
results, producing a full TRF16 file for each tournament.

Game results respect the probabilities given by the FIDE rating table:
    P(higher-rated wins) = 1 / (1 + 10**((Rb - Ra) / 400))

CLI usage::

    caissify-pairings-rtg --players 20 --rounds 9
    caissify-pairings-rtg -n 5000 -p 20 -r 9 -o output_dir/

Programmatic usage::

    from caissify_pairings.rtg import generate_tournament, generate_tournaments
    trf_text = generate_tournament(num_players=20, num_rounds=9)
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
from typing import Dict, List, Optional, Set, Tuple

from caissify_pairings.engines.dutch import DutchEngine
from caissify_pairings.trf import TRFWriter


# ---------------------------------------------------------------------------
# FIDE expected-score formula
# ---------------------------------------------------------------------------

def expected_score(rating_a: int, rating_b: int) -> float:
    """
    FIDE expected score for player A against player B.

    Returns P(A wins or draws favourably) in [0, 1].
    """
    return 1.0 / (1.0 + math.pow(10, (rating_b - rating_a) / 400.0))


def simulate_result(rating_white: int, rating_black: int, draw_rate: float = 0.30) -> str:
    """
    Simulate a game result using FIDE probability table.

    Returns "1" (white wins), "0" (black wins), or "=" (draw).
    ``draw_rate`` is the probability of a drawn game (default 30%).
    """
    rng = random.random()

    # Draw band centred around expected score
    if rng < draw_rate:
        return "="

    # Decide winner using FIDE expected score
    p_white = expected_score(rating_white, rating_black)
    if random.random() < p_white:
        return "1"
    return "0"


# ---------------------------------------------------------------------------
# Name / rating generators
# ---------------------------------------------------------------------------

_FIRST_NAMES = [
    "Alexander", "Boris", "Carlos", "Dmitry", "Einar", "Fabiano",
    "Gata", "Hikaru", "Ivan", "Jan", "Koneru", "Levon", "Magnus",
    "Nikita", "Olga", "Pavel", "Radoslaw", "Sergei", "Teimour",
    "Viswanathan", "Wesley", "Yifan", "Zurab", "Anish", "Baadur",
    "Daniel", "Ernesto", "Francisco", "Gukesh", "Hans", "Igor",
    "Judit", "Kateryna", "Leinier", "Maxime", "Nodirbek", "Oscar",
    "Peter", "Quang", "Richard", "Samuel", "Tigran", "Ursula",
    "Vladimir", "Wang", "Xiang", "Yuri", "Zhansaya",
]

_LAST_NAMES = [
    "Alekhine", "Botvinnik", "Carlsen", "Dominguez", "Erigaisi",
    "Fischer", "Grischuk", "Hou", "Ivanchuk", "Jobava", "Kramnik",
    "Liren", "Mamedyarov", "Nakamura", "Ojeda", "Ponomariov",
    "Quesada", "Rapport", "Svidler", "Topalov", "Ushenina",
    "Vachier-Lagrave", "Wei", "Xiong", "Yanez", "Zhao",
    "Abdusattorov", "Bhatt", "Caruana", "Duda", "Eljanov",
    "Firouzja", "Giri", "Harikrishna", "Inarkiev", "Jones",
    "Karjakin", "Le", "Morozevich", "Nepomniachtchi", "Oparin",
    "Praggnanandhaa", "Radjabov", "Shankland", "Tari", "Vallejo",
]


def _random_name(idx: int) -> str:
    first = random.choice(_FIRST_NAMES)
    last = random.choice(_LAST_NAMES)
    return f"{last},{first}"


def _random_rating(min_rating: int = 1400, max_rating: int = 2700) -> int:
    """Generate a random rating with a bell-curve centred around 2000."""
    mean = (min_rating + max_rating) / 2
    std = (max_rating - min_rating) / 4
    r = int(random.gauss(mean, std))
    return max(min_rating, min(max_rating, r))


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------

def generate_tournament(
    num_players: int = 20,
    num_rounds: int = 9,
    min_rating: int = 1400,
    max_rating: int = 2700,
    draw_rate: float = 0.30,
    seed: Optional[int] = None,
    tournament_name: Optional[str] = None,
    accelerated: bool = False,
) -> str:
    """
    Generate a complete simulated tournament and return TRF16 text.

    The engine pairs each round using the embedded Dutch engine, then
    results are simulated using the FIDE probability table.

    Set ``accelerated=True`` to enable Baku Acceleration (FIDE
    C.04.5.1) for rounds 1-2 — useful for cross-validating accelerated
    pairings against external oracles such as ``bbpPairings``.
    """
    if seed is not None:
        random.seed(seed)

    # --- Build initial player list ---
    players: List[Dict] = []
    for i in range(1, num_players + 1):
        players.append({
            "id": i,
            "starting_number": i,
            "pairing_number": i,
            "name": _random_name(i),
            "rating": _random_rating(min_rating, max_rating),
            "title": "",
            "score": 0.0,
            "color_hist": [],
            "float_history": [],
            "bye_count": 0,
            "forfeit_win_count": 0,
        })

    # Sort by rating desc to assign pairing numbers
    players.sort(key=lambda p: (-p["rating"], p["starting_number"]))
    for idx, p in enumerate(players, 1):
        p["pairing_number"] = idx
        p["starting_number"] = idx
        p["id"] = idx

    # C.04.3 §E: initial-colour determined by lot before round 1
    initial_color = random.choice(["white", "black"])

    previous_pairings: Set[Tuple[int, int]] = set()

    # Per-player tracking for TRF output
    # results[starting_number][round] = { opponent, color, result }
    all_results: Dict[int, Dict[int, Dict]] = {p["id"]: {} for p in players}

    for rnd in range(1, num_rounds + 1):
        # Capture pre-round scores for float direction computation
        pre_scores: Dict[int, float] = {p["id"]: p["score"] for p in players}

        # --- Pair ---
        engine_players = _snapshot_players(players)
        engine = DutchEngine(
            players=engine_players,
            previous_pairings=previous_pairings,
            round_number=rnd,
            total_rounds=num_rounds,
            initial_color=initial_color,
            accelerated=accelerated,
        )
        pairings = engine.generate_pairings()

        # --- Simulate results ---
        for pairing in pairings:
            w_id = pairing["white_id"]
            b_id = pairing.get("black_id")

            if b_id is None or pairing.get("bye"):
                # Bye — full point; counts as downfloat per A.4.b
                all_results[w_id][rnd] = {
                    "opponent": None,
                    "color": None,
                    "result": "U",
                }
                _update_player(players, w_id, score_delta=1.0, bye=True)
                _find(players, w_id)["float_history"].append("down")
                continue

            w_player = _find(players, w_id)
            b_player = _find(players, b_id)
            result = simulate_result(w_player["rating"], b_player["rating"], draw_rate)

            # Record for white
            all_results[w_id][rnd] = {
                "opponent": b_id,
                "color": "w",
                "result": result,
            }
            # Record for black (mirror)
            b_result = {"1": "0", "0": "1", "=": "="}[result]
            all_results[b_id][rnd] = {
                "opponent": w_id,
                "color": "b",
                "result": b_result,
            }

            # Update scores
            if result == "1":
                _update_player(players, w_id, score_delta=1.0, color="white")
                _update_player(players, b_id, score_delta=0.0, color="black")
            elif result == "0":
                _update_player(players, w_id, score_delta=0.0, color="white")
                _update_player(players, b_id, score_delta=1.0, color="black")
            else:
                _update_player(players, w_id, score_delta=0.5, color="white")
                _update_player(players, b_id, score_delta=0.5, color="black")

            # Track opponents
            previous_pairings.add((min(w_id, b_id), max(w_id, b_id)))

            # Compute float direction from pre-round scores
            w_pre = pre_scores[w_id]
            b_pre = pre_scores[b_id]
            if w_pre > b_pre:
                w_player["float_history"].append("down")
                b_player["float_history"].append("up")
            elif w_pre < b_pre:
                w_player["float_history"].append("up")
                b_player["float_history"].append("down")
            else:
                w_player["float_history"].append("none")
                b_player["float_history"].append("none")

    # --- Build TRF output ---
    trf_players = []
    for p in sorted(players, key=lambda x: x["starting_number"]):
        trf_players.append({
            "starting_number": p["starting_number"],
            "name": p["name"],
            "rating": p["rating"],
            "title": p.get("title", ""),
            "federation": "",
            "fide_id": None,
            "birth": "",
            "sex": "",
            "score": p["score"],
            "rank": p["starting_number"],
            "results": all_results[p["id"]],
        })

    tournament_meta = {
        "name": tournament_name or f"RTG {num_players}p {num_rounds}r",
        "system": "Individual: Swiss-System",
        "total_rounds": num_rounds,
    }

    return TRFWriter(tournament_meta, trf_players, num_rounds).write()


def generate_tournaments(
    count: int = 5000,
    num_players: int = 20,
    num_rounds: int = 9,
    **kwargs,
) -> List[str]:
    """Generate *count* random tournaments, returning a list of TRF strings."""
    trfs: List[str] = []
    for i in range(count):
        name = f"RTG-{i+1:05d} {num_players}p {num_rounds}r"
        trf = generate_tournament(
            num_players=num_players,
            num_rounds=num_rounds,
            seed=None,  # Different each time
            tournament_name=name,
            **kwargs,
        )
        trfs.append(trf)
    return trfs


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _snapshot_players(players: List[Dict]) -> List[Dict]:
    """Deep-copy player state for engine input."""
    return [
        {
            "id": p["id"],
            "name": p["name"],
            "score": p["score"],
            "rating": p["rating"],
            "starting_number": p["starting_number"],
            "pairing_number": p["pairing_number"],
            "title": p.get("title", ""),
            "color_hist": list(p["color_hist"]),
            "float_history": list(p["float_history"]),
            "bye_count": p["bye_count"],
            "forfeit_win_count": p.get("forfeit_win_count", 0),
        }
        for p in players
    ]


def _find(players: List[Dict], pid: int) -> Dict:
    for p in players:
        if p["id"] == pid:
            return p
    raise ValueError(f"Player {pid} not found")


def _update_player(
    players: List[Dict],
    pid: int,
    score_delta: float,
    color: Optional[str] = None,
    bye: bool = False,
):
    p = _find(players, pid)
    p["score"] += score_delta
    if color:
        p["color_hist"].append(color)
    if bye:
        p["bye_count"] += 1


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Random Tournament Generator (RTG) for FIDE endorsement"
    )
    parser.add_argument("-n", "--count", type=int, default=1,
                        help="Number of tournaments to generate (default: 1)")
    parser.add_argument("-p", "--players", type=int, default=20,
                        help="Number of players per tournament (default: 20)")
    parser.add_argument("-r", "--rounds", type=int, default=9,
                        help="Number of rounds per tournament (default: 9)")
    parser.add_argument("-o", "--output-dir", type=str, default=None,
                        help="Output directory (default: stdout)")
    parser.add_argument("--min-rating", type=int, default=1400)
    parser.add_argument("--max-rating", type=int, default=2700)
    parser.add_argument("--draw-rate", type=float, default=0.30)
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility")
    parser.add_argument("--accelerated", action="store_true",
                        help="Use Baku Acceleration (FIDE C.04.5.1) "
                             "for rounds 1-2")

    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)

    for i in range(args.count):
        name = f"RTG-{i+1:05d} {args.players}p {args.rounds}r"
        trf = generate_tournament(
            num_players=args.players,
            num_rounds=args.rounds,
            min_rating=args.min_rating,
            max_rating=args.max_rating,
            draw_rate=args.draw_rate,
            tournament_name=name,
            accelerated=args.accelerated,
        )

        if args.output_dir:
            filename = f"rtg_{i+1:05d}.trf"
            filepath = os.path.join(args.output_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(trf)
            if (i + 1) % 100 == 0 or i == 0:
                print(f"Generated {i+1}/{args.count}: {filepath}", file=sys.stderr)
        else:
            if args.count > 1:
                print(f"=== Tournament {i+1} ===")
            print(trf)
            if args.count > 1:
                print()

    if args.output_dir:
        print(f"Done. {args.count} TRF files written to {args.output_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
