from __future__ import annotations

from typing import Any

from app.diagram_planner import plan_diagram_teaching_bundle
from app.diagram_protocol import DiagramPlanIntent, DiagramPlanResult, DiagramTeachingBundleModel, DiagramVerificationResult
from app.diagram_verifier import verify_diagram_teaching_bundle


def diagram_plan_tool(intent: DiagramPlanIntent) -> DiagramPlanResult:
    bundle = plan_diagram_teaching_bundle(
        user_message=intent.user_message,
        assistant_message=intent.assistant_message,
    )
    if bundle is None:
        return DiagramPlanResult(
            bundle=None,
            verification=DiagramVerificationResult(ok=False, reason="planner_returned_none"),
        )

    verification = verify_diagram_teaching_bundle(bundle=bundle, session_id=intent.session_id)
    return DiagramPlanResult(
        bundle=DiagramTeachingBundleModel.model_validate(bundle),
        verification=DiagramVerificationResult.model_validate(verification),
    )


def diagram_verify_tool(*, session_id: str, bundle: dict[str, Any]) -> DiagramVerificationResult:
    return DiagramVerificationResult.model_validate(
        verify_diagram_teaching_bundle(bundle=bundle, session_id=session_id)
    )
