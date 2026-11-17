"""
TRF26 team records, the team checker (FPC) and the team RTG.

* A hand-written TRF26 file exercises every team record the reader
  supports (142, 152, 162, 192, 240, 260, 300, 310, 320, 330, 352, 362)
  and the rebuilt matches are checked by hand.
* RTG -> TRF26 -> parser round-trips are exact, and the engine's own
  tournaments always pass its checker (many random tournaments with
  every rule variant).
* CLI entry points.
"""

from __future__ import annotations

import io
import json
import logging
import random
import sys
from pathlib import Path

import pytest

from caissify_pairings.team_fpc import check_team_trf
from caissify_pairings.team_rtg import generate_team_tournament, random_settings, simulate_team_tournament
from caissify_pairings.team_trf import (
    engine_input,
    infer_initial_colour,
    is_team_trf,
    parse_team_trf,
    parse_type_code,
    settings_from_trf,
    team_history,
    team_rounds,
    type_code,
    write_team_trf,
)
from caissify_pairings.trf import TRFParser, format_player_line

FIXTURES = Path(__file__).parent / "fixtures"


def _team_310(tpn, name, nick, strength, mp, gp, rank, players):
    line = f"310 {tpn:>3} {name:<32} {nick:<5} {strength:>6} {mp:>6.1f} {gp:>6.1f} {rank:>3}  "
    return line + "".join(f"{p:>4} " for p in players).rstrip()


def _player(sn, name, rating, results, score):
    return format_player_line({"starting_number": sn, "name": name, "rating": rating, "score": score,
                               "results": results}, 2)


def _hand_written_trf():
    """
    5 teams, 2 boards, 2 rounds, match points 3/1/0.
    R1: 5 PAB; 1-2 (1 White on board 1: 1-0, =) -> 1 wins 1.5-0.5;
        3-4 forfeited by 4 (330 +-).
    R2: 5-1 with 1 out of default order (300: player 3 on board 1);
        board 1 p10-p3 0-1, board 2 p1-p11 1-0 -> 1 wins 2-0;
        3-2 (3 White): =, then p5-p7 0-1 -> 3 wins 1.5-0.5; 4 half-point bye.
    """
    r = lambda opp, col, res: {"opponent": opp, "color": col, "result": res}
    players = [
        _player(1, "Alpha, One", 2400, {1: r(4, "w", "1"), 2: r(11, "w", "1")}, 2.0),
        _player(2, "Alpha, Two", 2350, {1: r(5, "b", "=")}, 0.5),
        _player(3, "Alpha, Three", 2300, {2: r(10, "b", "1")}, 1.0),
        _player(4, "Beta, One", 2200, {1: r(1, "b", "0"), 2: r(6, "b", "=")}, 0.5),
        _player(5, "Beta, Two", 2150, {1: r(2, "w", "="), 2: r(7, "w", "0")}, 0.5),
        _player(6, "Gamma, One", 2100, {1: r(None, None, "+"), 2: r(4, "w", "=")}, 1.5),
        _player(7, "Gamma, Two", 2050, {1: r(None, None, "+"), 2: r(5, "b", "1")}, 2.0),
        _player(8, "Delta, One", 2000, {}, 0.0),
        _player(9, "Delta, Two", 1950, {}, 0.0),
        _player(10, "Eps, One", 1900, {2: r(3, "w", "0")}, 0.0),
        _player(11, "Eps, Two", 1850, {2: r(1, "b", "0")}, 0.0),
    ]
    lines = [
        "012 Hand-written team tournament",
        "142 2",
        "152 W",
        "162  W 1.0    D 0.5    L 0.0",
        "192 FIDE_TEAM_TYPEB_MP",
        "352 WB",
        "362 TW 3.0   TD 1.0   TL 0.0",
        *players,
        _team_310(1, "Alpha", "ALP", 2350, 6.0, 3.5, 1, [1, 2, 3]),
        _team_310(2, "Beta", "BET", 2175, 0.0, 1.0, 4, [4, 5]),
        _team_310(3, "Gamma", "GAM", 2075, 6.0, 3.5, 1, [6, 7]),
        _team_310(4, "Delta", "DEL", 1975, 1.0, 1.0, 3, [8, 9]),
        _team_310(5, "Eps", "EPS", 1875, 1.0, 1.0, 3, [10, 11]),
        "320  1.0  1.0   5   0",
        "330 +-   1   3   4",
        "300   2   1   5    3    1",
        "240 H   2    4",
        "260   3   3    1    3",
    ]
    return "\n".join(lines) + "\n"


class TestHandWrittenTRF26:
    def test_records(self):
        p = parse_team_trf(_hand_written_trf())
        assert [t["tpn"] for t in p["teams"]] == [1, 2, 3, 4, 5]
        assert p["teams"][0]["players"] == [1, 2, 3] and p["teams"][0]["nickname"] == "ALP"
        assert p["teams"][0]["match_points"] == 6.0 and p["teams"][0]["game_points"] == 3.5
        assert p["pab"] == {"mp": 1.0, "gp": 1.0, "rounds": {1: 5}}
        assert p["forfeits"] == [{"type": "+-", "round": 1, "white": 3, "black": 4}]
        assert p["out_of_order"] == [{"round": 2, "team": 1, "opponent": 5, "players": [3, 1]}]
        assert p["byes"] == [{"type": "H", "round": 2, "teams": [4]}]
        assert p["prohibited"] == [{"first_round": 3, "last_round": 3, "teams": [1, 3]}]
        t = p["tournament"]
        assert t["total_rounds"] == 2 and t["initial_color"] == "white" and t["board_colours"] == "WB"
        assert t["match_points"] == {"TW": 3.0, "TD": 1.0, "TL": 0.0}

    def test_settings(self):
        s = settings_from_trf(parse_team_trf(_hand_written_trf()))
        assert s["colour_preferences"] == "B" and s["primary_score"] == "mp"
        assert s["use_secondary_for_colours"] is False
        assert s["match_points_win"] == 3.0 and s["board_colours"] == "WB"
        assert s["bye_match_points"] == 1.0 and s["bye_game_points"] == 1.0
        assert s["forbidden_pairs"] == [{"teams": [1, 3], "first_round": 3, "last_round": 3}]

    def test_rebuilt_matches(self):
        rounds = team_rounds(parse_team_trf(_hand_written_trf()))
        key = lambda e: (e["opponent"], e["colour"], e["type"], e["mp"], e["gp"])
        assert {tpn: key(e) for tpn, e in rounds[1].items()} == {
            1: (2, "white", "played", 3.0, 1.5),
            2: (1, "black", "played", 0.0, 0.5),
            3: (4, "white", "forfeit_win", 3.0, 2.0),
            4: (3, "black", "forfeit_loss", 0.0, 0.0),
            5: (None, None, "pab", 1.0, 1.0),
        }
        assert {tpn: key(e) for tpn, e in rounds[2].items()} == {
            1: (5, "black", "played", 3.0, 2.0),      # board 1 is player 3 (300), Black
            5: (1, "white", "played", 0.0, 0.0),
            2: (3, "black", "played", 0.0, 0.5),
            3: (2, "white", "played", 3.0, 1.5),
            4: (None, None, "hpb", 1.0, 1.0),
        }

    def test_engine_input_for_round_3(self):
        teams, opts = engine_input(parse_team_trf(_hand_written_trf()), 3)
        by_id = {t["id"]: t for t in teams}
        assert [h["type"] for h in by_id[5]["history"]] == ["pab", "played"]
        assert sum(h["mp"] for h in by_id[1]["history"]) == 6.0

    def test_checker_reports_the_file(self):
        report = check_team_trf(_hand_written_trf(), colour_preferences="B")
        assert report["summary"]["rounds_checked"] == 2
        r1 = report["rounds"][0]
        assert {"white": 5, "black": None, "bye": True} in r1["trf_pairings"]

    def test_legacy_013(self):
        text = _hand_written_trf()
        lines = [l for l in text.split("\n") if not l.startswith("310")]
        legacy = [
            f"013 {'Alpha':<32}{1:>4} {2:>4} {3:>4}",
            f"013 {'Beta':<32}{4:>4} {5:>4}",
            f"013 {'Gamma':<32}{6:>4} {7:>4}",
            f"013 {'Delta':<32}{8:>4} {9:>4}",
            f"013 {'Eps':<32}{10:>4} {11:>4}",
        ]
        p = parse_team_trf("\n".join(lines + legacy))
        assert p["legacy_013"] and [t["players"] for t in p["teams"]][0] == [1, 2, 3]
        assert team_rounds(p)[1][1]["opponent"] == 2

    def test_trf16_parser_ignores_team_records(self):
        parsed = TRFParser(_hand_written_trf()).parse()
        assert len(parsed["players"]) == 11


class TestTypeCodesAndRecord250:
    @pytest.mark.parametrize("code, expected", [
        ("FIDE_TEAM", {"colour_preferences": "A", "primary_score": "mp", "use_secondary_for_colours": True, "accelerated": False}),
        ("FIDE_TEAM_BAKU", {"colour_preferences": "A", "primary_score": "mp", "use_secondary_for_colours": True, "accelerated": True}),
        ("FIDE_TEAM_TYPEA_GP_MP", {"colour_preferences": "A", "primary_score": "gp", "use_secondary_for_colours": True, "accelerated": False}),
        ("FIDE_TEAM_TYPEB_MP", {"colour_preferences": "B", "primary_score": "mp", "use_secondary_for_colours": False, "accelerated": False}),
        ("FIDE_TEAM_GP", {"colour_preferences": "none", "primary_score": "gp", "use_secondary_for_colours": False, "accelerated": False}),
        ("FIDE_TEAM_MP_GP_BAKU", {"colour_preferences": "none", "primary_score": "mp", "use_secondary_for_colours": True, "accelerated": True}),
    ])
    def test_parse(self, code, expected):
        got = parse_type_code(code)
        assert got.pop("fide") is True
        assert got == expected

    def test_round_trip_every_fide_team_code(self):
        for cp in ("A", "B", "none"):
            for primary in ("mp", "gp"):
                for sec in (True, False):
                    for baku in ((False, True) if primary == "mp" else (False,)):
                        s = {"colour_preferences": cp, "primary_score": primary,
                             "use_secondary_for_colours": sec, "accelerated": baku}
                        got = parse_type_code(type_code(s))
                        got.pop("fide")
                        assert got == s

    def test_non_team_codes(self):
        assert parse_type_code("FIDE_DUTCH_2026") == {}
        assert parse_type_code("CUSTOM_TEAM_SWISS_GP") == {"fide": False, "primary_score": "gp"}

    def _with_250(self, line):
        return _hand_written_trf().replace("192 FIDE_TEAM_TYPEB_MP", "192 FIDE_TEAM_BAKU") + line + "\n"

    def test_250_spec_order(self):
        p = parse_team_trf(self._with_250("250  2.0       1   1    1    2"))
        assert p["acceleration"] == [{"mp": 2.0, "gp": 0.0, "first_round": 1, "last_round": 1, "first_team": 1, "last_team": 2}]
        assert not p["warnings"]
        s = settings_from_trf(p)
        assert s["acceleration_records"] and s["accelerated"] is False

    def test_250_trf26_example_layout(self):
        line = "250 00.0 02.0 001 003 0001 0090"
        p = parse_team_trf(self._with_250(line))
        assert p["acceleration"][0]["mp"] == 0.0 and p["warnings"]
        p = parse_team_trf(self._with_250(line), baku_250_order="example")
        assert p["acceleration"][0] == {"mp": 2.0, "gp": 0.0, "first_round": 1, "last_round": 3,
                                        "first_team": 1, "last_team": 90}


class TestRoundTrip:
    @pytest.mark.parametrize("seed", range(60))
    def test_rtg_trf_parser_round_trip(self, seed):
        rng = random.Random(seed)
        rules = random_settings(rng)
        logging.disable(logging.WARNING)
        try:
            t = simulate_team_tournament(
                num_teams=rng.randint(3, 14), num_rounds=rng.randint(1, 9), seed=seed,
                boards=rng.choice([1, 2, 3, 4, 6]), forfeit_rate=0.1, double_forfeit_rate=0.02,
                board_forfeit_rate=0.08, out_of_order_rate=0.2, absence_rate=0.05, withdrawal_rate=0.03, **rules)
        finally:
            logging.disable(logging.NOTSET)
        text = write_team_trf(t)
        assert is_team_trf(text)
        parsed = parse_team_trf(text)
        assert parsed["warnings"] == []
        hist = team_history(parsed)
        for team in t["teams"]:
            mine = {h["round"]: h for h in team["history"]}
            got = {h["round"]: h for h in hist[team["tpn"]]}
            for rnd in range(1, t["total_rounds"] + 1):
                a, b = mine.get(rnd), got[rnd]
                if a is None:                      # withdrawn
                    assert b["type"] == "zpb"
                    continue
                col = a.get("color") if a["type"] not in ("pab", "zpb") else None
                assert (a.get("opponent"), col, a["type"], a["mp"], a["gp"]) == \
                       (b["opponent"], b["color"], b["type"], b["mp"], b["gp"])
        assert infer_initial_colour(parsed) in (None, t["initial_color"])

    def test_dutch_fixtures_are_not_team_files(self):
        for f in sorted(FIXTURES.glob("*.trf")):
            assert not is_team_trf(f.read_text())


def _rtg_fpc(seeds, lo, hi):
    bad = []
    rounds = 0
    logging.disable(logging.WARNING)
    try:
        for seed in seeds:
            rng = random.Random(seed)
            rules = random_settings(rng)
            n = rng.randint(lo, hi)
            trf = generate_team_tournament(
                num_teams=n, num_rounds=rng.randint(1, min(11, n + 1)), seed=seed,
                boards=rng.choice([2, 4, 5, 6]), forfeit_rate=rng.choice([0, 0.03, 0.1]),
                absence_rate=rng.choice([0, 0.03, 0.08]), withdrawal_rate=rng.choice([0, 0.02]), **rules)
            report = check_team_trf(trf)
            rounds += report["summary"]["rounds_checked"]
            if report["summary"]["rounds_mismatched"] or report["warnings"]:
                bad.append((seed, rules, [r["discrepancies"] for r in report["rounds"] if not r["match"]]))
    finally:
        logging.disable(logging.NOTSET)
    return rounds, bad


class TestRTGandFPC:
    def test_engine_tournaments_pass_the_checker(self):
        rounds, bad = _rtg_fpc(range(150), 3, 24)
        assert rounds > 500
        assert bad == []

    @pytest.mark.slow
    def test_engine_tournaments_pass_the_checker_many(self):
        rounds, bad = _rtg_fpc(range(1000, 3000), 3, 40)
        print("rounds checked:", rounds)
        assert bad == []

    def test_checker_finds_a_changed_pairing(self):
        trf = generate_team_tournament(num_teams=8, num_rounds=4, seed=3, forfeit_rate=0, absence_rate=0,
                                       withdrawal_rate=0)
        # Swap the board-1 colours of every game of round 2 (the colour of
        # the match): the checker must report it.
        lines = trf.split("\n")
        out = []
        for line in lines:
            if line.startswith("001 ") and len(line) > 107:
                block = line[101:111]
                if block[5] in "wb":
                    block = block[:5] + ("b" if block[5] == "w" else "w") + block[6:]
                    line = line[:101] + block + line[111:]
            out.append(line)
        report = check_team_trf("\n".join(out))
        r2 = [r for r in report["rounds"] if r["round"] == 2][0]
        assert not r2["match"] and any(d.startswith("Colours") for d in r2["discrepancies"])

    def test_baku_rtg_writes_record_250(self):
        trf = generate_team_tournament(num_teams=12, num_rounds=9, seed=1, accelerated=True)
        assert "192 FIDE_TEAM_TYPEA_MP_GP_BAKU" in trf
        assert "250  2.0  0.0   1   3    1    6" in trf and "250  1.0  0.0   4   5    1    6" in trf
        assert check_team_trf(trf)["summary"]["rounds_mismatched"] == 0


class TestCLI:
    def test_check_detects_team_files(self, tmp_path, monkeypatch, capsys):
        from caissify_pairings.fpc import main

        path = tmp_path / "team.trf"
        path.write_text(generate_team_tournament(num_teams=6, num_rounds=3, seed=2))
        monkeypatch.setattr(sys, "argv", ["caissify-pairings-check", str(path)])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        assert "3/3 rounds match" in capsys.readouterr().out

    def test_check_team_json(self, tmp_path, monkeypatch, capsys):
        from caissify_pairings.fpc import main

        path = tmp_path / "team.trf"
        path.write_text(generate_team_tournament(num_teams=7, num_rounds=3, seed=4))
        monkeypatch.setattr(sys, "argv", ["caissify-pairings-check", "--team", "--json", str(path)])
        with pytest.raises(SystemExit) as exc:
            main()
        report = json.loads(capsys.readouterr().out)
        assert exc.value.code == 0 and report["summary"]["rounds_matched"] == 3

    def test_pair_next_round_from_trf(self, tmp_path, monkeypatch, capsys):
        from caissify_pairings.__main__ import main

        t = simulate_team_tournament(num_teams=6, num_rounds=3, seed=5, forfeit_rate=0, absence_rate=0,
                                     withdrawal_rate=0)
        full = write_team_trf(t)
        path = tmp_path / "team.trf"
        path.write_text(full)
        monkeypatch.setattr(sys, "argv", ["caissify-pairings", "--team", str(path), "--round", "3"])
        with pytest.raises(SystemExit) as exc:
            main()
        rows = json.loads(capsys.readouterr().out)
        assert exc.value.code == 0
        assert sorted((r["white_id"], r["black_id"]) for r in rows) == sorted(t["rounds"][3]["pairs"])

    def test_pair_from_json_stdin(self, monkeypatch, capsys):
        from caissify_pairings.__main__ import main

        state = {"system": "team_swiss", "teams": [{"id": i, "name": f"T{i}"} for i in range(1, 7)],
                 "round_number": 1, "total_rounds": 5, "colour_preferences": "B", "boards": 6}
        monkeypatch.setattr(sys, "argv", ["caissify-pairings"])
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(state)))
        main()
        rows = json.loads(capsys.readouterr().out)
        assert [(r["white_id"], r["black_id"]) for r in rows] == [(1, 4), (5, 2), (3, 6)]
        assert len(rows[0]["boards"]) == 6

    def test_team_flag_with_json_stdin(self, monkeypatch, capsys):
        from caissify_pairings.__main__ import main

        state = {"teams": [{"id": i} for i in range(1, 6)], "round_number": 1, "total_rounds": 5}
        monkeypatch.setattr(sys, "argv", ["caissify-pairings", "--team"])
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(state)))
        with pytest.raises(SystemExit) as exc:
            main()
        rows = json.loads(capsys.readouterr().out)
        assert exc.value.code == 0 and rows[-1]["bye"] and rows[-1]["white_id"] == 5

    def test_rtg_team_cli(self, tmp_path, monkeypatch):
        from caissify_pairings.rtg import main

        monkeypatch.setattr(sys, "argv", ["caissify-pairings-rtg", "--team", "-t", "9", "-r", "4", "-n", "2",
                                          "--colours", "B", "--seed", "1", "-o", str(tmp_path)])
        with pytest.raises(SystemExit) as exc:
            main()
        files = sorted(tmp_path.glob("*.trf"))
        assert exc.value.code == 0 and len(files) == 2
        text = files[0].read_text()
        assert "192 FIDE_TEAM_TYPEB_MP_GP" in text
        assert check_team_trf(text)["summary"]["rounds_mismatched"] == 0
