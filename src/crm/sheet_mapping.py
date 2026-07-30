"""Canonical mapping for the approved scenarist Google Sheets template.

The original business columns A:BN are preserved. New CRM-only workflow
columns use the previously empty BO:BY range and continue after the protected
BZ identity column. Existing source-specific mappings always take precedence.
"""

from typing import Any

CANONICAL_LAYOUT_ANCHORS = {
    "external_id": "B",
    "content.script_text": "T",
}
DISPLAY_FIELD_REPLACEMENTS = {
    "assigned_scenarist_id": "scenarist.name",
    "montage.assigned_editor_id": "montage.assigned_editor_name",
    "publication.assigned_publisher_id": "publication.assigned_publisher_name",
}

CANONICAL_WRITEBACK_COLUMN_MAP: dict[str, str] = {
    "scenario_date": "A",
    "external_id": "B",
    "research.competitor_url": "C",
    "research.competitor_category": "D",
    "scenario_type": "E",
    "visual_format": "F",
    "research.full_analysis": "G",
    "research.performance_metrics": "H",
    "research.transcription": "I",
    "research.timeline": "J",
    "research.why_viral": "K",
    "research.takeaways": "L",
    "research.improvements": "M",
    "research.replication_template": "N",
    "research.ai_analysis": "O",
    "scenarist.name": "P",
    "content.claude_context": "Q",
    "speaker": "R",
    "content.cover_text": "S",
    "content.script_text": "T",
    "content.montage_brief": "U",
    "content.scenarist_comment": "V",
    "content.hook": "W",
    "content.retention": "X",
    "content.call_to_action": "Y",
    "content.visual_notes": "Z",
    "content.score_recommendations": "AA",
    "content.ai_review": "AB",
    "approval.responsible_review.decision": "AE",
    "approval.responsible_review.comment": "AF",
    "approval.pre_generation_client.decision": "AH",
    "approval.pre_generation_client.comment": "AI",
    "montage.source_material_url": "AK",
    "deadline": "AL",
    "montage.client_brand_style": "AM",
    "montage.extra_brief": "AN",
    "montage.assigned_editor_name": "AO",
    "montage.price": "AP",
    "montage.material_status": "AQ",
    "montage.scenarist_material_comment": "AR",
    "approval.source_material.decision": "AS",
    "approval.source_material.comment": "AT",
    "montage.ready_material_url": "AU",
    "approval.montage_compliance.decision": "AV",
    "montage.ready_at": "AW",
    "montage.bot_visual_analysis": "AX",
    "montage.compliance_analysis": "AY",
    "montage.ai_analysis": "AZ",
    "montage.scenarist_revision_status": "BA",
    "montage.scenarist_revision_comment": "BB",
    "approval.final_client.decision": "BC",
    "approval.final_client.comment": "BD",
    "publication.publication_date": "BE",
    "publication.publisher_brief": "BF",
    "publication.description_dzen": "BG",
    "publication.description_youtube": "BH",
    "publication.description_tiktok": "BI",
    "publication.description_instagram": "BJ",
    "publication.is_published": "BK",
    "publication.instagram_url": "BL",
    "publication.engagement_metrics": "BM",
    "publication.publication_analysis": "BN",
    # CRM workflow extensions. BZ remains the protected crm_row_id column.
    "approval.pre_generation_client.note": "BO",
    "approval.pre_generation_client.decided_at": "BP",
    "montage.external_editor_name": "BQ",
    "montage.payment_due_date": "BR",
    "montage.editor_status": "BS",
    "montage.editor_comment": "BT",
    "montage.brief_compliance_status": "BU",
    "approval.final_client.decided_at": "BV",
    "final_revision_gate.request_comment": "BW",
    "final_revision_gate.decision": "BX",
    "final_revision_gate.manager_comment": "BY",
    "final_revision_gate.decided_at": "CA",
    "publication.ai_social_descriptions": "CB",
    "publication.leia_script": "CC",
    "publication.preparation_status": "CD",
    "publication.assigned_publisher_name": "CE",
    "publication.manager_review_decision": "CF",
    "publication.manager_review_comment": "CG",
    "publication.manager_reviewed_at": "CH",
    "publication.dzen_url": "CI",
    "publication.youtube_url": "CJ",
    "publication.tiktok_url": "CK",
    "publication.publisher_status": "CL",
    "publication.publisher_comment": "CM",
    "publication.published_at": "CN",
    "comments.latest": "CO",
    "project.name": "CP",
    "project.client_name": "CQ",
    "score": "CR",
    "approval.montage_compliance.comment": "CS",
}

MANAGED_EXTENSION_HEADERS: dict[str, str] = {
    "approval.pre_generation_client.note": "Примечание клиента",
    "approval.pre_generation_client.decided_at": "Дата решения по сценарию",
    "montage.external_editor_name": "Внешний монтажёр",
    "montage.payment_due_date": "Дата периода оплаты",
    "montage.editor_status": "Статус монтажёра",
    "montage.editor_comment": "Комментарий монтажёра",
    "montage.brief_compliance_status": "Статус проверки по ТЗ",
    "approval.final_client.decided_at": "Дата решения по монтажу",
    "final_revision_gate.request_comment": "Запрос клиента на доработку",
    "final_revision_gate.decision": "Решение менеджера по доработке",
    "final_revision_gate.manager_comment": "Ответ менеджера клиенту",
    "final_revision_gate.decided_at": "Дата решения менеджера",
    "publication.ai_social_descriptions": "ИИ-описания сетей",
    "publication.leia_script": "Сценарий от Леи",
    "publication.preparation_status": "Статус подготовки публикации",
    "publication.assigned_publisher_name": "Публицист",
    "publication.manager_review_decision": "Проверка публикации менеджером",
    "publication.manager_review_comment": "Комментарий менеджера к публикации",
    "publication.manager_reviewed_at": "Дата проверки публикации",
    "publication.dzen_url": "Ссылка Dzen",
    "publication.youtube_url": "Ссылка YouTube",
    "publication.tiktok_url": "Ссылка TikTok",
    "publication.publisher_status": "Статус публикации",
    "publication.publisher_comment": "Комментарий публициста",
    "publication.published_at": "Опубликовано в",
    "comments.latest": "Последний комментарий",
    "project.name": "Проект",
    "project.client_name": "Клиент",
    "score": "Общий балл",
    "approval.montage_compliance.comment": "Комментарий проверки монтажа",
}


def canonical_layout_enabled(source: Any) -> bool:
    """Apply defaults only to sources already anchored to the approved layout."""
    configured = getattr(source, "writeback_column_map", None) or {}
    anchors_match = all(
        _column_letters(configured.get(field)) == column
        for field, column in CANONICAL_LAYOUT_ANCHORS.items()
    )
    protected_layout_matches = (
        getattr(source, "header_row", None) == 4
        and str(getattr(source, "crm_row_id_column", "")).strip().upper() == "BZ"
    )
    return anchors_match or protected_layout_matches


def effective_writeback_column_map(source: Any) -> dict[str, int | str]:
    configured = dict(getattr(source, "writeback_column_map", None) or {})
    use_canonical_layout = canonical_layout_enabled(source)
    for identifier_field, display_field in DISPLAY_FIELD_REPLACEMENTS.items():
        identifier_column = configured.pop(identifier_field, None)
        if identifier_column is not None:
            configured.setdefault(display_field, identifier_column)
    if (
        use_canonical_layout
        and _column_letters(configured.get("montage.external_editor_name")) == "AO"
    ):
        # The legacy template used one editor cell. Keep AO for the assigned
        # employee name and preserve an external editor separately in BQ.
        configured.pop("montage.external_editor_name")
    if not use_canonical_layout:
        return _protect_runtime_identity_column(source, configured)
    configured_columns = {
        _column_letters(reference) for reference in configured.values()
    }
    defaults = {
        field: column
        for field, column in CANONICAL_WRITEBACK_COLUMN_MAP.items()
        if field not in configured and column not in configured_columns
    }
    return _protect_runtime_identity_column(source, {**defaults, **configured})


def _protect_runtime_identity_column(
    source: Any,
    mapping: dict[str, int | str],
) -> dict[str, int | str]:
    """Keep legacy identity columns separate from workflow data.

    Source creation/update validation already rejects collisions. Older
    production sources predate that validation, though, and may use one of the
    columns later assigned to a canonical CRM extension. Rehome only the
    colliding workflow fields after the current mapping so existing identity
    values are never overwritten.
    """
    identity = _column_letters(getattr(source, "crm_row_id_column", None))
    if identity is None:
        return mapping
    collisions = [
        field
        for field, reference in mapping.items()
        if _column_letters(reference) == identity
    ]
    if not collisions:
        return mapping
    protected = dict(mapping)
    occupied = {
        column
        for reference in protected.values()
        if (column := _column_letters(reference)) is not None
    }
    next_column = max(
        [_column_number(column) for column in occupied | {identity}],
        default=0,
    ) + 1
    for field in collisions:
        while _column_letters(next_column) in occupied:
            next_column += 1
        replacement = _column_letters(next_column)
        if replacement is None:
            raise ValueError("Google Sheets column limit exceeded")
        protected[field] = replacement
        occupied.add(replacement)
        next_column += 1
    return protected


def _column_letters(reference: Any) -> str | None:
    if isinstance(reference, str):
        return reference.strip().upper()
    if not isinstance(reference, int) or reference < 1:
        return None
    result = ""
    value = reference
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _column_number(reference: str) -> int:
    value = 0
    for character in reference:
        value = value * 26 + ord(character) - 64
    return value
