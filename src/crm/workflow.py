from fastapi import HTTPException, status

from crm.models import ApprovalDecision, ApprovalStage, Role, Scenario, ScenarioStatus

EDITOR_VISIBLE_STATUSES = frozenset(
    {
        ScenarioStatus.HANDED_TO_EDITOR,
        ScenarioStatus.EDITING,
        ScenarioStatus.CLIENT_REVIEW,
        ScenarioStatus.PUBLISHED,
    }
)

ROLE_APPROVAL_STAGES: dict[Role, set[ApprovalStage]] = {
    Role.MANAGER: set(ApprovalStage),
    Role.SCENARIST: set(ApprovalStage),
    Role.EDITOR: set(ApprovalStage),
    Role.CLIENT: {
        ApprovalStage.PRE_GENERATION_CLIENT,
        ApprovalStage.FINAL_CLIENT,
    },
}


def approval_for(scenario: Scenario, stage: ApprovalStage):
    return next((item for item in scenario.approvals if item.stage == stage), None)


def is_approved(scenario: Scenario, stage: ApprovalStage) -> bool:
    approval = approval_for(scenario, stage)
    return approval is not None and approval.decision == ApprovalDecision.APPROVED


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
        return is_approved(scenario, ApprovalStage.RESPONSIBLE_REVIEW)
    if stage == ApprovalStage.SOURCE_MATERIAL:
        return bool(
            is_approved(scenario, ApprovalStage.RESPONSIBLE_REVIEW)
            and is_approved(scenario, ApprovalStage.PRE_GENERATION_CLIENT)
            and scenario.montage
            and scenario.montage.source_material_url
        )
    if stage == ApprovalStage.MONTAGE_COMPLIANCE:
        return bool(
            is_approved(scenario, ApprovalStage.RESPONSIBLE_REVIEW)
            and is_approved(scenario, ApprovalStage.PRE_GENERATION_CLIENT)
            and is_approved(scenario, ApprovalStage.SOURCE_MATERIAL)
            and scenario.montage
            and scenario.montage.ready_material_url
        )
    if stage == ApprovalStage.FINAL_CLIENT:
        return bool(
            is_approved(scenario, ApprovalStage.RESPONSIBLE_REVIEW)
            and is_approved(scenario, ApprovalStage.PRE_GENERATION_CLIENT)
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
        ApprovalStage.PRE_GENERATION_CLIENT: "Responsible review must be approved first",
        ApprovalStage.SOURCE_MATERIAL: (
            "Responsible and client script approvals plus source material are required"
        ),
        ApprovalStage.MONTAGE_COMPLIANCE: (
            "The full script/source approval chain and ready material are required"
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
        return scenario.status

    transitions = {
        (ApprovalStage.RESPONSIBLE_REVIEW, ApprovalDecision.APPROVED): ScenarioStatus.CLIENT_REVIEW,
        (ApprovalStage.RESPONSIBLE_REVIEW, ApprovalDecision.REVISION): ScenarioStatus.REVISION,
        (ApprovalStage.RESPONSIBLE_REVIEW, ApprovalDecision.REJECTED): ScenarioStatus.REVISION,
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
        (ApprovalStage.FINAL_CLIENT, ApprovalDecision.REVISION): ScenarioStatus.EDITING,
        (ApprovalStage.FINAL_CLIENT, ApprovalDecision.REJECTED): ScenarioStatus.EDITING,
    }
    return transitions[(stage, decision)]
