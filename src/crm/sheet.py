from dataclasses import dataclass
from typing import Literal

from crm.models import ApprovalStage, Role
from crm.schemas import EditorStatus, ScenarioRead, SheetColumnRead

EditorKind = Literal["inline", "detail", "readonly"]


@dataclass(frozen=True)
class SheetFieldSpec:
    field: str
    label: str
    group: str
    roles: frozenset[Role]
    editor: EditorKind = "readonly"


ALL_ROLES = frozenset(Role)
INTERNAL_ROLES = frozenset({Role.MANAGER, Role.SCENARIST, Role.EDITOR})
PRODUCTION_ROLES = INTERNAL_ROLES


def field(
    name: str,
    label: str,
    group: str,
    roles: frozenset[Role] = ALL_ROLES,
    editor: EditorKind = "readonly",
) -> SheetFieldSpec:
    return SheetFieldSpec(name, label, group, roles, editor)


SHEET_FIELDS = (
    field("scenario_date", "Дата", "Основное", editor="inline"),
    field("external_id", "ID", "Основное"),
    field("project.name", "Проект", "Основное"),
    field("project.client_name", "Клиент", "Основное", INTERNAL_ROLES),
    field("scenarist.name", "Сценарист", "Основное", ALL_ROLES),
    field("assigned_scenarist_id", "Назначить сценариста", "Основное", ALL_ROLES, "inline"),
    field("speaker", "Спикер", "Основное", editor="inline"),
    field("deadline", "Дедлайн", "Основное", PRODUCTION_ROLES, "inline"),
    field("scenario_type", "Тип сценария", "Исследование", INTERNAL_ROLES, "inline"),
    field("visual_format", "Формат визуала", "Исследование", INTERNAL_ROLES, "inline"),
    field("score", "Общий балл", "Исследование", INTERNAL_ROLES, "inline"),
    field(
        "research.competitor_url", "Ссылка на конкурента", "Исследование", INTERNAL_ROLES, "inline"
    ),
    field(
        "research.competitor_category",
        "Категория конкурента",
        "Исследование",
        INTERNAL_ROLES,
        "inline",
    ),
    field("research.full_analysis", "Анализ из бота", "Исследование", INTERNAL_ROLES, "detail"),
    field(
        "research.performance_metrics",
        "Просмотры / реакции / вирусность",
        "Исследование",
        INTERNAL_ROLES,
        "detail",
    ),
    field("research.transcription", "Транскрибация", "Исследование", INTERNAL_ROLES, "detail"),
    field("research.timeline", "Таймлайн", "Исследование", INTERNAL_ROLES, "detail"),
    field("research.why_viral", "Почему залетело", "Исследование", INTERNAL_ROLES, "detail"),
    field("research.takeaways", "Забрать себе", "Исследование", INTERNAL_ROLES, "detail"),
    field("research.improvements", "Улучшить", "Исследование", INTERNAL_ROLES, "detail"),
    field(
        "research.replication_template",
        "Шаблон репликации",
        "Исследование",
        INTERNAL_ROLES,
        "detail",
    ),
    field("research.ai_analysis", "ИИ-анализ", "Исследование", INTERNAL_ROLES, "detail"),
    field("content.claude_context", "Контекст Claude", "Сценарий", INTERNAL_ROLES, "detail"),
    field("content.cover_text", "Текст на обложке", "Сценарий", ALL_ROLES, "inline"),
    field("content.script_text", "Сценарий", "Сценарий", ALL_ROLES, "detail"),
    field("content.montage_brief", "ТЗ для монтажа", "Сценарий", PRODUCTION_ROLES, "detail"),
    field(
        "content.scenarist_comment",
        "Комментарий сценариста",
        "Сценарий",
        PRODUCTION_ROLES,
        "detail",
    ),
    field("content.hook", "Хук", "Сценарий", INTERNAL_ROLES, "inline"),
    field("content.retention", "Удержание", "Сценарий", INTERNAL_ROLES, "inline"),
    field("content.call_to_action", "Призыв к действию", "Сценарий", INTERNAL_ROLES, "inline"),
    field("content.visual_notes", "Визуальный формат", "Сценарий", INTERNAL_ROLES, "detail"),
    field("content.score_recommendations", "Рекомендации", "Сценарий", INTERNAL_ROLES, "detail"),
    field("content.ai_review", "ИИ-проверка сценария", "Сценарий", INTERNAL_ROLES, "detail"),
    field(
        "approval.responsible_review.decision",
        "Одобрение ответственного",
        "Согласование",
        INTERNAL_ROLES,
        "inline",
    ),
    field(
        "approval.responsible_review.comment",
        "Комментарий ответственного",
        "Согласование",
        INTERNAL_ROLES,
        "inline",
    ),
    field(
        "approval.pre_generation_client.decision",
        "Одобрение сценария клиентом",
        "Согласование",
        frozenset({Role.CLIENT, Role.MANAGER, Role.SCENARIST}),
        "inline",
    ),
    field(
        "approval.pre_generation_client.comment",
        "Комментарий клиента",
        "Согласование",
        frozenset({Role.CLIENT, Role.MANAGER, Role.SCENARIST}),
        "inline",
    ),
    field(
        "approval.pre_generation_client.note",
        "Примечание клиента",
        "Согласование",
        frozenset({Role.CLIENT, Role.MANAGER, Role.SCENARIST}),
        "inline",
    ),
    field(
        "approval.pre_generation_client.decided_at",
        "Дата решения по сценарию",
        "Согласование",
        frozenset({Role.CLIENT, Role.MANAGER, Role.SCENARIST}),
    ),
    field(
        "montage.source_material_url", "Исходник и обложка", "Монтаж", PRODUCTION_ROLES, "inline"
    ),
    field("montage.client_brand_style", "Фирменный стиль", "Монтаж", PRODUCTION_ROLES, "detail"),
    field("montage.extra_brief", "Дополнительное ТЗ", "Монтаж", PRODUCTION_ROLES, "detail"),
    field(
        "approval.source_material.decision",
        "Одобрение исходника",
        "Монтаж",
        PRODUCTION_ROLES,
        "inline",
    ),
    field(
        "approval.source_material.comment",
        "Комментарий к исходнику",
        "Монтаж",
        PRODUCTION_ROLES,
        "inline",
    ),
    field("montage.assigned_editor_id", "Монтажёр", "Монтаж", PRODUCTION_ROLES, "inline"),
    field("montage.assigned_editor_name", "Имя монтажёра", "Монтаж", ALL_ROLES),
    field("montage.external_editor_name", "Внешний монтажёр", "Монтаж", PRODUCTION_ROLES, "inline"),
    field("montage.price", "Цена монтажа", "Монтаж", PRODUCTION_ROLES, "inline"),
    field("montage.payment_due_date", "Период оплаты", "Монтаж", PRODUCTION_ROLES, "inline"),
    field("montage.material_status", "Статус материалов", "Монтаж", PRODUCTION_ROLES, "inline"),
    field(
        "montage.scenarist_material_comment",
        "Комментарий сценариста к материалам",
        "Монтаж",
        PRODUCTION_ROLES,
        "detail",
    ),
    field(
        "montage.ready_material_url",
        "Готовый материал",
        "Результат монтажа",
        frozenset({Role.MANAGER, Role.SCENARIST, Role.EDITOR, Role.CLIENT}),
        "inline",
    ),
    field(
        "montage.editor_status", "Статус монтажёра", "Результат монтажа", PRODUCTION_ROLES, "inline"
    ),
    field(
        "montage.editor_comment",
        "Комментарий монтажёра",
        "Результат монтажа",
        PRODUCTION_ROLES,
        "inline",
    ),
    field(
        "montage.ready_at", "Дата готового монтажа", "Результат монтажа", PRODUCTION_ROLES, "inline"
    ),
    field(
        "montage.brief_compliance_status",
        "Статус проверки по ТЗ",
        "Проверка",
        PRODUCTION_ROLES,
        "inline",
    ),
    field(
        "montage.bot_visual_analysis",
        "Раскладка бота-анализатора",
        "Проверка",
        PRODUCTION_ROLES,
        "detail",
    ),
    field(
        "montage.compliance_analysis",
        "Анализ соответствия",
        "Проверка",
        PRODUCTION_ROLES,
        "detail",
    ),
    field(
        "montage.ai_analysis", "ИИ-анализ монтажа", "Проверка", PRODUCTION_ROLES, "detail"
    ),
    field(
        "montage.scenarist_revision_status",
        "Исправление сценариста",
        "Проверка",
        PRODUCTION_ROLES,
        "inline",
    ),
    field(
        "montage.scenarist_revision_comment",
        "Комментарий сценариста к исправлению",
        "Проверка",
        PRODUCTION_ROLES,
        "detail",
    ),
    field(
        "approval.montage_compliance.decision",
        "Проверка монтажа по ТЗ",
        "Проверка",
        PRODUCTION_ROLES,
        "inline",
    ),
    field(
        "approval.montage_compliance.comment",
        "Комментарий менеджера",
        "Проверка",
        PRODUCTION_ROLES,
        "inline",
    ),
    field(
        "approval.final_client.decision",
        "Одобрение готового клиентом",
        "Проверка",
        frozenset({Role.CLIENT, Role.MANAGER, Role.SCENARIST}),
        "inline",
    ),
    field(
        "approval.final_client.comment",
        "Комментарий клиента к монтажу",
        "Проверка",
        frozenset({Role.CLIENT, Role.MANAGER, Role.SCENARIST}),
        "inline",
    ),
    field(
        "approval.final_client.decided_at",
        "Дата решения по монтажу",
        "Проверка",
        frozenset({Role.CLIENT, Role.MANAGER, Role.SCENARIST}),
    ),
    field(
        "publication.publication_date",
        "Дата публикации",
        "Публикация",
        frozenset({Role.CLIENT, Role.MANAGER, Role.SCENARIST}),
        "inline",
    ),
    field(
        "publication.publisher_brief", "ТЗ для публициста", "Публикация", INTERNAL_ROLES, "detail"
    ),
    field(
        "publication.description_dzen",
        "Описание Дзен",
        "Публикация",
        frozenset({Role.CLIENT, Role.MANAGER, Role.SCENARIST}),
        "detail",
    ),
    field(
        "publication.description_youtube",
        "Описание YouTube",
        "Публикация",
        frozenset({Role.CLIENT, Role.MANAGER, Role.SCENARIST}),
        "detail",
    ),
    field(
        "publication.description_tiktok",
        "Описание TikTok",
        "Публикация",
        frozenset({Role.CLIENT, Role.MANAGER, Role.SCENARIST}),
        "detail",
    ),
    field(
        "publication.description_instagram",
        "Описание Instagram",
        "Публикация",
        frozenset({Role.CLIENT, Role.MANAGER, Role.SCENARIST}),
        "detail",
    ),
    field(
        "publication.is_published",
        "Опубликовано",
        "Публикация",
        frozenset({Role.CLIENT, Role.MANAGER, Role.SCENARIST}),
        "inline",
    ),
    field(
        "publication.instagram_url",
        "Ссылка Instagram",
        "Публикация",
        frozenset({Role.CLIENT, Role.MANAGER, Role.SCENARIST}),
        "inline",
    ),
    field(
        "publication.engagement_metrics",
        "Лайки / просмотры",
        "Публикация",
        frozenset({Role.CLIENT, Role.MANAGER, Role.SCENARIST}),
        "detail",
    ),
    field(
        "publication.publication_analysis",
        "Анализ публикации",
        "Публикация",
        INTERNAL_ROLES,
        "detail",
    ),
    field(
        "publication.ai_social_descriptions",
        "ИИ-описания сетей",
        "Публикация",
        INTERNAL_ROLES,
        "detail",
    ),
    field(
        "publication.leia_script",
        "Сценарий от Леи",
        "Публикация",
        INTERNAL_ROLES,
        "detail",
    ),
)

SHEET_FIELD_MAP = {item.field: item for item in SHEET_FIELDS}

CLIENT_SHEET_FIELD_NAMES = (
    "scenario_date",
    "external_id",
    "speaker",
    "content.script_text",
    "approval.pre_generation_client.decision",
    "approval.pre_generation_client.comment",
    "approval.pre_generation_client.note",
    "montage.ready_material_url",
    "approval.final_client.decision",
    "approval.final_client.comment",
    "publication.description_dzen",
    "publication.description_youtube",
    "publication.description_tiktok",
    "publication.description_instagram",
    "publication.publication_date",
    "publication.is_published",
)


def fields_for_role(role: Role) -> tuple[SheetFieldSpec, ...]:
    if role == Role.CLIENT:
        return tuple(SHEET_FIELD_MAP[name] for name in CLIENT_SHEET_FIELD_NAMES)
    return SHEET_FIELDS


def columns_for_role(role: Role) -> list[SheetColumnRead]:
    return [
        SheetColumnRead(
            field=item.field,
            label=item.label,
            group=item.group,
            editor=item.editor,
            allowed_values=(
                [status.value for status in EditorStatus]
                if item.field == "montage.editor_status"
                else None
            ),
        )
        for item in fields_for_role(role)
    ]


def _nested_value(value, path: str):
    current = value
    for part in path.split("."):
        if current is None:
            return None
        current = getattr(current, part, None)
    return current


def values_for_role(scenario: ScenarioRead, role: Role) -> dict[str, object]:
    values: dict[str, object] = {}
    approvals = {item.stage.value: item for item in scenario.approvals}
    for item in fields_for_role(role):
        if item.field.startswith("approval."):
            _, stage, attribute = item.field.split(".")
            value = getattr(approvals.get(stage), attribute, None)
            if hasattr(value, "value"):
                value = value.value
        else:
            value = _nested_value(scenario, item.field)
        if item.editor == "detail" and isinstance(value, str) and len(value) > 240:
            value = f"{value[:237]}…"
        values[item.field] = value
    return values


def editable_fields_for_role(scenario: ScenarioRead, role: Role) -> list[str]:
    if role == Role.CLIENT:
        editable: set[str] = set()
        for stage in scenario.available_approval_stages:
            editable.add(f"approval.{stage.value}.decision")
            editable.add(f"approval.{stage.value}.comment")
            if stage == ApprovalStage.PRE_GENERATION_CLIENT:
                editable.add("approval.pre_generation_client.note")
        return sorted(editable)

    editable = set()
    available_approval_stages = set(scenario.available_approval_stages)
    available_sections = set(scenario.available_sections)
    for item in SHEET_FIELDS:
        if item.editor == "readonly":
            continue
        if item.field.startswith("approval."):
            _, stage_value, _ = item.field.split(".")
            if ApprovalStage(stage_value) not in available_approval_stages:
                continue
        elif item.field.startswith("montage.") and "montage" not in available_sections:
            continue
        elif item.field.startswith("publication.") and "publication" not in available_sections:
            continue
        editable.add(item.field)

    return sorted(editable)
