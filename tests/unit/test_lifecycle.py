"""Unit tests for the Kernel lifecycle state machine (ADR-0009)."""
from __future__ import annotations

import pytest

from kernel.lifecycle import KernelState, Lifecycle


class TestLegalTransitions:
    def test_starts_in_booting(self):
        assert Lifecycle().state == KernelState.BOOTING

    def test_full_happy_path(self):
        lc = Lifecycle()
        assert lc.transition(KernelState.RUNNING) == KernelState.RUNNING
        assert lc.transition(KernelState.PAUSED) == KernelState.PAUSED
        assert lc.transition(KernelState.RUNNING) == KernelState.RUNNING
        assert lc.transition(KernelState.STOPPING) == KernelState.STOPPING
        assert lc.transition(KernelState.STOPPED) == KernelState.STOPPED

    def test_boot_can_go_straight_to_stopping(self):
        lc = Lifecycle()
        assert lc.transition(KernelState.STOPPING) == KernelState.STOPPING


class TestIllegalTransitions:
    @pytest.mark.parametrize(
        "target",
        [KernelState.PAUSED, KernelState.STOPPED, KernelState.BOOTING],
    )
    def test_illegal_from_booting(self, target):
        with pytest.raises(RuntimeError):
            Lifecycle().transition(target)

    def test_stopped_is_terminal(self):
        lc = Lifecycle()
        lc.transition(KernelState.RUNNING)
        lc.transition(KernelState.STOPPING)
        lc.transition(KernelState.STOPPED)
        for target in KernelState:
            with pytest.raises(RuntimeError):
                lc.transition(target)

    def test_cannot_resume_from_running(self):
        lc = Lifecycle()
        lc.transition(KernelState.RUNNING)
        # RUNNING → RUNNING is not a defined transition.
        with pytest.raises(RuntimeError):
            lc.transition(KernelState.RUNNING)


class TestIsHelper:
    def test_is_matches_any(self):
        lc = Lifecycle()
        assert lc.is_(KernelState.BOOTING)
        assert lc.is_(KernelState.RUNNING, KernelState.BOOTING)
        assert not lc.is_(KernelState.STOPPED)
