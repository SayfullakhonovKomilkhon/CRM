# Google Sheets → CRM: клиент

Источник: клиентский лист со сценарием, двумя этапами согласования, готовым монтажом и
публикационными описаниями. Клиент видит только проекты своей организации.
API sheet-проекция содержит ровно 16 перечисленных ниже колонок в порядке разделов. Внутренние
исследования, назначения, UUID пользователей, цена, ТЗ и анализ монтажа клиенту не отправляются.

## Поля от сценариста

| Google Sheets | Backend | Доступ клиента |
|---|---|---|
| Дата | `scenarios.scenario_date` | чтение |
| ID | `scenarios.external_id` | чтение |
| Спикер | `scenarios.speaker` | чтение |
| Сценарий | `scenario_content.script_text` | чтение |

## Согласование сценария

| Google Sheets | Backend | Доступ клиента |
|---|---|---|
| Одобрение сценария | approval `pre_generation_client` | изменение |
| Комментарий / замечания | approval `pre_generation_client`.comment | изменение |
| Примечание | approval `pre_generation_client`.note | изменение |

Решения: `pending`, `approved`, `revision`, `rejected`.

## Готовый монтаж

| Google Sheets | Backend | Доступ клиента |
|---|---|---|
| Ссылка с готовым монтажом | `montage_tasks.ready_material_url` | чтение |
| Одобрение монтажа | approval `final_client` | изменение |
| Комментарий / замечания | approval `final_client`.comment | изменение |

Ссылку готового материала устанавливает любая внутренняя роль.

## Публикация

| Google Sheets | Backend | Доступ клиента |
|---|---|---|
| Описание DZEN | `publications.description_dzen` | чтение |
| Описание YouTube | `publications.description_youtube` | чтение |
| Описание TikTok | `publications.description_tiktok` | чтение |
| Описание Instagram | `publications.description_instagram` | чтение |
| Дата публикации | `publications.publication_date` | чтение |
| Статус публикации | `publications.is_published` | чтение |

Публикационные поля обновляет любая внутренняя роль. Для клиента они read-only.

Колонки approval-комментариев содержат текущее значение решения. Они не создают сообщения
автоматически. `scenario_comments` используется отдельно как история обсуждения через
`GET|POST /api/v1/scenarios/{id}/comments`; клиенту доступны только два клиентских этапа.
