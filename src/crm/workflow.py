from fastapi import HTTPException, status

from crm.models import (
    ApprovalDecision,
    ApprovalStage,
    PublicationReviewDecision,
    PublisherStatus,
    Role,
    Scenario,
    ScenarioStatus,
    SourceMaterialStatus,
)

EDITOR_VISIBLE_STATUSES = frozenset(
    {
        ScenarioStatus.HANDED_TO_EDITOR,
        ScenarioStatus.EDITING,
        ScenarioStatus.CLIENT_REVIEW,
        ScenarioStatus.APPROVED,
        ScenarioStatus.PUBLISHED,
    }
)
EDITOR_MANAGER_VISIBLE_STATUSES = frozenset(
    {
        ScenarioStatus.SENT_TO_GENERATION,
        ScenarioStatus.HANDED_TO_EDITOR,
        ScenarioStatus.EDITING,
        ScenarioStatus.CLIENT_REVIEW,
        ScenarioStatus.MANAGER_REVISION_REVIEW,
        ScenarioStatus.APPROVED,
        ScenarioStatus.READY_TO_PUBLISH,
        ScenarioStatus.PUBLISHED,
    }
)
EDITOR_ACTION_STATUSES = frozenset(
    {ScenarioStatus.HANDED_TO_EDITOR, ScenarioStatus.EDITING}
)
PUBLISHER_VISIBLE_STATUSES = frozenset(
    {ScenarioStatus.READY_TO_PUBLISH, ScenarioStatus.PUBLISHED}
)
PUBLISHER_MANAGER_VISIBLE_STATUSES = frozenset(
    {
        ScenarioStatus.APPROVED,
        ScenarioStatus.READY_TO_PUBLISH,
        ScenarioStatus.PUBLISHED,
    }
)

ROLE_APPROVAL_STAGES: dict[Role, set[ApprovalStage]] = {
    Role.ADMIN: set(),
    Role.MANAGER: {ApprovalStage.RESPONSIBLE_REVIEW},
    Role.EDITOR_MANAGER: {
        ApprovalStage.SOURCE_MATERIAL,
        ApprovalStage.MONTAGE_COMPLIANCE,
    },
    Role.PUBLISHER_MANAGER: set(),
    Role.SCENARIST: set(),
    Role.EDITOR: set(),
    Role.CLIENT: {
        ApprovalStage.PRE_GENERATION_CLIENT,
        ApprovalStage.FINAL_CLIENT,
    },
    Role.PUBLISHER: set(),
}
APPROVAL_STAGE_ORDER = tuple(ApprovalStage)


def approval_for(scenario: Scenario, stage: ApprovalStage):
    return next((item for item in scenario.approvals if item.stage == stage), None)


def is_approved(scenario: Scenario, stage: ApprovalStage) -> bool:
    approval = approval_for(scenario, stage)
    return approval is not None and approval.decision == ApprovalDecision.APPROVED


def _reset_approval(approval) -> None:
    approval.decision = ApprovalDecision.PENDING
    approval.decided_by_id = None
    approval.decided_at = None


def _unpublish(scenario: Scenario) -> None:
    if scenario.publication is not None:
        scenario.publication.is_published = False
        scenario.publication.published_at = None
        scenario.publication.manager_review_decision = PublicationReviewDecision.PENDING
        scenario.publication.manager_review_comment = None
        scenario.publication.manager_reviewed_by_id = None
        scenario.publication.manager_reviewed_at = None
        scenario.publication.assigned_publisher_id = None
        scenario.publication.publisher_status = PublisherStatus.PENDING
        scenario.publication.publisher_comment = None


def reset_downstream_approvals(scenario: Scenario, stage: ApprovalStage) -> None:
    """Invalidate decisions that depended on a now non-approved stage."""
    stage_index = APPROVAL_STAGE_ORDER.index(stage)
    downstream = set(APPROVAL_STAGE_ORDER[stage_index + 1 :])
    for approval in scenario.approvals:
        if approval.stage in downstream:
            _reset_approval(approval)
    _unpublish(scenario)


def reset_approvals_from(scenario: Scenario, stage: ApprovalStage) -> None:
    """Invalidate a stage and every later decision after its source data changed."""
    stage_index = APPROVAL_STAGE_ORDER.index(stage)
    invalidated = set(APPROVAL_STAGE_ORDER[stage_index:])
    for approval in scenario.approvals:
        if approval.stage in invalidated:
            _reset_approval(approval)
    _unpublish(scenario)


def require_stage_role(role: Role, stage: ApprovalStage) -> None:
    if stage not in ROLE_APPROVAL_STAGES.get(role, set()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role {role.value} cannot decide {stage.value}",
        )


def stage_prerequisites_met(scenario: Scenario, stage: ApprovalStage) -> bool:
    if stage == ApprovalStage.RESPONSIBLE_REVIEW:
        return bool(scenario.content and scenario.content.script_text)
    if stage == ApprovalStage.PRE_GENERATION_CLIENT:
        return bool(
            scenario.content
            and scenario.content.script_text
            and is_approved(scenario, ApprovalStage.RESPONSIBLE_REVIEW)
        )
    if stage == ApprovalStage.SOURCE_MATERIAL:
        return bool(
            is_approved(scenario, ApprovalStage.PRE_GENERATION_CLIENT)
            and scenario.montage
            and (
                getattr(scenario.montage, "material_status", None)
                == SourceMaterialStatus.READY_FOR_REVIEW
                or is_approved(scenario, ApprovalStage.SOURCE_MATERIAL)
            )
        )
    if stage == ApprovalStage.MONTAGE_COMPLIANCE:
        return bool(
            is_approved(scenario, ApprovalStage.PRE_GENERATION_CLIENT)
            and is_approved(scenario, ApprovalStage.SOURCE_MATERIAL)
            and scenario.montage
            and scenario.montage.ready_material_url
        )
    if stage == ApprovalStage.FINAL_CLIENT:
        return bool(
            is_approved(scenario, ApprovalStage.PRE_GENERATION_CLIENT)
            and is_approved(scenario, ApprovalStage.SOURCE_MATERIAL)
            and is_approved(scenario, ApprovalStage.MONTAGE_COMPLIANCE)
            and scenario.montage
            and scenario.montage.ready_material_url
        )
    return False


def require_stage_prerequisites(scenario: Scenario, stage: ApprovalStage) -> None:
    if stage_prerequisites_met(scenario, stage):
        return
    messages = {
        ApprovalStage.RESPONSIBLE_REVIEW: "Script text is required before responsible review",
        ApprovalStage.PRE_GENERATION_CLIENT: (
            "Responsible manager approval is required before client review"
        ),
        ApprovalStage.SOURCE_MATERIAL: (
            "The scenarist must submit source material for review"
        ),
        ApprovalStage.MONTAGE_COMPLIANCE: (
            "The client/source approval chain and ready material are required"
        ),
        ApprovalStage.FINAL_CLIENT: (
            "The full script, source and montage approval chain is required"
        ),
    }
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=messages[stage],
    )


def publication_section_available(scenario: Scenario) -> bool:
    """Keep an already-used publication section available after unpublishing."""
    publication = scenario.publication
    was_published = bool(
        publication
        and (publication.is_published or publication.first_published_at is not None)
    )
    final_chain_complete = is_approved(
        scenario, ApprovalStage.FINAL_CLIENT
    ) and stage_prerequisites_met(scenario, ApprovalStage.FINAL_CLIENT)
    return final_chain_complete or was_published


def status_after_unpublishing(scenario: Scenario) -> ScenarioStatus:
    """Restore the status implied by the latest non-pending workflow decision."""
    for stage in reversed(list(ApprovalStage)):
        approval = approval_for(scenario, stage)
        if approval is not None and approval.decision != ApprovalDecision.PENDING:
            return status_after_decision(scenario, stage, approval.decision)
    if scenario.content and scenario.content.script_text:
        return ScenarioStatus.IN_REVIEW
    return ScenarioStatus.DRAFT


def status_after_decision(
    scenario: Scenario,
    stage: ApprovalStage,
    decision: ApprovalDecision,
) -> ScenarioStatus:
    if decision == ApprovalDecision.PENDING:
        return status_after_unpublishing(scenario)

    transitions = {
        (ApprovalStage.RESPONSIBLE_REVIEW, ApprovalDecision.APPROVED): ScenarioStatus.CLIENT_REVIEW,
        (ApprovalStage.RESPONSIBLE_REVIEW, ApprovalDecision.REVISION): ScenarioStatus.REVISION,
        (ApprovalStage.RESPONSIBLE_REVIEW, ApprovalDecision.REJECTED): ScenarioStatus.REJECTED,
        (
            ApprovalStage.PRE_GENERATION_CLIENT,
            ApprovalDecision.APPROVED,
        ): ScenarioStatus.SENT_TO_GENERATION,
        (ApprovalStage.PRE_GENERATION_CLIENT, ApprovalDecision.REVISION): ScenarioStatus.REVISION,
        (ApprovalStage.PRE_GENERATION_CLIENT, ApprovalDecision.REJECTED): ScenarioStatus.REVISION,
        (ApprovalStage.SOURCE_MATERIAL, ApprovalDecision.APPROVED): (
            ScenarioStatus.HANDED_TO_EDITOR
            if scenario.montage and scenario.montage.assigned_editor_id
            else ScenarioStatus.SENT_TO_GENERATION
        ),
        (
            ApprovalStage.SOURCE_MATERIAL,
            ApprovalDecision.REVISION,
        ): ScenarioStatus.SENT_TO_GENERATION,
        (
            ApprovalStage.SOURCE_MATERIAL,
            ApprovalDecision.REJECTED,
        ): ScenarioStatus.SENT_TO_GENERATION,
        (ApprovalStage.MONTAGE_COMPLIANCE, ApprovalDecision.APPROVED): ScenarioStatus.CLIENT_REVIEW,
        (ApprovalStage.MONTAGE_COMPLIANCE, ApprovalDecision.REVISION): ScenarioStatus.EDITING,
        (ApprovalStage.MONTAGE_COMPLIANCE, ApprovalDecision.REJECTED): ScenarioStatus.EDITING,
        (ApprovalStage.FINAL_CLIENT, ApprovalDecision.APPROVED): ScenarioStatus.APPROVED,
        (
            ApprovalStage.FINAL_CLIENT,
            ApprovalDecision.REVISION,
        ): ScenarioStatus.MANAGER_REVISION_REVIEW,
        (
            ApprovalStage.FINAL_CLIENT,
            ApprovalDecision.REJECTED,
        ): ScenarioStatus.MANAGER_REVISION_REVIEW,
    }
    return transitions[(stage, decision)]
