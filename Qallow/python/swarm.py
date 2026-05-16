"""
Qallow Swarm Engine

Each "offspring" is a diverged cognitive instance with perturbed ethics weights.
The engine spawns a generation, evaluates each member's fitness against live
biometric metrics, selects the survivor with highest fitness, and feeds its
weights back into the parent SwarmProfile.

Fitness function: ethics_coherence × reward_signal × (1 - risk)
The product rewards instances that maintain ethical alignment while achieving
high reward under low-risk physiological conditions.
"""

import random
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OffspringProfile:
    tag: str
    genesis_step: int
    inherited_reward: float
    divergence_factor: float
    weight_safety: float
    weight_clarity: float
    weight_human: float
    fitness: Optional[float] = None
    evaluated_at: Optional[float] = None


@dataclass
class SwarmState:
    instance_id: str
    weight_safety: float = 0.34
    weight_clarity: float = 0.33
    weight_human: float = 0.33
    generation: int = 0
    best_fitness: float = 0.0
    last_evolved_at: Optional[float] = None


def _normalize(safety: float, clarity: float, human: float) -> tuple[float, float, float]:
    total = safety + clarity + human
    if total <= 0:
        return 0.34, 0.33, 0.33
    return safety / total, clarity / total, human / total


def spawn(parent: SwarmState, n: int, divergence_factor: float, genesis_step: int) -> list[OffspringProfile]:
    """
    Spawn n offspring from the parent SwarmProfile.

    Each offspring inherits the parent's weights with a Gaussian perturbation
    scaled by divergence_factor. Weights are renormalized to sum to 1.0.
    """
    offspring = []
    for i in range(n):
        noise = lambda: random.gauss(0.0, divergence_factor)
        s = max(0.01, parent.weight_safety  + noise())
        c = max(0.01, parent.weight_clarity + noise())
        h = max(0.01, parent.weight_human   + noise())
        s, c, h = _normalize(s, c, h)

        child = OffspringProfile(
            tag=f"child_{parent.generation}_{i:02d}",
            genesis_step=genesis_step,
            inherited_reward=parent.best_fitness,
            divergence_factor=divergence_factor,
            weight_safety=round(s, 4),
            weight_clarity=round(c, 4),
            weight_human=round(h, 4),
        )
        offspring.append(child)
    return offspring


def evaluate(child: OffspringProfile, metrics: dict) -> float:
    """
    Compute fitness for one offspring given live biometric metrics.

    metrics expected keys (all floats, 0.0–1.0 range unless noted):
      energy      — HRV-derived energy level
      risk        — EEG beta-derived risk/arousal (lower is better)
      reward_mod  — SPO2-derived reward modulation
      ethics_score — composite ethics gate (safety × clarity × human blend)

    Fitness = ethics_coherence × reward_signal × calm_factor
    where:
      ethics_coherence = weighted blend of the offspring's weight vector × ethics_score
      reward_signal    = (energy × reward_mod) clamped to [0, 1]
      calm_factor      = 1 - risk
    """
    ethics_score = float(metrics.get("ethics_score", 0.5))
    energy       = float(metrics.get("energy",       0.5))
    risk         = float(metrics.get("risk",          0.5))
    reward_mod   = float(metrics.get("reward_mod",    0.5))

    # The offspring's weight vector biases how much each ethics dimension counts.
    # Here ethics_score is a scalar — weight vector affects sensitivity to it.
    ethics_coherence = (
        child.weight_safety  * ethics_score +
        child.weight_clarity * ethics_score +
        child.weight_human   * ethics_score
    ) / 3.0

    reward_signal = min(1.0, max(0.0, energy * reward_mod))
    calm_factor   = max(0.0, 1.0 - risk)

    fitness = ethics_coherence * reward_signal * calm_factor
    child.fitness = round(fitness, 6)
    child.evaluated_at = time.time()
    return child.fitness


def select_survivor(offspring: list[OffspringProfile]) -> Optional[OffspringProfile]:
    """Return the offspring with highest fitness. Returns None if list is empty."""
    evaluated = [o for o in offspring if o.fitness is not None]
    if not evaluated:
        return None
    return max(evaluated, key=lambda o: o.fitness)  # type: ignore[arg-type]


def feedback_to_parent(winner: OffspringProfile, parent: SwarmState, learning_rate: float = 0.1) -> SwarmState:
    """
    Blend the winner's weights into the parent's weights using exponential
    moving average (learning_rate controls how fast the parent adapts).

    A conservative default of 0.1 means the parent shifts 10% toward the
    winner each generation — fast enough to learn, slow enough to stay stable.
    """
    lr = max(0.0, min(1.0, learning_rate))
    parent.weight_safety  = round((1 - lr) * parent.weight_safety  + lr * winner.weight_safety,  4)
    parent.weight_clarity = round((1 - lr) * parent.weight_clarity + lr * winner.weight_clarity, 4)
    parent.weight_human   = round((1 - lr) * parent.weight_human   + lr * winner.weight_human,   4)

    # Renormalize after blend to prevent drift
    parent.weight_safety, parent.weight_clarity, parent.weight_human = _normalize(
        parent.weight_safety, parent.weight_clarity, parent.weight_human
    )
    parent.weight_safety  = round(parent.weight_safety,  4)
    parent.weight_clarity = round(parent.weight_clarity, 4)
    parent.weight_human   = round(parent.weight_human,   4)

    if winner.fitness is not None and winner.fitness > parent.best_fitness:
        parent.best_fitness = winner.fitness

    parent.generation += 1
    parent.last_evolved_at = time.time()
    return parent
