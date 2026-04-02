"""
DAG Resolver Adapter

Topological sort of plan graph into parallel execution levels
using Kahn's algorithm.

Reference: LLD.md Section 6.7
"""

from __future__ import annotations

from collections import defaultdict, deque

from shared.schemas.plan import PlanStep

from ..domain.models import CycleDetectedError


class DAGResolver:
    """Resolve plan graph into parallel execution levels."""

    def resolve(self, graph: list[PlanStep]) -> list[list[PlanStep]]:
        """Group steps into execution levels by dependency order.

        Returns list of levels. Each level contains independent steps
        that can run in parallel via asyncio.gather().

        Args:
            graph: List of PlanStep with `step` and `after` fields.

        Returns:
            List of levels, each a list of PlanSteps.

        Raises:
            ValueError: If graph is empty.
            CycleDetectedError: If circular dependencies exist.
        """
        if not graph:
            raise ValueError("Plan graph must contain at least one step")

        step_map: dict[int, PlanStep] = {s.step: s for s in graph}
        in_degree: dict[int, int] = {s.step: 0 for s in graph}
        dependents: dict[int, list[int]] = defaultdict(list)

        for s in graph:
            for dep in s.after:
                if dep in step_map:
                    in_degree[s.step] += 1
                    dependents[dep].append(s.step)

        queue: deque[int] = deque(sid for sid, deg in in_degree.items() if deg == 0)
        levels: list[list[PlanStep]] = []
        processed = 0

        while queue:
            level_ids = list(queue)
            queue.clear()
            level = [step_map[sid] for sid in level_ids]
            levels.append(level)
            processed += len(level)

            for sid in level_ids:
                for dep_id in dependents[sid]:
                    in_degree[dep_id] -= 1
                    if in_degree[dep_id] == 0:
                        queue.append(dep_id)

        if processed != len(graph):
            raise CycleDetectedError(f"Processed {processed}/{len(graph)} steps")

        return levels
