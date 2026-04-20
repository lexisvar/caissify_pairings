"""
Helpers for invoking JaVaFo 2.x as a second FIDE-endorsed reference oracle.

JaVaFo is authored by Roberto Ricca, Secretary of the FIDE SPPC — the
commission that writes C.04.3 itself. Alongside bbpPairings, JaVaFo is
one of the two reference engines we cross-validate our Python engine
against per FIDE A.7.

This module is script-only (it does NOT live under src/) so that the
core package has no Java runtime dependency. Tests that need it guard
on the jar + a usable JVM being present.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
JAVAFO_JAR = PROJECT_ROOT / "vendor" / "javafo" / "javafo.jar"

# Preferred JVM locations on macOS/Linux. We avoid /usr/bin/java on macOS
# because that's a stub that prints "Unable to locate a Java Runtime"
# unless a JDK has been installed in a very specific location.
_CANDIDATE_JAVA_PATHS = [
    os.environ.get("CAISSIFY_JAVA"),
    "/opt/homebrew/opt/openjdk/bin/java",
    "/usr/local/opt/openjdk/bin/java",
    "/opt/homebrew/bin/java",
    shutil.which("java"),
]


def _probes_ok(path: str) -> bool:
    try:
        r = subprocess.run(
            [path, "-version"], capture_output=True, text=True, timeout=5
        )
    except Exception:
        return False
    return r.returncode == 0


def find_java() -> Optional[str]:
    for p in _CANDIDATE_JAVA_PATHS:
        if p and Path(p).exists() and _probes_ok(p):
            return p
    return None


def is_available() -> bool:
    return JAVAFO_JAR.exists() and find_java() is not None


def _run(args: List[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False
    )


def version() -> str:
    java = find_java()
    if not java:
        return "java-not-found"
    r = _run([java, "-ea", "-jar", str(JAVAFO_JAR), "-r"], timeout=10)
    return (r.stdout or r.stderr).strip().splitlines()[0] if r.stdout or r.stderr else "unknown"


# ---------------------------------------------------------------------------
# Pairing generation (engine mode)
# ---------------------------------------------------------------------------

def pair_round(trf_path: str | os.PathLike) -> List[Tuple[int, int]]:
    """
    Ask JaVaFo to pair the *next* round of the tournament described by the
    given TRF. Returns a list of `(white_id, black_id)` tuples. A bye is
    encoded as `(player_id, 0)` as per the JaVaFo AUM.
    """
    java = find_java()
    if not java:
        raise RuntimeError("No Java runtime available")
    trf_path = str(trf_path)
    out_path = trf_path + ".pair.out"
    try:
        r = _run([java, "-ea", "-jar", str(JAVAFO_JAR), trf_path, "-p", out_path])
        if r.returncode != 0 or not Path(out_path).exists():
            raise RuntimeError(f"javafo -p failed: {r.stderr or r.stdout}")
        with open(out_path) as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        # line 0 = pair count, lines 1..P = pairs
        pairs: List[Tuple[int, int]] = []
        for ln in lines[1:]:
            parts = ln.split()
            if len(parts) < 2:
                continue
            w, b = int(parts[0]), int(parts[1])
            pairs.append((w, b))
        return pairs
    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)


# ---------------------------------------------------------------------------
# Pairings checker (FPC mode)
# ---------------------------------------------------------------------------

# Per the JaVaFo AUM, the checker's output per diverging round is:
#
#     <name>: Round #N
#       Checker pairings        Tournament pairings
#           6 -   8                  8 -   1
#         PAB:    1                PAB:    6
#
# Matching rounds print just "<name>: Round #N" with no body.

_ROUND_HDR = re.compile(r":\s*Round\s*#(\d+)\s*$")
_PAIR_LINE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s+(\d+)\s*-\s*(\d+)\s*$")
_BYE_LINE = re.compile(r"^\s*PAB:\s*(\d+)\s+PAB:\s*(\d+)\s*$")


def check_trf(trf_path: str | os.PathLike) -> Dict:
    """
    Run `javafo <trf> -c`, parse the diagnostic output.

    Returns::

        {
            "rounds": {
                <round_no>: {
                    # Pairs that appear only in JaVaFo's view (checker column).
                    "javafo_only_pairs": {(min(a,b), max(a,b)), ...},
                    # Pairs that appear only in the TRF (tournament column).
                    "trf_only_pairs":    {(min(a,b), max(a,b)), ...},
                    "javafo_bye":   int | None,   # from "PAB: N"  (checker)
                    "trf_bye":      int | None,   # from "PAB: M"  (tournament)
                    "match":        bool,         # True if no body emitted
                    "has_body":     bool,
                },
                ...
            },
            "rounds_checked":   int,
            "rounds_mismatched": int,
            "total_discrepancies": int,
        }

    NOTE: JaVaFo's ``-c`` output lists **only the pairs that differ**, not
    the full round. To reconstruct JaVaFo's full pair set, the caller
    needs the TRF's own pair set and should compute
    ``(trf_full - trf_only_pairs) | javafo_only_pairs``.
    """
    java = find_java()
    if not java:
        raise RuntimeError("No Java runtime available")
    r = _run([java, "-ea", "-jar", str(JAVAFO_JAR), str(trf_path), "-c"])
    output = r.stdout + r.stderr

    rounds: Dict[int, Dict] = {}
    current: Optional[Dict] = None
    current_no: Optional[int] = None

    def _commit():
        nonlocal current, current_no
        if current is not None and current_no is not None:
            jv = current["javafo_only_pairs"]
            tv = current["trf_only_pairs"]
            has_body = len(jv) > 0 or len(tv) > 0 or \
                current["javafo_bye"] is not None or \
                current["trf_bye"] is not None
            current["has_body"] = has_body
            current["match"] = not has_body
            rounds[current_no] = current
        current = None
        current_no = None

    for line in output.splitlines():
        m = _ROUND_HDR.search(line)
        if m:
            _commit()
            current_no = int(m.group(1))
            current = {
                "javafo_only_pairs": set(),
                "trf_only_pairs": set(),
                "javafo_bye": None,
                "trf_bye": None,
            }
            continue
        if current is None:
            continue
        mp = _PAIR_LINE.match(line)
        if mp:
            jw, jb, tw, tb = (int(x) for x in mp.groups())
            current["javafo_only_pairs"].add((min(jw, jb), max(jw, jb)))
            current["trf_only_pairs"].add((min(tw, tb), max(tw, tb)))
            continue
        mb = _BYE_LINE.match(line)
        if mb:
            current["javafo_bye"] = int(mb.group(1))
            current["trf_bye"] = int(mb.group(2))
            continue
    _commit()

    total_disc = 0
    mismatched = 0
    for rd in rounds.values():
        if not rd["match"]:
            mismatched += 1
            total_disc += len(rd["javafo_only_pairs"]) + len(rd["trf_only_pairs"])
            if (rd["javafo_bye"] is not None or rd["trf_bye"] is not None) \
                    and rd["javafo_bye"] != rd["trf_bye"]:
                total_disc += 1

    return {
        "rounds": rounds,
        "rounds_checked": len(rounds),
        "rounds_mismatched": mismatched,
        "total_discrepancies": total_disc,
    }
