import pytest
from swarm import (
    OffspringProfile,
    SwarmState,
    evaluate,
    feedback_to_parent,
    select_survivor,
    spawn,
    _normalize,
)


def make_parent(**kwargs) -> SwarmState:
    defaults = dict(
        instance_id="test-swarm",
        weight_safety=0.34,
        weight_clarity=0.33,
        weight_human=0.33,
        generation=0,
        best_fitness=0.0,
    )
    defaults.update(kwargs)
    return SwarmState(**defaults)


def nominal_metrics() -> dict:
    return {"energy": 0.8, "risk": 0.2, "reward_mod": 0.9, "ethics_score": 0.95}


# ── spawn ─────────────────────────────────────────────────────────────────

class TestSpawn:
    def test_returns_correct_count(self):
        parent = make_parent()
        offspring = spawn(parent, n=5, divergence_factor=0.05, genesis_step=0)
        assert len(offspring) == 5

    def test_weights_sum_to_one(self):
        parent = make_parent()
        for child in spawn(parent, n=10, divergence_factor=0.1, genesis_step=0):
            total = child.weight_safety + child.weight_clarity + child.weight_human
            assert abs(total - 1.0) < 1e-6, f"weights sum to {total}"

    def test_weights_all_positive(self):
        parent = make_parent()
        for child in spawn(parent, n=20, divergence_factor=0.5, genesis_step=0):
            assert child.weight_safety  > 0
            assert child.weight_clarity > 0
            assert child.weight_human   > 0

    def test_inherits_genesis_step(self):
        parent = make_parent()
        offspring = spawn(parent, n=3, divergence_factor=0.05, genesis_step=42)
        assert all(o.genesis_step == 42 for o in offspring)

    def test_zero_divergence_clones_parent(self):
        parent = make_parent()
        # With zero divergence Gaussian noise is still possible but extremely small;
        # test that weights remain close (within 0.01 of parent values).
        for child in spawn(parent, n=5, divergence_factor=0.0, genesis_step=0):
            assert abs(child.weight_safety  - parent.weight_safety)  < 0.01
            assert abs(child.weight_clarity - parent.weight_clarity) < 0.01
            assert abs(child.weight_human   - parent.weight_human)   < 0.01


# ── evaluate ─────────────────────────────────────────────────────────────

class TestEvaluate:
    def _child(self) -> OffspringProfile:
        return OffspringProfile(
            tag="test", genesis_step=0, inherited_reward=0.0,
            divergence_factor=0.05,
            weight_safety=0.34, weight_clarity=0.33, weight_human=0.33,
        )

    def test_fitness_in_unit_range(self):
        child = self._child()
        fitness = evaluate(child, nominal_metrics())
        assert 0.0 <= fitness <= 1.0

    def test_high_risk_reduces_fitness(self):
        child = self._child()
        low_risk  = evaluate(self._child(), {**nominal_metrics(), "risk": 0.1})
        high_risk = evaluate(self._child(), {**nominal_metrics(), "risk": 0.9})
        assert low_risk > high_risk

    def test_zero_energy_gives_zero_fitness(self):
        child = self._child()
        fitness = evaluate(child, {**nominal_metrics(), "energy": 0.0})
        assert fitness == 0.0

    def test_fitness_stored_on_child(self):
        child = self._child()
        evaluate(child, nominal_metrics())
        assert child.fitness is not None
        assert child.evaluated_at is not None


# ── select_survivor ───────────────────────────────────────────────────────

class TestSelectSurvivor:
    def test_returns_highest_fitness(self):
        a = OffspringProfile("a", 0, 0, 0.05, 0.34, 0.33, 0.33, fitness=0.3)
        b = OffspringProfile("b", 0, 0, 0.05, 0.34, 0.33, 0.33, fitness=0.7)
        c = OffspringProfile("c", 0, 0, 0.05, 0.34, 0.33, 0.33, fitness=0.1)
        assert select_survivor([a, b, c]).tag == "b"

    def test_empty_list_returns_none(self):
        assert select_survivor([]) is None

    def test_unevaluated_children_ignored(self):
        evaluated = OffspringProfile("e", 0, 0, 0.05, 0.34, 0.33, 0.33, fitness=0.5)
        unevaluated = OffspringProfile("u", 0, 0, 0.05, 0.34, 0.33, 0.33)
        assert select_survivor([evaluated, unevaluated]).tag == "e"


# ── feedback_to_parent ────────────────────────────────────────────────────

class TestFeedbackToParent:
    def test_generation_increments(self):
        parent = make_parent()
        winner = OffspringProfile("w", 0, 0, 0.05, 0.5, 0.3, 0.2, fitness=0.8)
        result = feedback_to_parent(winner, parent)
        assert result.generation == 1

    def test_weights_shift_toward_winner(self):
        parent = make_parent(weight_safety=0.34, weight_clarity=0.33, weight_human=0.33)
        winner = OffspringProfile("w", 0, 0, 0.05, 0.6, 0.2, 0.2, fitness=0.9)
        result = feedback_to_parent(winner, parent, learning_rate=1.0)
        # With lr=1.0 the parent should fully adopt the winner's weights
        assert abs(result.weight_safety - 0.6) < 0.01

    def test_weights_still_sum_to_one_after_feedback(self):
        parent = make_parent()
        winner = OffspringProfile("w", 0, 0, 0.05, 0.5, 0.3, 0.2, fitness=0.8)
        result = feedback_to_parent(winner, parent, learning_rate=0.2)
        total = result.weight_safety + result.weight_clarity + result.weight_human
        assert abs(total - 1.0) < 1e-5

    def test_best_fitness_updates(self):
        parent = make_parent(best_fitness=0.3)
        winner = OffspringProfile("w", 0, 0, 0.05, 0.34, 0.33, 0.33, fitness=0.9)
        result = feedback_to_parent(winner, parent)
        assert result.best_fitness == 0.9

    def test_best_fitness_not_downgraded(self):
        parent = make_parent(best_fitness=0.95)
        winner = OffspringProfile("w", 0, 0, 0.05, 0.34, 0.33, 0.33, fitness=0.5)
        result = feedback_to_parent(winner, parent)
        assert result.best_fitness == 0.95


# ── _normalize ────────────────────────────────────────────────────────────

class TestNormalize:
    def test_sums_to_one(self):
        s, c, h = _normalize(3.0, 2.0, 1.0)
        assert abs(s + c + h - 1.0) < 1e-9

    def test_zero_inputs_give_defaults(self):
        s, c, h = _normalize(0.0, 0.0, 0.0)
        assert abs(s - 0.34) < 1e-9
        assert abs(c - 0.33) < 1e-9
        assert abs(h - 0.33) < 1e-9
