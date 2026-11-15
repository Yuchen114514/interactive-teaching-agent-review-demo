from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from app.teaching.taxonomy import AlgorithmFamily


FORBIDDEN_PRIMER_KEYS = {"problem_rules", "fixed_solution", "fixed_steps", "fixed_diagram", "answer_template"}
DEFAULT_PRIMER_POLICY_PATH = Path(__file__).with_name("algorithm_primer_policy.json")


@dataclass(frozen=True)
class AlgorithmPrimerPolicy:
    unknown_family: str
    depth_fallbacks: dict[str, str]
    source: str = "inline"
    source_detail: str = ""

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        source: str = "inline",
        source_detail: str = "",
    ) -> "AlgorithmPrimerPolicy":
        forbidden = FORBIDDEN_PRIMER_KEYS.intersection(payload)
        if forbidden:
            raise ValueError(f"primer policy contains problem-specific runtime rules: {sorted(forbidden)}")
        return cls(
            unknown_family=str(payload["unknown_family"]),
            depth_fallbacks={
                str(key): str(value)
                for key, value in (payload.get("depth_fallbacks") or {}).items()
            },
            source=source,
            source_detail=source_detail,
        )

    def fallback_for(self, *, family: AlgorithmFamily, depth: str) -> str:
        if family.family_id == "unknown":
            return self.unknown_family
        template = self.depth_fallbacks.get(depth) or self.depth_fallbacks.get("default") or ""
        return template.format(
            family_id=family.family_id,
            family_display_name=family.display_name,
            depth=depth,
        )


def load_default_primer_policy() -> AlgorithmPrimerPolicy:
    return load_primer_policy("")


def load_primer_policy(path: str | None = None) -> AlgorithmPrimerPolicy:
    if path is not None and path.strip():
        policy_path = Path(path).expanduser()
        source = "custom_config"
    else:
        policy_path = DEFAULT_PRIMER_POLICY_PATH
        source = "bundled_config"
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    return AlgorithmPrimerPolicy.from_dict(
        payload,
        source=source,
        source_detail=policy_path.name,
    )
