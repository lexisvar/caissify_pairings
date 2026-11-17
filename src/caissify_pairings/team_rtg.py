"""
Random Tournament Generator (RTG) for team Swiss tournaments (C.04.6).

Simulates team tournaments board by board and writes them as TRF26
(see :mod:`caissify_pairings.team_trf`). Each round is paired by the
team engine (or any ``pairer`` with the same signature); game results
follow the FIDE expected-score formula, with draws at ``draw_rate``.

Variety on purpose, so the checker sees every rule at work:

* team forfeits (one team or both do not show up: TRF26 record 330),
  empty boards and boards played out of the default order (record 300),
* absent teams (zero-point byes, record 240) and withdrawals,
* Type A / Type B / no colour preferences, MP or GP as primary score,
  the secondary score used for colours or not, Baku Acceleration.

CLI::

    caissify-pairings-rtg --team --teams 20 --rounds 9 -n 100 -o out/
    caissify-pairings-rtg --team --teams 40 --rounds 11 --colours B --baku

Programmatic::

    from caissify_pairings.team_rtg import generate_team_tournament
    trf_text = generate_team_tournament(num_teams=20, num_rounds=9, seed=1)
"""

from __future__ import annotations

import math
import random
from typing import Callable, Dict, List, Optional

from caissify_pairings.engines.team_swiss import (
    DOUBLE_FORFEIT,
    FORFEIT_LOSS,
    FORFEIT_WIN,
    PAB,
    PLAYED,
    ZPB,
    TeamSwissEngine,
    baku_records,
    settings_from_kwargs,
)

Pairer = Callable[..., List[dict]]

_COUNTRIES = [
    "Arda", "Belor", "Calen", "Dorne", "Elara", "Fenwick", "Galdor", "Harrow",
    "Istra", "Jorvik", "Kestrel", "Lorien", "Marrow", "Norland", "Ostwick",
    "Penrose", "Quarry", "Rivendale", "Sable", "Tarn", "Umber", "Valen",
    "Wexford", "Yarrow", "Zephyr",
]


def _expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + math.pow(10, (rb - ra) / 400.0))


def _game(rng: random.Random, rw: float, rb: float, draw_rate: float) -> str:
    if rng.random() < draw_rate:
        return "="
    return "1" if rng.random() < _expected(rw, rb) else "0"


def _engine_pairer(teams, previous_pairings, round_number, total_rounds, **kwargs):
    return TeamSwissEngine(
        players=teams,
        previous_pairings=previous_pairings,
        round_number=round_number,
        total_rounds=total_rounds,
        **kwargs,
    ).generate_pairings()


def simulate_team_tournament(
    num_teams: int = 12,
    num_rounds: int = 7,
    boards: int = 4,
    reserves: int = 1,
    colour_preferences: str = "A",
    primary_score: str = "mp",
    use_secondary_for_colours: bool = True,
    accelerated: bool = False,
    initial_color: Optional[str] = None,
    forfeit_rate: float = 0.03,
    double_forfeit_rate: float = 0.005,
    board_forfeit_rate: float = 0.02,
    out_of_order_rate: float = 0.05,
    absence_rate: float = 0.02,
    withdrawal_rate: float = 0.01,
    draw_rate: float = 0.30,
    min_rating: int = 1400,
    max_rating: int = 2700,
    seed: Optional[int] = None,
    pairer: Optional[Pairer] = None,
    on_round: Optional[Callable[[int, List[dict], List[dict]], None]] = None,
    tournament_name: Optional[str] = None,
    **engine_options,
) -> Dict:
    """
    Simulate a whole team tournament and return its data (the input of
    :func:`caissify_pairings.team_trf.write_team_trf`).

    ``pairer(teams, previous_pairings, round_number, total_rounds, **opts)``
    pairs each round (default: the team engine). ``on_round(rnd, teams,
    rows)`` is called with the engine input and output of every round.
    """
    rng = random.Random(seed)
    if initial_color is None:
        initial_color = rng.choice(["white", "black"])
    settings = settings_from_kwargs(dict(
        engine_options,
        boards=boards,
        colour_preferences=colour_preferences,
        primary_score=primary_score,
        use_secondary_for_colours=use_secondary_for_colours,
        accelerated=accelerated,
        initial_color=initial_color,
    ))
    pair = pairer or _engine_pairer

    # --- Teams and players: TPN by strength (C.04.6 1.1.2: the rules decide) ---
    roster_size = boards + reserves
    raw_teams = []
    for i in range(num_teams):
        centre = rng.gauss((min_rating + max_rating) / 2, (max_rating - min_rating) / 5)
        ratings = sorted(
            (max(min_rating, min(max_rating, int(rng.gauss(centre, 90)))) for _ in range(roster_size)),
            reverse=True,
        )
        raw_teams.append(ratings)
    raw_teams.sort(key=lambda rs: -sum(rs[:boards]) / boards)

    teams: List[Dict] = []
    players: List[Dict] = []
    pid = 0
    for idx, ratings in enumerate(raw_teams, 1):
        base = _COUNTRIES[(idx - 1) % len(_COUNTRIES)]
        suffix = "" if idx <= len(_COUNTRIES) else f" {(idx - 1) // len(_COUNTRIES) + 1}"
        roster = []
        for k, r in enumerate(ratings, 1):
            pid += 1
            players.append({
                "starting_number": pid,
                "name": f"{base}{suffix.strip()}, Player {k}",
                "rating": r,
                "team": idx,
                "results": {},
                "score": 0.0,
            })
            roster.append(pid)
        teams.append({
            "id": idx,
            "tpn": idx,
            "name": f"{base}{suffix}",
            "nickname": (base[:3].upper() + str(idx))[:5],
            "strength": round(sum(ratings[:boards]) / boards),
            "players": roster,
            "history": [],
        })
    team_map = {t["id"]: t for t in teams}
    player_map = {p["starting_number"]: p for p in players}

    rounds: Dict[int, Dict] = {}
    withdrawn: set = set()
    previous_pairings: set = set()
    seq = settings.board_sequence
    W, D, L = settings.game_points_win, settings.game_points_draw, settings.game_points_loss

    def board_points(code: str) -> float:
        return {"1": W, "+": W, "=": D, "0": L, "-": 0.0}[code]

    for rnd in range(1, num_rounds + 1):
        info: Dict = {"pairs": [], "bye": None, "forfeits": [], "lineups": {}, "absent": [], "withdrawn": []}
        # Withdrawals (from round 3) and absences (from round 2), never more than a third of the field.
        if rnd >= 3:
            for t in teams:
                if t["id"] not in withdrawn and rng.random() < withdrawal_rate and len(withdrawn) < num_teams // 3:
                    withdrawn.add(t["id"])
        absent = set()
        if rnd >= 2:
            for t in teams:
                if t["id"] not in withdrawn and rng.random() < absence_rate:
                    absent.add(t["id"])
        info["absent"] = sorted(absent)
        info["withdrawn"] = sorted(withdrawn)

        engine_teams = []
        for t in teams:
            d = {k: v for k, v in t.items() if k != "history"}
            d["history"] = [dict(h) for h in t["history"]]
            d["withdrawn"] = t["id"] in withdrawn
            d["absent"] = t["id"] in absent
            engine_teams.append(d)
        opts = dict(engine_options)
        opts.update(
            boards=boards,
            colour_preferences=colour_preferences,
            primary_score=primary_score,
            use_secondary_for_colours=use_secondary_for_colours,
            accelerated=accelerated,
            initial_color=initial_color,
        )
        rows = pair(engine_teams, set(previous_pairings), rnd, num_rounds, **opts)
        if on_round is not None:
            on_round(rnd, engine_teams, rows)

        for tid in sorted(absent | withdrawn):
            if tid in absent:
                team_map[tid]["history"].append({"round": rnd, "opponent": None, "type": ZPB, "mp": 0.0, "gp": 0.0})

        for row in rows:
            w, b = row["white_id"], row.get("black_id")
            if b is None:
                info["bye"] = w
                team_map[w]["history"].append({
                    "round": rnd, "opponent": None, "type": PAB,
                    "mp": settings.pab_match_points, "gp": settings.pab_game_points,
                })
                continue
            info["pairs"].append((w, b))
            roll = rng.random()
            if roll < double_forfeit_rate:
                kind = "--"
            elif roll < double_forfeit_rate + forfeit_rate:
                kind = rng.choice(["+-", "-+"])
            else:
                kind = None
            if kind is not None:
                info["forfeits"].append((kind, w, b))
                for tid, opp, won in ((w, b, kind == "+-"), (b, w, kind == "-+")):
                    colour = "white" if tid == w else "black"
                    if kind == "--":
                        status, mp, gp = DOUBLE_FORFEIT, 0.0, 0.0
                    elif won:
                        status, mp, gp = FORFEIT_WIN, settings.match_points_win, boards * W
                        lineup = team_map[tid]["players"][:boards]
                        info["lineups"][tid] = list(lineup)
                        for p in lineup:
                            player_map[p]["results"][rnd] = {"opponent": None, "color": None, "result": "+"}
                            player_map[p]["score"] += W
                    else:
                        status, mp, gp = FORFEIT_LOSS, 0.0, 0.0
                    team_map[tid]["history"].append({
                        "round": rnd, "opponent": opp, "color": colour, "type": status, "mp": mp, "gp": gp,
                    })
                continue

            # Played match: line-ups (a reserve may come in; boards may stay empty).
            lineups = {}
            for tid in (w, b):
                roster = list(team_map[tid]["players"])
                if len(roster) > boards and rng.random() < 0.3:
                    roster.pop(rng.randrange(len(roster)))
                lineup = roster[:boards]
                if rng.random() < out_of_order_rate and boards > 1:
                    i, j = rng.sample(range(boards), 2)
                    lineup[i], lineup[j] = lineup[j], lineup[i]
                for k in range(boards):
                    if rng.random() < board_forfeit_rate:
                        lineup[k] = 0
                lineups[tid] = lineup
            # At least one board is really played, so the match counts as
            # played and its board-1 colour is recorded (TRF26 has no other
            # place for it).
            if not any(lineups[w][k] and lineups[b][k] for k in range(boards)):
                for tid in (w, b):
                    lineup = lineups[tid]
                    if not lineup[0]:
                        spare = [p for p in team_map[tid]["players"] if p not in lineup]
                        if spare:
                            lineup[0] = spare[0]
                        else:
                            k = next(k for k in range(boards) if lineup[k])
                            lineup[0], lineup[k] = lineup[k], 0
            info["lineups"][w] = lineups[w]
            info["lineups"][b] = lineups[b]
            gp = {w: 0.0, b: 0.0}
            for k in range(boards):
                pw, pb = lineups[w][k], lineups[b][k]
                # Board colours: the team with White on board 1 follows `seq`.
                if seq[k] == "W":
                    pwhite, pblack = pw, pb
                else:
                    pwhite, pblack = pb, pw
                if pwhite and pblack:
                    res = _game(rng, player_map[pwhite]["rating"], player_map[pblack]["rating"], draw_rate)
                    mirror = {"1": "0", "0": "1", "=": "="}[res]
                    player_map[pwhite]["results"][rnd] = {"opponent": pblack, "color": "w", "result": res}
                    player_map[pblack]["results"][rnd] = {"opponent": pwhite, "color": "b", "result": mirror}
                    for p, code in ((pwhite, res), (pblack, mirror)):
                        player_map[p]["score"] += board_points(code)
                        gp[player_map[p]["team"]] += board_points(code)
                elif pwhite or pblack:
                    present = pwhite or pblack
                    player_map[present]["results"][rnd] = {"opponent": None, "color": None, "result": "+"}
                    player_map[present]["score"] += W
                    gp[player_map[present]["team"]] += W
            if gp[w] > gp[b]:
                mps = {w: settings.match_points_win, b: settings.match_points_loss}
            elif gp[w] < gp[b]:
                mps = {w: settings.match_points_loss, b: settings.match_points_win}
            else:
                mps = {w: settings.match_points_draw, b: settings.match_points_draw}
            for tid, opp, colour in ((w, b, "white"), (b, w, "black")):
                team_map[tid]["history"].append({
                    "round": rnd, "opponent": opp, "color": colour, "type": PLAYED, "mp": mps[tid], "gp": gp[tid],
                })
            previous_pairings.add((min(w, b), max(w, b)))
        rounds[rnd] = info

    for t in teams:
        t["match_points"] = sum(h["mp"] for h in t["history"])
        t["game_points"] = sum(h["gp"] for h in t["history"])
        if t["id"] in withdrawn:
            t["withdrawn"] = True

    tournament = {
        "name": tournament_name or f"RTG team {num_teams}t {num_rounds}r",
        "total_rounds": num_rounds,
        "boards": boards,
        "initial_color": initial_color,
        "settings": {
            "primary_score": primary_score,
            "use_secondary_for_colours": use_secondary_for_colours,
            "colour_preferences": colour_preferences,
            "accelerated": accelerated,
            "initial_color": initial_color,
            "boards": boards,
            "board_colours": settings.board_colours,
            "match_points_win": settings.match_points_win,
            "match_points_draw": settings.match_points_draw,
            "match_points_loss": settings.match_points_loss,
            "game_points_win": W,
            "game_points_draw": D,
            "game_points_loss": L,
            "bye_match_points": settings.pab_match_points,
            "bye_game_points": settings.pab_game_points,
        },
        "teams": teams,
        "players": players,
        "rounds": rounds,
    }
    if accelerated:
        tournament["acceleration_records"] = baku_records(num_rounds, num_teams, settings.match_points_win)
    return tournament


def generate_team_tournament(
    num_teams: int = 12,
    num_rounds: int = 7,
    seed: Optional[int] = None,
    **kwargs,
) -> str:
    """Simulate a team tournament and return it as TRF26 text."""
    from caissify_pairings.team_trf import write_team_trf

    return write_team_trf(simulate_team_tournament(num_teams=num_teams, num_rounds=num_rounds, seed=seed, **kwargs))


def random_settings(rng: random.Random) -> Dict:
    """A random mix of competition rules (used by tests and ``--team --mix``)."""
    primary = rng.choice(["mp", "mp", "gp"])
    return {
        "colour_preferences": rng.choice(["A", "A", "B", "none"]),
        "primary_score": primary,
        "use_secondary_for_colours": rng.random() < 0.8,
        "accelerated": primary == "mp" and rng.random() < 0.25,
    }


def main(argv: Optional[List[str]] = None) -> int:
    """``caissify-pairings-rtg --team [options]``: write TRF26 team tournaments."""
    import argparse
    import logging
    import os
    import sys

    parser = argparse.ArgumentParser(
        prog="caissify-pairings-rtg --team",
        description="Random Tournament Generator for team Swiss tournaments (C.04.6, TRF26)",
    )
    parser.add_argument("--team", action="store_true", help="(implied) team mode")
    parser.add_argument("-n", "--count", type=int, default=1, help="number of tournaments (default 1)")
    parser.add_argument("-t", "--teams", type=int, default=12, help="teams per tournament (default 12)")
    parser.add_argument("-r", "--rounds", type=int, default=7, help="rounds (default 7)")
    parser.add_argument("-b", "--boards", type=int, default=4, help="boards per match (default 4)")
    parser.add_argument("--reserves", type=int, default=1, help="reserve players per team (default 1)")
    parser.add_argument("--colours", "--colors", dest="colours", choices=["A", "B", "none"], default="A",
                        help="colour preferences: Type A (default), Type B or none")
    parser.add_argument("--primary", choices=["mp", "gp"], default="mp", help="primary score (default mp)")
    parser.add_argument("--no-secondary", action="store_true",
                        help="do not use the secondary score to choose the first-team (4.2.2)")
    parser.add_argument("--baku", action="store_true", help="Baku Acceleration (C.04.7; MP primary only)")
    parser.add_argument("--mix", action="store_true",
                        help="random rules per tournament (colour type, primary score, Baku)")
    parser.add_argument("--forfeit-rate", type=float, default=0.03)
    parser.add_argument("--board-forfeit-rate", type=float, default=0.02)
    parser.add_argument("--absence-rate", type=float, default=0.02)
    parser.add_argument("--withdrawal-rate", type=float, default=0.01)
    parser.add_argument("--draw-rate", type=float, default=0.30)
    parser.add_argument("--seed", type=int, default=None, help="random seed for reproducibility")
    parser.add_argument("-o", "--output-dir", type=str, default=None, help="output directory (default: stdout)")
    args = parser.parse_args(argv)

    logging.getLogger("caissify_pairings").setLevel(logging.ERROR)
    master = random.Random(args.seed)
    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
    for i in range(args.count):
        seed = master.randrange(2 ** 31)
        rules = {
            "colour_preferences": args.colours,
            "primary_score": args.primary,
            "use_secondary_for_colours": not args.no_secondary,
            "accelerated": args.baku,
        }
        if args.mix:
            rules = random_settings(random.Random(seed))
        name = f"RTG-TEAM-{i + 1:05d} {args.teams}t {args.rounds}r"
        trf = generate_team_tournament(
            num_teams=args.teams,
            num_rounds=args.rounds,
            seed=seed,
            boards=args.boards,
            reserves=args.reserves,
            forfeit_rate=args.forfeit_rate,
            board_forfeit_rate=args.board_forfeit_rate,
            absence_rate=args.absence_rate,
            withdrawal_rate=args.withdrawal_rate,
            draw_rate=args.draw_rate,
            tournament_name=name,
            **rules,
        )
        if args.output_dir:
            path = os.path.join(args.output_dir, f"rtg_team_{i + 1:05d}.trf")
            with open(path, "w", encoding="utf-8") as f:
                f.write(trf)
        else:
            sys.stdout.write(trf)
            if args.count > 1:
                sys.stdout.write("\n")
    if args.output_dir:
        print(f"Done. {args.count} TRF26 files written to {args.output_dir}", file=sys.stderr)
    return 0
