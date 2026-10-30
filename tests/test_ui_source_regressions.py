from pathlib import Path


ROOT = Path(__file__).parents[1]
APP = (ROOT / "yaad/ui/app.js").read_text()
CSS = (ROOT / "yaad/ui/style.css").read_text()
HTML = (ROOT / "yaad/ui/index.html").read_text()


def function_source(name: str) -> str:
    marker = f"function {name}("
    start = APP.index(marker)
    end = APP.find("\n", start)
    return APP[start:end if end != -1 else None]


def test_transcript_preserves_message_anchor_except_explicit_follow_actions():
    render = function_source("renderConversation")
    assert "data-message-index" in render
    assert "restoreMessageScroll" in render
    assert "scroll:'bottom'" in function_source("renderRecents")
    assert "scroll:'top'" in function_source("newConversation")
    assert "scrollTop=$('#conversation-scroll').scrollHeight" not in render


def test_copy_actions_share_clipboard_fallback_and_live_feedback():
    render = function_source("renderConversation")
    assert "document.execCommand('copy')" in render
    assert "Couldn’t copy" in render
    assert "$('#copy-live').textContent" in render
    assert "querySelectorAll('.copy-question')" in render
    assert "querySelectorAll('.copy-answer')" in render


def test_answer_status_and_sensitive_content_are_not_blurred():
    message = function_source("renderMessage")
    assert "answer-badge" in message
    assert "Yaad ·" not in message
    assert "sensitive-reveal" in message
    assert ".assistant-answer.masked" not in message
    assert ".assistant-answer{filter:none;opacity:1" in CSS


def test_local_unavailable_status_has_label_retry_and_badge():
    assert "local_unavailable:'Local model unavailable'" in function_source("statusLabel")
    assert "'local_unavailable'" in function_source("renderMessage")
    assert ".answer-badge.local_unavailable" in CSS


def test_source_cards_and_tables_constrain_long_content():
    message = function_source("renderMessage")
    assert "source-filename" in message
    assert ".source-chip{width:100%;min-width:0" in CSS
    assert ".data-table{table-layout:fixed" in CSS
    assert "min-width:760px" in CSS


def test_privacy_status_write_cannot_throw_on_missing_element():
    privacy = function_source("updatePrivacyStatus")
    assert 'id="consent-note"' in HTML
    assert "if(note)" in privacy


def test_privacy_refresh_merges_consent_without_clobbering_folder_rows():
    refresh = function_source("refreshPrivacy")
    assert "folders=result.folders" not in refresh
    assert "incoming" in refresh
    assert "model_error" in refresh


def test_every_failed_answer_gets_a_working_retry_button():
    render = function_source("renderConversation")
    assert "querySelectorAll('.answer-retry')" in render


def test_numeric_alignment_matches_each_tables_columns():
    assert ".folder-table th:nth-child(2)" in CSS
    assert ".folder-table th:nth-child(3)" in CSS
    assert ".data-table th:nth-child(2)" not in CSS


def test_ask_transport_failure_is_an_error_not_cloud_unavailable():
    assert "status:'error'" in APP
    assert "error.status===0?'cloud_unavailable'" not in APP


def test_welcome_distinguishes_loading_error_and_empty_library():
    welcome = function_source("welcomeMarkup")
    assert "statusError" in welcome
    assert "welcome-retry" in welcome
    assert "welcome-retry" in function_source("renderConversation")


def test_file_table_icons_use_the_sized_outline_icon_style():
    files = function_source("renderFileStatus")
    assert '<span class="source-icon">' in files
    assert ".source-icon svg{width:18px" in CSS


def test_error_text_uses_theme_colors_instead_of_fixed_pale_text():
    assert "#ffd8d8" not in CSS
    assert "#ffc2a5" not in CSS
    assert ".state-message.error{border:1px solid var(--error);color:var(--error)" in CSS
    assert ".cloud-indicator.status-unavailable{color:var(--uncertain)}" in CSS


def test_skipped_files_offer_no_retry_and_run_disables_table_actions():
    files = function_source("renderFileStatus")
    assert "['partial','failed'].includes(file.status)" in files
    assert "['partial','failed','skipped']" not in APP
    assert "querySelectorAll('.table-action')" in function_source("runIndex")


def test_failed_status_refresh_keeps_last_good_rows_and_retries_same_route():
    refresh = function_source("refreshStatus")
    assert "statusLoaded" in refresh
    assert "folders=[]" not in refresh
    assert "files=[]" not in refresh
    assert "setErrorState(folderState" in refresh
    assert "setErrorState(indexState" in refresh
    assert "api('/api/index/status')" in refresh


def _theme_block(theme: str) -> str:
    import re

    match = re.search(r":root\[data-theme=" + theme + r"\]\{([^}]*)\}", CSS)
    assert match, f"missing explicit {theme} theme rule"
    return match.group(1)


def _luminance(hex_color: str) -> float:
    rgb = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]

    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(value) for value in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _theme_app_bg(theme: str) -> str:
    import re

    match = re.search(r"--app-bg:(#[0-9A-Fa-f]{6})", _theme_block(theme))
    assert match, f"missing --app-bg in {theme} theme"
    return match.group(1)


def test_medium_theme_is_genuinely_between_light_and_dark():
    medium, light, dark = (_luminance(_theme_app_bg(theme)) for theme in ("medium", "light", "dark"))
    assert medium < light - 0.15, "medium theme is still nearly as bright as light"
    assert medium > dark + 0.15, "medium theme collapsed toward dark"
    # The unattributed :root fallback must match medium so first paint agrees.
    assert CSS.count("--app-bg:#C9CED4") >= 2


def test_theme_selection_persists_across_reload():
    assert "localStorage.getItem('yaad.theme')" in HTML
    assert "localStorage.setItem(THEME_STORE" in function_source("applyTheme")


def test_composer_supporting_text_is_readable_without_shrinking_input():
    assert ".composer textarea{font-size:19px" in CSS
    assert ".scope-label{font-size:13px" in CSS
    assert ".composer select{font-size:13px" in CSS
    assert ".composer-hint{font-size:13px" in CSS
    assert ".send-button{width:40px;height:40px" in CSS


def test_shared_tables_use_stable_grids_and_right_aligned_actions():
    for token in ("folder-col-name", "file-col-name", "egress-col-time", "egress-col-summary"):
        assert token in HTML
    assert ".file-col-name{width:33%}" in CSS
    assert ".egress-col-time{width:17%}" in CSS
    assert ".folder-table th:last-child" in CSS
    assert ".file-table th:last-child" in CSS
    assert ".egress-table th:last-child" not in CSS
    assert ".data-table tbody tr:hover{background:var(--hover)}" in CSS
    assert ".table-empty{padding:20px 14px!important" in CSS
    assert ".file-col-type{width:9%}" in CSS
    assert ".file-col-status{width:15%}" in CSS
    assert ".file-col-details{width:29%}" in CSS
    assert ".file-col-action{width:14%}" in CSS
    files = function_source("renderFileStatus")
    assert '<span class="table-detail">' in files
    assert '<td class="table-detail"' not in files


def test_folder_consent_states_are_explicit_text():
    assert "'Allowed':'Local only'" in function_source("renderFolders")


def test_cloud_activity_is_a_plain_table_without_token_counts():
    assert '<table class="data-table egress-table">' in HTML
    for heading in ("<th scope=\"col\">Time</th>", "<th scope=\"col\">Activity</th>", "<th scope=\"col\">Model</th>",
                    "<th scope=\"col\">Status</th>"):
        assert heading in HTML
    assert 'id="summary-info"' in HTML
    assert 'aria-label="About the Summary column"' in HTML
    assert 'id="summary-info-dialog"' in HTML
    assert 'id="egress-detail-dialog"' in HTML
    assert '<tbody id="egress">' in HTML
    egress = function_source("renderEgress")
    assert "document.createElement('tr')" in egress
    assert "status-badge" in egress
    assert "details" not in egress
    assert "<summary>" not in egress
    assert "input_tokens" not in egress
    assert "output_tokens" not in egress
    assert "/api/egress?limit=10&offset=" in function_source("refreshEgress")


def test_folder_and_file_tables_share_pagination_pattern():
    for token in ("folder-prev", "folder-next", "folder-page-label",
                  "file-prev", "file-next", "file-page-label"):
        assert f'id="{token}"' in HTML
    assert 'id="folder-count"' not in HTML
    assert "folder-count" not in APP
    assert "slice(folderPage,folderPage+10)" in function_source("renderFolders")
    assert "slice(filePage,filePage+10)" in function_source("renderFileStatus")
    assert "Math.ceil(folders.length/10)" in function_source("renderFolders")
    assert "Math.ceil(sorted.length/10)" in function_source("renderFileStatus")
    assert "$('#folder-prev').onclick" in APP
    assert "$('#file-next').onclick" in APP


def test_summary_column_has_concise_time_and_full_detail_view():
    egress = function_source("renderEgress")
    assert "shortTime(item.ts)" in egress
    assert "cell-detail" in egress
    assert "openEgressRow(Number(item.id))" in egress
    clock = function_source("shortTime")
    assert "'—'" in clock
    assert "local time" in function_source("openEgressRow")
    detail = function_source("openEgressRow")
    assert "input_tokens" not in detail
    assert "output_tokens" not in detail
    assert ".th-info{" in CSS
    assert ".cell-detail{" in CSS
    assert ".detail-list{" in CSS or ".detail-list{display:grid" in CSS


def test_privacy_page_hides_redundant_model_and_count_text():
    assert "privacy-model" not in HTML
    assert "privacy-request-count" not in HTML
    assert "Provider model" not in HTML
    assert "recorded request(s)" not in HTML
    assert 'id="privacy-alert"' in HTML
    refresh = function_source("refreshPrivacy")
    assert "$('#privacy-alert')" in refresh
    assert "model_error" in refresh


def test_settings_icon_is_a_centered_gear():
    assert "m19.4 13.5" not in HTML
    assert '<circle cx="12" cy="12" r="3"/>' in HTML
    assert "M12.22 2h-.44" in HTML
    assert ".nav-item svg circle" in CSS


def test_index_action_disables_without_selection_or_while_busy():
    assert 'id="index-button" disabled' in HTML
    assert "updateIndexButton()" in function_source("renderFolders")
    gate = function_source("updateIndexButton")
    assert "dataset.busy==='true'" in gate
    assert "!selectedFolderId" in gate
    run = function_source("runIndex")
    assert "dataset.busy='true'" in run
    assert "updateIndexButton()" in run


def test_summary_metrics_use_restrained_tones():
    counts = function_source("renderCounts")
    for tone in ("tone-ok", "tone-warn", "tone-muted", "tone-bad"):
        assert tone in counts
    assert ".summary-value{font-size:24px" in CSS
    assert ".summary-item.tone-ok .summary-value{color:var(--verified)}" in CSS
    assert ".summary-item.tone-bad .summary-value{color:var(--conflict)}" in CSS


def test_audio_citations_render_stored_timestamps_without_invented_precision():
    label = function_source("locatorLabel")
    assert "formatTime(locator.start)" in label
    assert "formatTime(locator.end)" in label
    clock = function_source("formatTime")
    assert "Math.floor(seconds/60)" in clock
    assert "Math.floor(seconds%60)" in clock


def test_type_scale_keeps_functional_text_readable():
    assert ".state-message{font-size:13px}" in CSS
    assert ".cloud-indicator{font-size:12px}" in CSS
    assert ".section-heading p{font-size:13px}" in CSS
    assert ".form-message{font-size:12px}" in CSS
    assert ".local-disclaimer{font-size:11px}" in CSS
    assert CSS.rfind(".local-disclaimer{font-size:11px}") > CSS.rfind("font-size:9px")


def test_memory_nav_and_view_follow_existing_view_pattern():
    assert 'data-view="memory"' in HTML
    assert 'id="memory-view"' in HTML
    assert "'memory':'memory-view'" in function_source("setView")
    assert "if(view==='memory')refreshMemory()" in function_source("setView")
    assert 'class="data-table memory-table"' in HTML
    assert 'id="memory-list"' in HTML
    assert ".memory-col-name{width:" in CSS


def test_memory_table_columns_and_pager_match_shared_pattern():
    for heading in ("Memory", "Type", "Date", "Status", "Serving", "Details"):
        assert f"<th scope=\"col\">{heading}</th>" in HTML
    assert 'id="memory-prev"' in HTML and 'id="memory-next"' in HTML
    assert 'id="memory-page-label"' in HTML
    pager = function_source("updateMemoryPager")
    assert "memory-page-label" in pager
    assert "memory-prev" in pager and "memory-next" in pager
    assert ".memory-table th:last-child" in CSS
    assert ".memory-col-date{width:19%}" in CSS
    assert ".memory-table td.egress-time" in CSS
    assert '<td class="egress-time" title="' in function_source("renderMemory")


def test_memory_status_labels_distinguish_current_from_history():
    badges = function_source("memoryStatusBadge")
    for label in ("Conflicted", "Superseded", "Stale", "Proposed", "Undecided", "Current"):
        assert f"'{label}'" in badges
    assert "Inferred · excluded" in badges
    serving = function_source("memoryServingBadge")
    assert "'eligible'" in serving and "Answers questions" in serving
    assert "'excluded'" not in serving or "Excluded" in serving
    assert "Excluded" in serving


def test_memory_filter_offers_current_and_history_groups():
    assert 'id="memory-filter"' in HTML
    for value in ("all", "current", "proposal", "unresolved", "conflicted", "history"):
        assert f'value="{value}"' in HTML
    filt = function_source("memoryMatchesFilter")
    assert "memory-filter" in filt
    assert "undecided_only" in filt


def test_memory_record_dialog_shows_evidence_and_report_button():
    assert 'id="memory-dialog"' in HTML
    assert 'id="memory-dialog-body"' in HTML
    assert 'id="memory-evidence-list"' in HTML
    assert 'id="memory-report-open"' in HTML
    assert "memory-flag-form" not in HTML
    dialog = function_source("openMemoryRecord")
    assert "memory-dialog" in dialog
    assert "showModal()" in dialog
    assert "Source removed" in dialog
    assert ".detail-list{" not in dialog


def test_memory_report_dialog_structure_validation_and_submission():
    dialog = HTML.split('id="memory-report-dialog">')[1].split("</dialog>")[0]
    assert "Report a problem with this memory" in dialog
    assert "What is wrong?" in dialog
    assert 'id="memory-report-reason"' in dialog
    assert "<textarea" in dialog
    assert 'id="memory-report-submit"' in dialog
    assert 'id="memory-report-cancel"' in dialog
    opener = function_source("openMemoryReport")
    assert "flaggingRecordId" in opener
    assert "#memory-report-dialog" in opener
    submit = function_source("submitMemoryReport")
    assert "flaggingRecordId" in submit
    assert ".trim()" in submit
    assert "Describe the problem first" in submit
    assert "/api/memory/'+flaggingRecordId+'/flag" in submit
    assert "Report recorded. Thank you." in submit
    assert "#memory-report-dialog" in submit and ".close()" in submit


def test_memory_extraction_is_explicit_and_never_automatic():
    assert 'id="memory-extract" type="button" disabled' in HTML
    run = function_source("runMemoryExtract")
    assert "/api/memory/extract" in run
    assert "folder_id" in run
    assert "accepted" in run and "spent" in run
    assert "/api/memory/extract" not in function_source("refreshMemory")
    assert "/api/memory/extract" not in function_source("setView")
    assert "/api/memory/extract" not in function_source("renderMessage")


def test_from_memory_label_shown_when_memory_used():
    message = function_source("renderMessage")
    assert "memoryClaimSection" not in message
    assert "result.memory&&result.memory.used" in message
    assert "from-memory" in message
    assert ">Memory</span>" in message
    assert "From memory" not in message
    assert "answer-status" in message
    assert "statusLabel(result.status)" in message
    assert ".from-memory{display:inline-flex;align-items:center;gap:5px;background:#745b27;color:#fff0bf;font-size:11px;font-weight:600;white-space:nowrap;padding:4px 9px;border-radius:999px;line-height:1.3}" in CSS
    assert ".from-memory svg{" in CSS
    assert ".answer-status{" in CSS
    assert "memoryClaimSection" not in APP
    assert "toggleClaimPop" not in APP
    assert "closeClaimPops" not in APP
    assert ".memory-claim{" not in CSS
    assert "claim-badge" not in CSS
    assert "claim-sources-pop" not in CSS


def test_from_memory_label_hidden_when_memory_not_used():
    message = function_source("renderMessage")
    assert "result.memory&&result.memory.used?'<span class=\"from-memory\"" in message
    assert "Memory</span>':''" in message
    assert "assistant-answer" in message
    assert "source-chip" in message
    assert "mem-evidence" not in APP
    assert "mem-evidence" not in CSS
    assert ".memory-claims{" not in CSS
    assert "claim-statement" not in CSS


def test_source_cards_remain_single_source_list():
    render = function_source("renderConversation")
    assert "querySelectorAll('.source-link')" in render
    assert "mem-evidence" not in render
    opener = function_source("openEvidence")
    assert "record_ids:[]" in opener
    preview = function_source("loadEvidencePreview")
    assert "info.file" in preview


def test_memory_keyboard_responsive_and_empty_states():
    assert "from-memory" in function_source("renderMessage")
    assert 'aria-label="Filter memories"' in HTML
    assert "memory-state" in function_source("refreshMemory")
    assert "No memories yet" in function_source("renderMemory")
    assert "No memories match this filter" in function_source("renderMemory")
    assert "@media(max-width:700px){.memory-extract-panel select" in CSS
    assert "memory-evidence-list" in HTML


def test_memory_lists_never_show_raw_ids_or_token_counts():
    table = function_source("renderMemory")
    assert "r.id" not in table or "data-record" in table
    assert "input_tokens" not in APP and "output_tokens" not in APP
    dialog = function_source("openMemoryRecord")
    assert "record_key" not in dialog
    assert "extraction_run" not in dialog


def test_existing_file_and_index_workflows_untouched_by_memory():
    assert "memory" not in function_source("renderFolders").lower()
    assert "memory" not in function_source("renderFileStatus").lower()
    assert "memory" not in function_source("runIndex").lower()
    assert function_source("refreshMemory").count("/api/memory") == 1
    assert "/api/memory" not in function_source("renderMemory")


def test_status_listing_answers_keep_history_markers():
    message = function_source("renderMessage")
    assert "history-tag" in message
    assert "is-historical" in message
    assert ".history-tag{" in CSS


def test_ask_timeout_is_120s_while_other_timeouts_unchanged():
    assert "api('/api/ask',{question,folder_id:folderValue?Number(folderValue):null},{timeoutMs:120000})" in APP
    assert "options.timeoutMs||12000" in APP
    for untouched in ("timeoutMs:600000", "timeoutMs:180000", "timeoutMs:300000", "timeoutMs:185000"):
        assert untouched in APP
    assert APP.count("timeoutMs:120000") == 1


def test_ask_submit_guard_prevents_duplicates_and_stuck_loading():
    assert "if(!question||$('#ask-form').dataset.busy)return;" in APP
    assert "$('#ask-form').dataset.busy='true';$('#send-button').disabled=true;" in APP
    assert "finally{delete $('#ask-form').dataset.busy;resizeComposer()}" in APP
    assert "editingQuestion={conversationId:item.id,index:Number(button.dataset.index)}" in APP
    assert "requestSubmit()" in APP


def test_ask_errors_render_accurate_retryable_messages():
    assert "result:{status:'error',answer:error.message,evidence:[]}" in APP
    api = function_source("api")
    assert "error.name==='AbortError'?'Yaad took too long to respond. Please retry.'" in api
    assert "'Could not connect to Yaad. Check that the app is running and retry.'" in api


def test_superseded_sources_show_history_label():
    message = function_source("renderMessage")
    assert "ev.historical?' is-historical':''" in message
    assert "history-tag" in message
    assert "Superseded by a newer memory" in message
    assert ".history-tag{" in CSS
    assert ".source-chip.is-historical{" in CSS


def test_index_summary_reports_capped_files():
    assert "over folder budget" in function_source("runIndex")


def test_folder_consent_copy_discloses_memory_extraction_egress():
    # Disclosure lives in the How-this-works dialog and Privacy page,
    # not as repeated small print on Sources (user-directed copy).
    assert "Eligible images, scanned PDF pages, and audio may be sent" not in HTML
    dialog = HTML.split('id="consent-dialog">')[1].split("</dialog>")[0]
    assert "memory extraction" in dialog
    assert "consented evidence of any kind" in dialog


def test_partial_status_has_a_human_label():
    labels = function_source("statusLabel")
    assert "partial:'Partially answered'" in labels
    assert "local_unavailable:'Local model unavailable'" in labels


def test_home_and_history_views_are_wired_to_real_apis():
    assert 'data-view="home"' in HTML
    assert 'data-view="history"' in HTML
    assert 'id="home-view"' in HTML
    assert 'id="history-view"' in HTML
    assert "function renderHome()" in APP
    assert "/api/memory" in APP
    assert "/api/memory/transitions?limit=200" in APP
    assert "'home':'home-view'" in APP
    assert "'history':'history-view'" in APP


def test_refinement_invariants_composer_consent_history_home():
    assert "padding-bottom:56px" in CSS
    assert "scroll-padding-bottom:56px" in CSS
    assert "composer clearance" in CSS
    assert "Allow cloud processing for this folder" in HTML
    assert 'id="history-empty-memory"' in APP
    assert "No changes yet" in APP
    assert "data-goto" in APP


def test_home_cards_contain_long_titles_in_a_uniform_grid():
    assert ".home-grid{grid-template-columns:repeat(2,minmax(0,1fr))}" in CSS
    assert ".home-card,.home-card:nth-child(n){grid-column:auto}" in CSS
    assert ".home-list li>:first-child" in CSS
    assert "text-overflow:ellipsis" in CSS


def test_loading_state_offers_retry_only_after_an_error():
    welcome = function_source("welcomeMarkup")
    assert "welcome-loading" not in welcome
    assert ".welcome-loading" not in CSS
    assert "Still loading? Retry" not in welcome
    assert "Retry loading" in welcome
    assert "statusLoading=false" in function_source("renderConversation")


def test_memory_statuses_differ_by_more_than_color():
    badges = function_source("memoryStatusBadge")
    assert "'historical'" in badges
    assert ".status-badge.historical{border:1px dashed currentColor}" in CSS
    assert ".status-badge.failed{border:1px solid currentColor}" in CSS
    assert ".memory-title{display:block" in CSS


def test_dialog_actions_align_right_and_summary_wraps_cleanly():
    assert "#egress-detail-dialog>.primary-button" in CSS
    assert ".egress-table .table-detail{max-width:none" in CSS
    assert ".egress-col-summary{width:32%}" in CSS


def test_minimalist_system_flattens_decoration_and_unifies_scale():
    assert ".app-shell{background:var(--app-bg)}" in CSS
    assert ".sidebar{background:var(--sidebar-bg)}" in CSS
    assert ".nav-item.active{background:var(--selected);box-shadow:none}" in CSS
    assert ".home-card:hover,.home-ask:hover{box-shadow:var(--card-shadow);transform:none}" in CSS
    assert ".primary-button:hover{transform:none}" in CSS
    assert ".composer:focus-within{border-color:var(--accent);box-shadow:none}" in CSS
    assert ".timeline-dot{box-shadow:none}" in CSS
    assert ".management-inner>h1,.home-inner>h1{font-size:30px}" in CSS
    assert ".home-card{min-height:0}" in CSS
    assert ".data-table tbody td{height:auto}" in CSS
    assert "dialog,.consent-dialog{border-radius:16px}" in CSS


def test_home_cards_form_aligned_rows_and_composer_has_no_stray_band():
    assert ".composer-area{border-top:0;box-shadow:none;background:transparent}" in CSS
    assert ".home-grid{align-items:stretch}" in CSS
    assert ".home-card{display:flex;flex-direction:column;padding:20px 22px}" in CSS
    assert ".home-card:last-child:nth-child(odd){grid-column:1/-1}" in CSS
    assert ".home-action{margin-top:auto;padding-top:14px;align-self:flex-start}" in CSS
    assert ".brand-row{height:44px}" in CSS
    assert ".memory-extract-panel .primary-button,.memory-extract-panel .text-button{min-height:42px}" in CSS


def test_home_cards_show_at_most_five_items():
    home = function_source("renderHome")
    assert ".slice(0,5)" in home
    assert ".slice(-5)" in home
    assert "conflicts.slice(0,5)" in home
    assert "more)" in home


def test_chat_view_refreshes_on_navigation_so_welcome_never_goes_stale():
    assert "if(view==='chat')renderConversation()" in function_source("setView")


def test_history_never_shows_raw_record_ids_and_labels_stay_distinct():
    history = function_source("renderHistory")
    assert "replace(/record \\d+/g,'another memory')" in history
    message = function_source("renderMessage")
    assert "locatorLabel(ev.locator)===shortPath(ev.file)||locatorLabel(ev.locator)===shortPath(ev.file).replace" in message
    assert ".file-table .table-detail{cursor:default;text-decoration:none}" in CSS


def test_source_chip_grid_fits_icon_name_tag_and_action():
    assert "grid-template-columns:18px minmax(0,1fr) auto auto" in CSS


def test_history_list_is_paged_ten_per_page_with_shared_pager():
    for token in ("history-prev", "history-next", "history-page-label"):
        assert f'id="{token}"' in HTML
    assert "slice(historyPage,historyPage+10)" in function_source("renderHistory")
    assert "historyPage=0" in function_source("refreshHistory")
    assert "updateHistoryPager()" in function_source("renderHistory")
    assert "$('#history-next').onclick" in APP
    assert "$('#history-view').scrollTop=0" in APP


def test_history_changes_render_as_a_table_with_pager():
    assert '<table class="data-table history-table">' in HTML
    for heading in ("Change", "Transition", "Details", "Date"):
        assert f"<th scope=\"col\">{heading}</th>" in HTML
    assert 'id="history-table-wrap"' in HTML
    assert 'id="history-empty"' in HTML
    assert ".history-col-transition{width:23%}.history-col-details{width:33%}" in CSS
    rows = function_source("renderHistory")
    assert "timeline-item" not in rows
    assert "'<tr><td><strong>'" in rows
    assert "history-table-wrap" in rows


def test_orphan_source_chip_spans_the_full_row():
    assert ".source-list>*:last-child:nth-child(odd){grid-column:1/-1}" in CSS


def test_topbar_and_privacy_show_the_active_inference_provider():
    privacy = function_source("updatePrivacyStatus")
    assert "modelProvider==='local'" in privacy
    assert "'Local · '+localModel" in privacy
    assert "Inference provider: local model" in privacy
    assert "Inference provider: cloud model" in privacy
    assert "Local inference is active. Cloud calls are never made in this mode." in privacy
    assert "modelProvider=String(data.model_provider||'cloud')" in function_source("refreshStatus")
    assert "modelProvider=String(result.model_provider||modelProvider)" in function_source("refreshPrivacy")


def test_privacy_labels_are_provider_neutral():
    assert "<h2>Model processing</h2>" in HTML
    assert "<h2>Recent model activity</h2>" in HTML
    assert "No model requests recorded yet." in APP


def test_history_transition_uses_shared_status_badges():
    history = function_source("renderHistory")
    assert "status-badge '+(TRANSITION_BADGE[t.transition]||'partial')" in history
    assert 'class="home-tag"' not in history
    assert "record_updated:'Updated'" in APP
    assert "record_updated:'indexed'" in APP
    assert "conflict_marked:'failed'" in APP
    assert "superseded:'partial'" in APP


def test_every_ui_badge_carries_a_color_class():
    # No badge/value pair may render as the uncolored default pill.
    assert "ok?'indexed':fail?'failed':'partial'" in function_source("renderEgress")
    assert "['historical','Inferred · excluded']" in APP
    assert "return['historical','Excluded']" in APP
    assert '<span class="status-badge info">' in APP
    assert ".status-badge.info," in CSS
    assert ".status-badge.empty{" in CSS
    assert ".consent-badge.local-only{" in CSS
    assert ".status-label.partial," in CSS
    assert ".status-label.error{" in CSS
    assert ".status-label.not_found," in CSS


def test_privacy_page_hides_details_behind_info_icons():
    # Visible copy stays short; extra explanation lives in dialogs.
    assert "selected content may be sent to Meta for processing" not in HTML
    assert "Request history stays in Yaad" not in HTML.split('id="egress-info-dialog"')[0]
    assert 'id="processing-info"' in HTML
    assert 'id="privacy-intro-info"' in HTML
    assert 'id="egress-info"' in HTML
    assert 'id="processing-info-dialog"' in HTML
    assert 'id="egress-info-dialog"' in HTML
    assert 'id="processing-info-body"' in HTML
    assert "$('#processing-info-dialog').showModal()" in APP
    assert "$('#egress-info-dialog').showModal()" in APP
    privacy = function_source("updatePrivacyStatus")
    assert "$('#processing-info-body')" in privacy
    assert "if(detail)" in privacy
