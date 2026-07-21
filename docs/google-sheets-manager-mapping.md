# Google Sheets → CRM: менеджер

Менеджер видит всю производственную строку, но изменяет только назначение монтажёра, стоимость,
свои решения и комментарии. Данные сценариста, монтажёра и клиента отображаются read-only.

## Поступает от сценариста

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
| Дополнительное ТЗ | `montage_tasks.extra_brief` |
| Статус сценариста | `scenarios.status` / `montage_tasks.material_status` |
| Комментарий сценариста | `montage_tasks.scenarist_material_comment` |

## Изменяет менеджер

| Google Sheets | Backend |
|---|---|
| Одобрение исходника | approval `source_material` |
| Комментарий | comment соответствующего approval |
| Выбор монтажёра | `montage_tasks.assigned_editor_id` / `external_editor_name` |
| Цена монтажа | `montage_tasks.price` |
| Дата периода оплаты | `montage_tasks.payment_due_date` |
| Одобрение готового монтажа | approval `montage_compliance` |
| Комментарий ответственного по монтажу | comment approval `montage_compliance` |
| Дата готового монтажа | `montage_tasks.ready_at` |

## Поступает от монтажёра

| Google Sheets | Backend |
|---|---|
| Ссылка с готовым материалом | `montage_tasks.ready_material_url` |
| Статус монтажёра | `montage_tasks.editor_status` |
| Комментарий монтажёра | `montage_tasks.editor_comment` |

Монтажёр может обновлять эти поля только в назначенной ему задаче.

## Поступает от клиента

| Google Sheets | Backend |
|---|---|
| Одобрение клиента | approval `final_client` |
| Комментарий клиента | comment approval `final_client` |
