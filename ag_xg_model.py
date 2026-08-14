"""
AG-xG Model — v1
================
AccaGenius expected goals estimation from live API-Football statistics.

Design goals:
- Versioned: AG-xG-v1 so future versions don't break historical data
- Configurable: all weights in AG_XG_V1_CONFIG, no magic numbers elsewhere
- Graceful: returns None rather than inventing xG when stats unavailable
- Reusable: pure functions + stateful tracker, no FastAPI dependency
- Testable: all core logic in this file, no side effects

Integration:
  API-Football live stats → ag_xg_model.compute() → signals + frontend

Author: AGD Sports Trading
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from collections import deque
import math
import time

# ─────────────────────────────────────────────────────────────────────────────
# AG-xG v1 Configuration
# Change weights here — nowhere else.
# ─────────────────────────────────────────────────────────────────────────────

AG_XG_V1_CONFIG = {
    "version": "AG-xG-v1",

    # Shot quality weights (applied when Opta/StatsBomb real xG is unavailable)
    "w_shots_on_target": 0.30,   # each shot on target worth ~0.30 xG
    "w_shots_inside_box": 0.10,  # extra credit for inside-box shots
    "w_shots_outside_box": 0.03, # long-range shots — low xG
    "w_keeper_saves": 0.12,      # opponent saves = we had dangerous chances

    # Penalty and goal corrections
    # Goals inflate xG so we subtract a fixed penalty xG per goal scored
    # (avoids double-counting: goal already happened, xG should reflect shots not outcomes)
    "penalty_xg": 0.76,          # average PK xG (empirically ~0.76)
    "subtract_goal_xg": False,   # set True to subtract goal xG from shot-based total

    # Momentum window (in minutes)
    "momentum_window_short": 5,   # last 5 min xG accumulation → "last_5"
    "momentum_window_long": 10,   # last 10 min xG accumulation → "last_10"

    # Momentum thresholds
    "momentum_high_threshold": 0.25,  # xG in last 5 min to be "HIGH"
    "momentum_low_threshold":  0.08,  # below this in last 5 min = "LOW"

    # Minimum minutes before we trust the estimate
    "min_minutes_for_estimate": 10,

    # Maximum plausible xG per team for a full match (sanity cap)
    "max_xg_per_team": 5.0,
}


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class XGSnapshot:
    """Single xG reading at a point in time."""
    minute:    int
    xg:        float   # cumulative xG at this minute
    timestamp: float = field(default_factory=time.time)


@dataclass
class TeamXGState:
    """Rolling xG state for one team in one fixture."""
    snapshots: deque = field(default_factory=lambda: deque(maxlen=120))
    last_xg:   float = 0.0

    def record(self, minute: int, xg: float):
        self.snapshots.append(XGSnapshot(minute=minute, xg=xg))
        self.last_xg = xg

    def xg_in_window(self, current_minute: int, window_minutes: int) -> float:
        """xG accumulated in the last N minutes."""
        cutoff = current_minute - window_minutes
        snaps = [s for s in self.snapshots if s.minute >= cutoff]
        if len(snaps) < 2:
            return 0.0
        return max(0.0, snaps[-1].xg - snaps[0].xg)


@dataclass
class AGXGResult:
    """Output of AG-xG computation for one team."""
    version:    str             = AG_XG_V1_CONFIG["version"]
    xg:         Optional[float] = None   # None = unavailable (do not show)
    xg_real:    bool            = False  # True = Opta xG, False = our estimate
    last_5:     Optional[float] = None   # xG in last 5 min
    last_10:    Optional[float] = None   # xG in last 10 min
    momentum:   Optional[str]  = None   # HIGH / MEDIUM / LOW / None
    data_source: str            = "none" # "opta" | "estimated" | "none"

    def to_dict(self) -> dict:
        return {
            "version":    self.version,
            "xg":         self.xg,
            "xg_real":    self.xg_real,
            "last_5":     self.last_5,
            "last_10":    self.last_10,
            "momentum":   self.momentum,
            "data_source": self.data_source,
        }


@dataclass
class AGXGMatchResult:
    """Full match xG result — home + away."""
    home:    AGXGResult
    away:    AGXGResult
    version: str = AG_XG_V1_CONFIG["version"]

    @property
    def available(self) -> bool:
        return self.home.xg is not None and self.away.xg is not None

    @property
    def match_xg(self) -> Optional[float]:
        if not self.available: return None
        return round(self.home.xg + self.away.xg, 2)

    @property
    def supremacy(self) -> Optional[float]:
        if not self.available: return None
        return round(self.home.xg - self.away.xg, 2)

    def to_dict(self) -> dict:
        return {
            "version":    self.version,
            "available":  self.available,
            "home":       self.home.to_dict(),
            "away":       self.away.to_dict(),
            "match_xg":   self.match_xg,
            "supremacy":  self.supremacy,
            "label":      "AccaGenius Est." if not (self.home.xg_real or self.away.xg_real) else "AG-xG",
        }


# ─────────────────────────────────────────────────────────────────────────────
# Core model — stateless computation
# ─────────────────────────────────────────────────────────────────────────────

class AgXGModel:
    """
    AG-xG v1 — stateless xG computation.
    All state lives in the caller (AgXGTracker).
    """

    def __init__(self, config: dict = None):
        self.cfg = config or AG_XG_V1_CONFIG

    def compute_from_stats(self, stats: dict, minute: int) -> Optional[float]:
        """
        Compute xG from raw team stats dict (keys from FIELD_MAP in main.py).

        Returns None if insufficient data to make a meaningful estimate.
        Returns float xG if we can compute something reasonable.
        """
        cfg = self.cfg

        if not stats or minute < cfg["min_minutes_for_estimate"]:
            return None

        # Prefer real Opta/StatsBomb xG if available
        real_xg = self._parse_float(stats.get("expected_goals"))
        if real_xg is not None and real_xg > 0:
            return round(min(real_xg, cfg["max_xg_per_team"]), 3)

        # Estimate from shots
        sot    = self._parse_float(stats.get("shots_on_goal"))
        ib     = self._parse_float(stats.get("shots_inside_box"))
        ob     = self._parse_float(stats.get("shots_outside_box") or
                                   stats.get("shots_offgoal"))  # some APIs use this
        saves  = self._parse_float(stats.get("goalkeeper_saves"))  # opponent's saves = our shots on target saved
        total  = self._parse_float(stats.get("total_shots"))

        # Need at least shots on target or saves to proceed
        if sot is None and saves is None:
            return None

        sot   = sot   or 0.0
        ib    = ib    or 0.0
        ob    = ob    or 0.0
        saves = saves or 0.0

        # If inside_box > total shots something is wrong — cap it
        if total is not None and ib > total:
            ib = total

        xg = (
            sot   * cfg["w_shots_on_target"]
            + ib   * cfg["w_shots_inside_box"]
            + ob   * cfg["w_shots_outside_box"]
            + saves * cfg["w_keeper_saves"]
        )

        if xg <= 0:
            return None  # no data, don't invent

        return round(min(xg, cfg["max_xg_per_team"]), 3)

    def is_real_xg(self, stats: dict) -> bool:
        """True if stats contain official Opta/StatsBomb xG."""
        v = self._parse_float(stats.get("expected_goals"))
        return v is not None and v > 0

    def compute_momentum(self, last_5: Optional[float]) -> Optional[str]:
        """Classify momentum based on last-5-minute xG."""
        if last_5 is None:
            return None
        if last_5 >= self.cfg["momentum_high_threshold"]:
            return "HIGH"
        if last_5 >= self.cfg["momentum_low_threshold"]:
            return "MEDIUM"
        return "LOW"

    @staticmethod
    def _parse_float(v) -> Optional[float]:
        if v is None: return None
        try:
            f = float(str(v).replace("%","").strip())
            return f if math.isfinite(f) else None
        except (ValueError, TypeError):
            return None


# ─────────────────────────────────────────────────────────────────────────────
# Stateful tracker — one instance per fixture
# ─────────────────────────────────────────────────────────────────────────────

class AgXGTracker:
    """
    Stateful xG tracker for a single fixture.
    Records xG snapshots over time and computes rolling momentum.
    """

    def __init__(self, fixture_id: int, home: str, away: str,
                 config: dict = None):
        self.fixture_id = fixture_id
        self.home       = home
        self.away       = away
        self.model      = AgXGModel(config or AG_XG_V1_CONFIG)
        self.home_state = TeamXGState()
        self.away_state = TeamXGState()

    def update(self, home_stats: dict, away_stats: dict,
               minute: int) -> AGXGMatchResult:
        """
        Process latest stats snapshot. Returns full xG result.
        Fails gracefully — returns unavailable result if stats missing.
        """
        cfg = self.model.cfg

        h_xg = self.model.compute_from_stats(home_stats, minute)
        a_xg = self.model.compute_from_stats(away_stats, minute)

        h_real = self.model.is_real_xg(home_stats)
        a_real = self.model.is_real_xg(away_stats)

        # Record snapshots
        if h_xg is not None:
            self.home_state.record(minute, h_xg)
        if a_xg is not None:
            self.away_state.record(minute, a_xg)

        # Rolling windows
        h_last5  = self.home_state.xg_in_window(minute, cfg["momentum_window_short"]) if h_xg else None
        h_last10 = self.home_state.xg_in_window(minute, cfg["momentum_window_long"])  if h_xg else None
        a_last5  = self.away_state.xg_in_window(minute, cfg["momentum_window_short"]) if a_xg else None
        a_last10 = self.away_state.xg_in_window(minute, cfg["momentum_window_long"])  if a_xg else None

        home_result = AGXGResult(
            xg=h_xg,
            xg_real=h_real,
            last_5=round(h_last5, 3) if h_last5 is not None else None,
            last_10=round(h_last10, 3) if h_last10 is not None else None,
            momentum=self.model.compute_momentum(h_last5),
            data_source="opta" if h_real else ("estimated" if h_xg is not None else "none"),
        )
        away_result = AGXGResult(
            xg=a_xg,
            xg_real=a_real,
            last_5=round(a_last5, 3) if a_last5 is not None else None,
            last_10=round(a_last10, 3) if a_last10 is not None else None,
            momentum=self.model.compute_momentum(a_last5),
            data_source="opta" if a_real else ("estimated" if a_xg is not None else "none"),
        )

        return AGXGMatchResult(home=home_result, away=away_result)


# ─────────────────────────────────────────────────────────────────────────────
# Global registry — fixture_id → AgXGTracker
# ─────────────────────────────────────────────────────────────────────────────

_xg_trackers: dict[int, AgXGTracker] = {}

def get_or_create_tracker(fixture_id: int, home: str, away: str) -> AgXGTracker:
    if fixture_id not in _xg_trackers:
        _xg_trackers[fixture_id] = AgXGTracker(fixture_id, home, away)
    return _xg_trackers[fixture_id]

def clear_old_trackers(active_fixture_ids: set):
    """Remove trackers for fixtures no longer live — called nightly."""
    stale = [k for k in _xg_trackers if k not in active_fixture_ids]
    for k in stale:
        del _xg_trackers[k]


# ─────────────────────────────────────────────────────────────────────────────
# Tests — run with: python3 ag_xg_model.py
# ─────────────────────────────────────────────────────────────────────────────

def _run_tests():
    model = AgXGModel()
    passed = failed = 0

    def check(name, condition, detail=""):
        nonlocal passed, failed
        if condition:
            print(f"  ✅ {name}")
            passed += 1
        else:
            print(f"  ❌ {name} — {detail}")
            failed += 1

    print("\n=== AG-xG v1 Tests ===\n")

    # 1. No stats → None
    check("Empty stats → None", model.compute_from_stats({}, 45) is None)

    # 2. Too early → None
    check("Minute 5 → None", model.compute_from_stats({"shots_on_goal": 3}, 5) is None)

    # 3. Real xG used when present
    r = model.compute_from_stats({"expected_goals": 1.46}, 45)
    check("Real xG passed through", r == 1.46, r)

    # 4. Shot-based estimation
    r = model.compute_from_stats({"shots_on_goal": 5, "shots_inside_box": 3}, 45)
    check("Shot-based estimate > 0", r is not None and r > 0, r)
    expected = round(5*0.30 + 3*0.10, 3)
    check("Shot weights correct", r == expected, f"got {r}, expected {expected}")

    # 5. Zero shots → None (don't invent)
    check("Zero shots → None", model.compute_from_stats({"shots_on_goal": 0, "goalkeeper_saves": 0}, 45) is None)

    # 6. Max cap
    r = model.compute_from_stats({"expected_goals": 99.9}, 45)
    check("xG capped at max", r == AG_XG_V1_CONFIG["max_xg_per_team"], r)

    # 7. Momentum HIGH
    check("Momentum HIGH", model.compute_momentum(0.30) == "HIGH")
    check("Momentum MEDIUM", model.compute_momentum(0.15) == "MEDIUM")
    check("Momentum LOW", model.compute_momentum(0.05) == "LOW")
    check("Momentum None", model.compute_momentum(None) is None)

    # 8. Tracker — graceful failure
    tracker = AgXGTracker(999, "Home", "Away")
    result = tracker.update({}, {}, 45)
    check("Tracker fails gracefully", not result.available)
    check("Tracker returns unavailable not crash", result.match_xg is None)

    # 9. Tracker — valid data
    tracker2 = AgXGTracker(1000, "Home", "Away")
    r2 = tracker2.update(
        {"expected_goals": 1.46, "shots_on_goal": 5},
        {"expected_goals": 0.73, "shots_on_goal": 2},
        45
    )
    check("Tracker with real xG available", r2.available)
    check("Home xG correct", r2.home.xg == 1.46, r2.home.xg)
    check("Away xG correct", r2.away.xg == 0.73, r2.away.xg)
    check("Match xG correct", r2.match_xg == 2.19, r2.match_xg)
    check("Supremacy correct", r2.supremacy == 0.73, r2.supremacy)
    check("Data source is opta", r2.home.data_source == "opta")

    # 10. Rolling window — momentum after multiple updates
    tracker3 = AgXGTracker(1001, "Home", "Away")
    tracker3.update({"expected_goals": 0.5}, {"expected_goals": 0.2}, 30)
    tracker3.update({"expected_goals": 0.8}, {"expected_goals": 0.3}, 35)
    r3 = tracker3.update({"expected_goals": 1.3}, {"expected_goals": 0.4}, 38)
    check("Rolling window last_5 populated", r3.home.last_5 is not None, r3.home.last_5)
    check("Momentum populated", r3.home.momentum in ("HIGH","MEDIUM","LOW"), r3.home.momentum)

    # 11. Result dict format
    d = r2.to_dict()
    check("to_dict has version", d["version"] == "AG-xG-v1")
    check("to_dict has available", "available" in d)
    check("to_dict has label", "label" in d)

    # 12. Partial stats — saves only
    r4 = model.compute_from_stats({"goalkeeper_saves": 4}, 45)
    check("Saves-only estimate works", r4 is not None and r4 > 0, r4)

    print(f"\n{'='*30}")
    print(f"Results: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    success = _run_tests()
    exit(0 if success else 1)
