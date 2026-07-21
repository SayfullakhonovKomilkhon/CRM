# Google Sheets → CRM: клиент

Источник: клиентский лист со сценарием, двумя этапами согласования, готовым монтажом и
публикационными описаниями. Клиент видит только проекты своей организации.

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
| Комментарий / замечания | approval comment + `scenario_comments` | изменение |
| Примечание | comment stage `pre_generation_client` | изменение |

Решения: `pending`, `approved`, `revision`.

## Готовый монтаж

| Google Sheets | Backend | Доступ клиента |
|---|---|---|
| Ссылка с готовым монтажом | `montage_tasks.ready_material_url` | чтение |
| Одобрение монтажа | approval `final_client` | изменение |
| Комментарий / замечания | approval comment + `scenario_comments` | изменение |

Ссылку готового материала устанавливает монтажёр или менеджер.

## Публикация

| Google Sheets | Backend | Доступ клиента |
|---|---|---|
| Описание DZEN | `publications.description_dzen` | чтение |
| Описание YouTube | `publications.description_youtube` | чтение |
| Описание TikTok | `publications.description_tiktok` | чтение |
| Описание Instagram | `publications.description_instagram` | чтение |
| Дата публикации | `publications.publication_date` | чтение |
| Статус публикации | `publications.is_published` | чтение |
| ТЗ для публициста | `publications.publisher_brief` | чтение |
| Ссылка Instagram | `publications.instagram_url` | чтение |
| Лайки / просмотры | `publications.engagement_metrics` | чтение |
| Анализ публикации | `publications.publication_analysis` | чтение |
| ИИ-описания сетей | `publications.ai_social_descriptions` | чтение |
| Сценарий от Леи | `publications.leia_script` | чтение |

Публикационные поля обновляет сценарист или менеджер. Для клиента они read-only.
