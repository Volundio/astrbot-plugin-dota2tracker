"""Port of Koishi's ``Random`` (package ``inaba``) and the seed hash helper.

The original plugin builds deterministic generators with
``new Random(() => seed)`` where ``seed`` is a constant derived from
``enhancedSimpleHashToSeed``. Every call therefore receives the *same* value;
reproducing the exact algorithms keeps anonymous profiles, hero picks and
broadcast comments identical to the Koishi version.
"""

from __future__ import annotations

import base64
import math
import random as _random
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

from .jsnum import js_object_key_order

T = TypeVar("T")

__all__ = ["KoishiRandom", "enhanced_simple_hash_to_seed", "seeded_random"]


def enhanced_simple_hash_to_seed(input_string: str) -> float:
    """Port of ``enhancedSimpleHashToSeed``: deterministic value in ``[0, 1)``."""
    try:
        raw = input_string.encode("latin-1")
    except UnicodeEncodeError:
        # ``btoa`` throws for non Latin-1 input; UTF-8 keeps the hash usable.
        raw = input_string.encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    total = 0
    complex_factor = 1
    for char in encoded:
        total += ord(char) * complex_factor
        complex_factor += 1
        total %= 9973
    total = ((total % 9973) * (total % 9973)) % 9973
    return (total % 1000) / 1000


class KoishiRandom:
    """Behaviourally identical clone of Koishi's ``Random`` class."""

    def __init__(self, get: Callable[[], float] | None = None) -> None:
        self._get = get or _random.random

    def value(self) -> float:
        return self._get()

    def bool(self, probability: float) -> bool:
        if probability >= 1:
            return True
        if probability <= 0:
            return False
        return self._get() < probability

    def real(self, *args: float) -> float:
        lower = args[0] if len(args) > 1 else 0
        upper = args[-1]
        return self._get() * (upper - lower) + lower

    def int(self, *args: float) -> int:
        return math.floor(self.real(*args))

    def splice(self, source: list[T]) -> T | None:
        index = math.floor(self._get() * len(source))
        if 0 <= index < len(source):
            return source.pop(index)
        return None

    def pick(self, source: Sequence[T], count: int | None = None) -> Any:
        if count is None:
            picked = self.pick(source, 1)
            return picked[0] if picked else None
        copy = list(source)
        result: list[T] = []
        count = min(len(copy), count)
        for _ in range(count):
            result.append(self.splice(copy))  # type: ignore[arg-type]
        return result

    def shuffle(self, source: Sequence[T]) -> list[T]:
        return self.pick(source, len(source))

    def weighted_pick(self, weights: Mapping[Any, float]) -> Any:
        """Iterate keys in JavaScript object order; ``None`` mirrors ``undefined``."""
        ordered = js_object_key_order(list(weights.keys()))
        total = 0.0
        for key in ordered:
            total += weights[key]
        pointer = self._get() * total
        counter = 0.0
        for key in ordered:
            counter += weights[key]
            if pointer < counter:
                return key
        return None


def seeded_random(seed_text: str) -> KoishiRandom:
    """``new Random(() => enhancedSimpleHashToSeed(seed_text))``."""
    seed = enhanced_simple_hash_to_seed(seed_text)
    return KoishiRandom(lambda: seed)
