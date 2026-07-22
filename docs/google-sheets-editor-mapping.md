# Google Sheets → CRM: монтажёр

Монтажёр видит только назначенные ему активные производственные задачи. В общей internal-role
модели он может изменять доступные рабочие поля строки; системные identifiers и summaries
остаются read-only. Endpoint `/montage/editor` сохраняется как строгая форма результата монтажа
из трёх полей и доступен только назначенному монтажёру при готовом согласованном исходнике.

## Поступает от менеджера и сценариста

| Google Sheets | Backend |
|---|---|
| Дата создания | `scenarios.scenario_date` |
| ID | `scenarios.external_id` |
| Проект | `scenarios.project_id` |
| Сценарист | `scenarios.assigned_scenarist_id` |
| Текст на обложке | `scenario_content.cover_text` |
| Сценарий | `scenario_content.script_text` |
| Дата дедлайна | `scenarios.deadline` |
| Ссылка с исходником и обложкой | `montage_tasks.source_material_url` |
| Фирменный стиль клиента | `montage_tasks.client_brand_style` |
| ТЗ для монтажа | `scenario_content.montage_brief` |
| ТЗ монтажа доп | `montage_tasks.extra_brief` |
| Цена оплаты | `montage_tasks.price` |
| Дата периода оплаты | `montage_tasks.payment_due_date` |
| Одобрение ответственного лица | approval `source_material` |
| Комментарий сценариста по монтажу | `montage_tasks.scenarist_material_comment` |

## Заполняет монтажёр

| Google Sheets | Backend |
|---|---|
| Ссылка с готовым материалом | `montage_tasks.ready_material_url` |
| Статус монтажа | `montage_tasks.editor_status` |
| Комментарий монтажёра | `montage_tasks.editor_comment` |

Запись выполняется через `PUT /api/v1/scenarios/{scenario_id}/montage/editor`. Endpoint принимает
только три поля из последней таблицы. Монтажёр получает строку только при назначении на него;
Обычные рабочие тексты остаются совместно редактируемыми, но передача результата через этот
workflow endpoint выполняется только назначенным монтажёром.
