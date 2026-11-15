from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


FORBIDDEN_RULE_KEYS = {"problem_rules", "fixed_solution", "fixed_steps", "fixed_diagram", "answer_template"}
DEFAULT_TAXONOMY_PATH = Path(__file__).with_name("algorithm_taxonomy.json")


@dataclass(frozen=True)
class AlgorithmFamily:
    family_id: str
    display_name: str
    description: str
    teaching_dimensions: tuple[str, ...]
    classification_evidence: tuple[tuple[str, ...], ...]
    primer_guidance: dict[str, str]


class AlgorithmTaxonomy:
    def __init__(
        self,
        families: list[AlgorithmFamily],
        *,
        source: str = "inline",
        source_detail: str = "",
    ):
        self._families = {family.family_id: family for family in families}
        self.source = source
        self.source_detail = source_detail

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        source: str = "inline",
        source_detail: str = "",
    ) -> "AlgorithmTaxonomy":
        families: list[AlgorithmFamily] = []
        for raw_family in payload.get("families", []):
            forbidden = FORBIDDEN_RULE_KEYS.intersection(raw_family)
            if forbidden:
                raise ValueError(f"taxonomy contains problem-specific runtime rules: {sorted(forbidden)}")

            families.append(
                AlgorithmFamily(
                    family_id=str(raw_family["family_id"]),
                    display_name=str(raw_family["display_name"]),
                    description=str(raw_family.get("description") or ""),
                    teaching_dimensions=tuple(str(item) for item in raw_family.get("teaching_dimensions", [])),
                    classification_evidence=tuple(
                        tuple(str(marker) for marker in evidence_group)
                        for evidence_group in raw_family.get("classification_evidence", [])
                    ),
                    primer_guidance={
                        str(key): str(value) for key, value in (raw_family.get("primer_guidance") or {}).items()
                    },
                )
            )
        return cls(families, source=source, source_detail=source_detail)

    def get_family(self, family_id: str) -> AlgorithmFamily:
        return self._families[family_id]

    def family_ids(self) -> list[str]:
        return sorted(self._families)

    def classify_message(self, message: str) -> AlgorithmFamily:
        normalized = message.lower()
        for family in self._families.values():
            if family.family_id == "unknown":
                continue
            if any(
                all(marker.lower() in normalized for marker in evidence_group)
                for evidence_group in family.classification_evidence
            ):
                return family
        return self.get_family("unknown")


def load_default_taxonomy() -> AlgorithmTaxonomy:
    return load_taxonomy("")


def load_taxonomy(path: str | None = None) -> AlgorithmTaxonomy:
    if path is not None and path.strip():
        taxonomy_path = Path(path).expanduser()
        source = "custom_config"
    else:
        taxonomy_path = DEFAULT_TAXONOMY_PATH
        source = "bundled_config"
    payload = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    return AlgorithmTaxonomy.from_dict(
        payload,
        source=source,
        source_detail=taxonomy_path.name,
    )
