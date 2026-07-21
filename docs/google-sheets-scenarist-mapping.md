# Google Sheets → CRM: сценарист

Статус: утверждённая основа импорта. Источник читается только через отдельный адаптер; CRM не
записывает изменения обратно в рабочие Google Sheets.

## Идентификация источника

Каждая строка определяется составным ключом:

- `source_sheet_id` — ID Google Spreadsheet;
- `source_tab` — название вкладки/проекта;
- `external_id` — значение колонки `ID`;
- `source_row` — номер строки для диагностики;
- `source_checksum` будет добавлен в импортёр для определения изменений.

Один `ID` нельзя считать глобально уникальным без Spreadsheet и вкладки.

## Основной сценарий

| Google Sheets | Backend |
|---|---|
| дата | `scenarios.scenario_date` |
| ID | `scenarios.external_id` |
| Тип сценария | `scenarios.scenario_type` |
| Формат визуала / Визуальный формат | `scenarios.visual_format`, `scenario_content.visual_notes` |
| Сценарист | `scenarios.assigned_scenarist_id` через сопоставление пользователя |
| Спикер | `scenarios.speaker` |
| Дата дедлайна | `scenarios.deadline` |
| Общий бал / рекомендации | `scenarios.score`, `scenario_content.score_recommendations` |

Проект и клиент определяются вкладкой и конфигурацией соответствий, затем связываются через
`projects` и `clients`.

## Исследование конкурента

| Google Sheets | Backend |
|---|---|
| Ссылка на конкурента / перераспаковку | `scenario_research.competitor_url` |
| Категория конкурента | `scenario_research.competitor_category` |
| Анализ из бота | `scenario_research.full_analysis` |
| Просмотры / лайки / комментарии / хештеги / вирусность | `scenario_research.performance_metrics` |
| Транскрибация | `scenario_research.transcription` |
| Таймлайн | `scenario_research.timeline` |
| Почему залетело | `scenario_research.why_viral` |
| Забрать себе | `scenario_research.takeaways` |
| Улучшить | `scenario_research.improvements` |
| Шаблон для репликации | `scenario_research.replication_template` |
| ИИ на этапе анализа | `scenario_research.ai_analysis` |

## Контент сценариста

| Google Sheets | Backend |
|---|---|
| Место для информации с Claude | `scenario_content.claude_context` |
| Текст на обложке | `scenario_content.cover_text` |
| Сценарий | `scenario_content.script_text` |
| ТЗ для монтажа | `scenario_content.montage_brief` |
| Комментарий сценариста | `scenario_content.scenarist_comment` |
| Хук | `scenario_content.hook` |
| Удержание | `scenario_content.retention` |
| Призыв к действию | `scenario_content.call_to_action` |
| Общий бал / рекомендации | `scenario_content.score_recommendations` |
| ИИ на этапе проверки | `scenario_content.ai_review` |

## Согласования

Повторяющиеся колонки `Исправление`, `Комментарий`, `ИИ` и `Одобрение клиента` не объединяются.
Они сохраняются с указанием этапа:

- `responsible_review` — проверка ответственного лица;
- `pre_generation_client` — согласование сценария клиентом;
- `source_material` — согласование исходника;
- `montage_compliance` — проверка монтажа по ТЗ;
- `final_client` — финальное согласование клиента.

Решения: `pending`, `approved`, `revision`. Комментарии дополнительно сохраняются в
`scenario_comments` с автором и этапом.

## Следующие модули

## Передача исходников и монтаж

| Google Sheets | Backend |
|---|---|
| Отправка на генерацию | `scenarios.status = sent_to_generation` |
| Ссылка с исходником | `montage_tasks.source_material_url` |
| Дата дедлайна | `scenarios.deadline` |
| Фирменный стиль | `montage_tasks.client_brand_style` |
| ТЗ монтажа доп | `montage_tasks.extra_brief` |
| Монтажёр | `montage_tasks.assigned_editor_id` / `external_editor_name` |
| Цена | `montage_tasks.price` |
| Отправить материал | `montage_tasks.material_status` |
| Комментарий сценариста | `montage_tasks.scenarist_material_comment` |
| Одобрение исходника менеджером | approval `source_material` |
| Ссылка с готовым | `montage_tasks.ready_material_url` |
| Проверка монтажа по ТЗ | approval `montage_compliance` + `brief_compliance_status` |
| Дата готового монтажа | `montage_tasks.ready_at` |
| Раскладка бота | `montage_tasks.bot_visual_analysis` |
| Анализ соответствия | `montage_tasks.compliance_analysis` |
| ИИ на этапе монтажа | `montage_tasks.ai_analysis` |
| Исправление сценариста | `montage_tasks.scenarist_revision_status` |
| Комментарий сценариста после монтажа | `montage_tasks.scenarist_revision_comment` |

## Публикация

| Google Sheets | Backend |
|---|---|
| Финальное одобрение клиента | approval `final_client` |
| Дата публикации | `publications.publication_date` |
| ТЗ для публициста | `publications.publisher_brief` |
| Описание Дзен / YouTube / TikTok / Instagram | соответствующие `publications.description_*` |
| Опубликовано | `publications.is_published` |
| Ссылка Instagram | `publications.instagram_url` |
| Лайки / просмотры | `publications.engagement_metrics` |
| Анализ публикации | `publications.publication_analysis` |
| ИИ-описания сетей | `publications.ai_social_descriptions` |
| Сценарий от Леи | `publications.leia_script` |

Разделение по сущностям предотвращает конфликт повторяющихся колонок `ИИ`, `Исправление`,
`Комментарий` и `Одобрение клиента`.
