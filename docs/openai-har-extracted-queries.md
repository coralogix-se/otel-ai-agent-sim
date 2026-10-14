# Extracted queries — openai.cx498.coralogix.com.har

Unique rows: **96** (from 726 metrics URL hits).

| Status | Eval / window | Endpoint | PromQL |
|---|---|---|---|
| 200 | `2026-09-30T15:16:48Z` | query | `count(max_over_time(cursor_active_users_total[10d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `count(max_over_time(cursor_org_pooled_usage_usd[10d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `count(max_over_time(cursor_active_users_cloud_agent[10d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `count(max_over_time(openai_analytics_codex_credits[10d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `count(max_over_time(openai_administration_usage_input_tokens[10d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `count(increase(openai_compliance_events{event_type=~"CODEX_LOG\|CODEX_SECURITY_LOG"}[10d]) > 0)` |
| 200 | `2026-09-30T15:16:48Z` | query | `count(increase(openclaw_tokens_1_total[10d]) > 0)` |
| 200 | `2026-09-30T15:16:48Z` | query | `count(increase(openai_compliance_events{event_type="CONVERSATION_MESSAGE"}[10d]) > 0)` |
| 200 | `2026-09-30T15:16:48Z` | query | `sum by (line_item, project_id, project_name, date) (last_over_time(openai_administration_cost_usd[7d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `sum by (line_item, project_id, project_name, date, unit) (last_over_time(openai_administration_cost_quantity[7d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_tokens[7d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_uncached_tokens[7d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_cached_tokens[7d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_cache_write_tokens[7d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_text_tokens[7d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_audio_tokens[7d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_image_tokens[7d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_cached_text_tokens[7d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_cached_audio_tokens[7d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_cached_image_tokens[7d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_output_tokens[7d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_output_text_tokens[7d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_output_audio_tokens[7d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_output_image_tokens[7d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_requests[7d]))` |
| 200 | `2026-09-30T15:16:48Z` | query | `last_over_time(openai_administration_spend_limit_configured[7d])` |
| 200 | `2026-09-30T15:16:48Z` | query | `last_over_time(openai_administration_project_spend_limit_configured[7d])` |
| 200 | `2026-09-30T15:16:48Z` | query | `last_over_time(openai_administration_project_spend_limit_enforcing[7d])` |
| 200 | `2026-09-30T15:16:48Z` | query | `last_over_time(openai_administration_project_spend_limit_usd[7d])` |
| 200 | `2026-09-30T15:16:48Z` | query | `last_over_time(openai_administration_spend_alert_threshold_usd[7d])` |
| 200 | `2026-09-30T15:16:48Z` | query | `sum by (line_item, project_id, project_name, date) (last_over_time(openai_administration_cost_usd[7d] offset 7d))` |
| 200 | `2026-09-30T15:16:48Z` | query | `sum by (line_item, project_id, project_name, date, unit) (last_over_time(openai_administration_cost_quantity[7d] offset 7d))` |
| — | `2026-09-30T15:16:48Z` | query | `count(max_over_time(cursor_active_users_cloud_agent[10d]))` |
| — | `2026-09-30T15:16:48Z` | query | `count(max_over_time(openai_analytics_codex_credits[10d]))` |
| — | `2026-09-30T15:16:48Z` | query | `count(max_over_time(openai_administration_usage_input_tokens[10d]))` |
| — | `2026-09-30T15:16:48Z` | query | `count(increase(openai_compliance_events{event_type=~"CODEX_LOG\|CODEX_SECURITY_LOG"}[10d]) > 0)` |
| — | `2026-09-30T15:16:48Z` | query | `count(increase(openclaw_tokens_1_total[10d]) > 0)` |
| — | `2026-09-30T15:16:48Z` | query | `count(increase(openai_compliance_events{event_type="CONVERSATION_MESSAGE"}[10d]) > 0)` |
| — | `2026-09-30T15:16:48Z` | query | `sum by (line_item, project_id, project_name, date) (last_over_time(openai_administration_cost_usd[7d]))` |
| — | `2026-09-30T15:16:48Z` | query | `sum by (line_item, project_id, project_name, date, unit) (last_over_time(openai_administration_cost_quantity[7d]))` |
| — | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_tokens[7d]))` |
| — | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_uncached_tokens[7d]))` |
| — | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_cached_text_tokens[7d]))` |
| — | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_cached_tokens[7d]))` |
| — | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_output_tokens[7d]))` |
| — | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_audio_tokens[7d]))` |
| — | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_cached_image_tokens[7d]))` |
| — | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_output_audio_tokens[7d]))` |
| — | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_cached_audio_tokens[7d]))` |
| — | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_image_tokens[7d]))` |
| — | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_cache_write_tokens[7d]))` |
| — | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_output_text_tokens[7d]))` |
| — | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_requests[7d]))` |
| — | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_input_text_tokens[7d]))` |
| — | `2026-09-30T15:16:48Z` | query | `last_over_time(openai_administration_project_spend_limit_enforcing[7d])` |
| — | `2026-09-30T15:16:48Z` | query | `last_over_time(openai_administration_spend_limit_configured[7d])` |
| — | `2026-09-30T15:16:48Z` | query | `last_over_time(openai_administration_project_spend_limit_configured[7d])` |
| — | `2026-09-30T15:16:48Z` | query | `last_over_time(openai_administration_spend_alert_threshold_usd[7d])` |
| — | `2026-09-30T15:16:48Z` | query | `sum by (line_item, project_id, project_name, date) (last_over_time(openai_administration_cost_usd[7d] offset 7d))` |
| — | `2026-09-30T15:16:48Z` | query | `sum by (model, api_key_id, date) (last_over_time(openai_administration_usage_output_image_tokens[7d]))` |
| — | `2026-09-30T15:16:48Z` | query | `sum by (line_item, project_id, project_name, date, unit) (last_over_time(openai_administration_cost_quantity[7d] offset 7d))` |
| — | `2026-09-30T15:16:48Z` | query | `last_over_time(openai_administration_project_spend_limit_usd[7d])` |
| 200 | `2026-09-30T15:16:50Z` | query | `last_over_time(openai_administration_org_user_info[7d])` |
| 200 | `2026-09-30T15:16:50Z` | query | `last_over_time(openai_administration_project_info[7d])` |
| 200 | `2026-09-30T15:16:50Z` | query | `last_over_time(openai_administration_project_api_key_info[7d])` |
| 200 | `2026-09-30T15:16:50Z` | query | `last_over_time(openai_administration_project_service_account_info[7d])` |
| 200 | `2026-09-30T15:16:50Z` | query | `last_over_time(openai_administration_org_invite_info[7d])` |
| 200 | `2026-09-30T15:16:50Z` | query | `last_over_time(openai_administration_admin_key_info[7d])` |
| 200 | `2026-09-30T15:16:50Z` | query | `last_over_time(openai_administration_project_member_info[7d])` |
| — | `2026-09-30T15:16:50Z` | query | `last_over_time(openai_administration_project_info[7d])` |
| — | `2026-09-30T15:16:50Z` | query | `last_over_time(openai_administration_org_user_info[7d])` |
| — | `2026-09-30T15:16:50Z` | query | `last_over_time(openai_administration_project_api_key_info[7d])` |
| — | `2026-09-30T15:16:50Z` | query | `last_over_time(openai_administration_org_invite_info[7d])` |
| — | `2026-09-30T15:16:50Z` | query | `last_over_time(openai_administration_project_member_info[7d])` |
| — | `2026-09-30T15:16:50Z` | query | `last_over_time(openai_administration_admin_key_info[7d])` |
| — | `2026-09-30T15:16:50Z` | query | `last_over_time(openai_administration_project_service_account_info[7d])` |
| 200 | `2026-09-30T15:16:51Z` | query | `sum(last_over_time(openai_administration_usage_embeddings_tokens[7d]))` |
| 200 | `2026-09-30T15:16:51Z` | query | `sum(last_over_time(openai_administration_usage_embeddings_model_requests[7d]))` |
| 200 | `2026-09-30T15:16:51Z` | query | `sum(last_over_time(openai_administration_usage_audio_speeches_characters[7d]))` |
| 200 | `2026-09-30T15:16:51Z` | query | `sum(last_over_time(openai_administration_usage_audio_speeches_model_requests[7d]))` |
| 200 | `2026-09-30T15:16:51Z` | query | `sum(last_over_time(openai_administration_usage_audio_transcriptions_seconds[7d]))` |
| 200 | `2026-09-30T15:16:51Z` | query | `sum(last_over_time(openai_administration_usage_audio_transcriptions_model_requests[7d]))` |
| 200 | `2026-09-30T15:16:51Z` | query | `sum(last_over_time(openai_administration_usage_moderations_tokens[7d]))` |
| 200 | `2026-09-30T15:16:51Z` | query | `sum(last_over_time(openai_administration_usage_moderations_model_requests[7d]))` |
| 200 | `2026-09-30T15:16:51Z` | query | `sum(last_over_time(openai_administration_usage_web_search_requests[7d]))` |
| 200 | `2026-09-30T15:16:51Z` | query | `sum(last_over_time(openai_administration_usage_web_search_model_requests[7d]))` |
| 200 | `2026-09-30T15:16:51Z` | query | `sum(last_over_time(openai_administration_usage_file_search_requests[7d]))` |
| — | `2026-09-30T15:16:51Z` | query | `sum(last_over_time(openai_administration_usage_embeddings_tokens[7d]))` |
| — | `2026-09-30T15:16:51Z` | query | `sum(last_over_time(openai_administration_usage_audio_transcriptions_model_requests[7d]))` |
| — | `2026-09-30T15:16:51Z` | query | `sum(last_over_time(openai_administration_usage_embeddings_model_requests[7d]))` |
| — | `2026-09-30T15:16:51Z` | query | `sum(last_over_time(openai_administration_usage_moderations_model_requests[7d]))` |
| — | `2026-09-30T15:16:51Z` | query | `sum(last_over_time(openai_administration_usage_audio_speeches_model_requests[7d]))` |
| — | `2026-09-30T15:16:51Z` | query | `sum(last_over_time(openai_administration_usage_moderations_tokens[7d]))` |
| — | `2026-09-30T15:16:51Z` | query | `sum(last_over_time(openai_administration_usage_audio_transcriptions_seconds[7d]))` |
| — | `2026-09-30T15:16:51Z` | query | `sum(last_over_time(openai_administration_usage_web_search_model_requests[7d]))` |
| — | `2026-09-30T15:16:51Z` | query | `sum(last_over_time(openai_administration_usage_web_search_requests[7d]))` |
