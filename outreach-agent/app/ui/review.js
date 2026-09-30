(() => {
  'use strict';

  const app = document.getElementById('app');
  const pageTitle = document.getElementById('page-title');
  const topStatus = document.getElementById('top-status');
  const state = {
    view: 'overview', clinics: [], queue: [], settings: {}, dashboard: {},
    activity: [], verticals: [], detail: null, discovery: null, analysisBusy: new Set(),
    batch: null, send: null, batchAvailable: 0, sendPreview: null,
    batchApiAvailable: false, sendApiAvailable: false,
    batchApiError: '', sendApiError: '',
  };
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
  const formatAIValue = (value) => {
    if (typeof value === 'string' || typeof value === 'number') return String(value);
    if (Array.isArray(value)) return value.map(formatAIValue).filter(Boolean).join(' · ');
    if (value && typeof value === 'object') return Object.entries(value)
      .map(([key, item]) => `${key.replaceAll('_', ' ')}: ${formatAIValue(item)}`).filter(Boolean).join(' · ');
    return '';
  };
  const api = async (url, options) => {
    const response = await fetch(url, options);
    let body;
    try { body = await response.json(); } catch { body = {}; }
    if (!response.ok) {
      const error = new Error(body.error || `HTTP ${response.status}`);
      error.status = response.status;
      error.endpoint = url;
      throw error;
    }
    return body;
  };
  const get = (url) => api(url);
  const post = (url, data) => api(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  });
  const toast = (message) => {
    const node = document.getElementById('toast');
    node.textContent = message;
    node.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { node.hidden = true; }, 3200);
  };
  const statusBadge = (value) => `<span class="badge ${esc(String(value || 'unknown').toLowerCase())}">${esc(value || 'UNKNOWN')}</span>`;
  const qualificationBadge = (value) => statusBadge(value || 'NEEDS_REVIEW');

  async function load() {
    const [dashboard, clinics, queue, activity, settings, verticals, health] = await Promise.all([
      get('/api/dashboard'), get('/api/clinics'), get('/api/queue'), get('/api/activity'),
      get('/api/settings'), get('/api/verticals'), get('/api/health'),
    ]);
    const items = Array.isArray(verticals.items) ? verticals.items.filter((item) => item?.key) : [];
    const preferredVertical = items.find((item) => item.key === 'dental')?.key || items[0]?.key || '';
    const capabilities = health.capabilities || {};
    const hasBatchApi = Boolean(capabilities.batch_analysis);
    const hasSendApi = Boolean(capabilities.send_batch);
    const [batchResult, sendResult, eligibleResult] = await Promise.allSettled([
      hasBatchApi ? get('/api/batches/latest') : Promise.resolve({ job: null }),
      hasSendApi ? get('/api/send-batches/latest') : Promise.resolve({ job: null }),
      hasBatchApi && preferredVertical
        ? get(`/api/batches/eligible?vertical=${encodeURIComponent(preferredVertical)}`)
        : Promise.resolve({ available: 0 }),
    ]);
    const batchApiAvailable = hasBatchApi && batchResult.status === 'fulfilled' && eligibleResult.status === 'fulfilled';
    const sendApiAvailable = hasSendApi && sendResult.status === 'fulfilled';
    Object.assign(state, {
      dashboard, clinics: clinics.items || [], queue: queue.items || [],
      activity: activity.items || [], settings, verticals: items,
      batch: batchApiAvailable ? batchResult.value.job : null,
      send: sendApiAvailable ? sendResult.value.job : null,
      batchApiAvailable, sendApiAvailable,
      batchApiError: batchResult.status === 'rejected' ? batchResult.reason.message : eligibleResult.status === 'rejected' ? eligibleResult.reason.message : hasBatchApi ? '' : 'This running backend does not advertise persistent batch endpoints.',
      sendApiError: sendResult.status === 'rejected' ? sendResult.reason.message : hasSendApi ? '' : 'This running backend does not advertise send-batch endpoints.',
      batchAvailable: eligibleResult.status === 'fulfilled' ? Number(eligibleResult.value.available || 0) : 0,
    });
    topStatus.textContent = health.lm_studio
      ? `Backend connected · ${settings.lm_studio_model}`
      : 'Backend connected · LM Studio unavailable';
    document.getElementById('sidebar-provider').textContent = health.lm_studio
      ? `LM Studio · ${settings.lm_studio_model}` : 'LM Studio недоступен';
    document.getElementById('sidebar-send').textContent = settings.smtp_mode || 'SIMULATED';
  }

  function nav(view) {
    state.view = view;
    document.querySelectorAll('#navigation [data-view], .topbar [data-view]').forEach((button) => {
      button.classList.toggle('active', button.dataset.view === view);
    });
    pageTitle.textContent = ({ overview: 'Overview', discovery: 'Discovery', leads: 'Leads', review: 'Review Queue', activity: 'Activity', settings: 'Settings' })[view] || 'Overview';
  }

  function metric(label, value, hint = '') {
    return `<article><span>${esc(label)}</span><strong>${Number(value || 0)}</strong>${hint ? `<small>${esc(hint)}</small>` : ''}</article>`;
  }

  let jobPollTimer = null;
  const elapsed = (started, finished) => {
    if (!started) return '—';
    const seconds = Math.max(0, Math.floor(((Date.parse(finished || new Date().toISOString()) || Date.now()) - Date.parse(started)) / 1000));
    return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, '0')}s`;
  };
  const defaultVertical = () => state.verticals.find((item) => item.key === 'dental')?.key || state.verticals[0]?.key || '';
  const verticalLabel = (item) => ({ dental: 'Dental', detailing: 'Detailing' })[item.key] || item.label || item.key;
  const verticalOptions = (selected = defaultVertical()) => state.verticals.length
    ? state.verticals.map((item) => `<option value="${esc(item.key)}" ${item.key === selected ? 'selected' : ''}>${esc(verticalLabel(item))}</option>`).join('')
    : '<option value="" selected disabled>No verticals configured</option>';

  function batchStatus(job) {
    if (!job) return '<p class="muted">Batch ещё не запускался.</p>';
    const items = (job.items || []).map((item) => `<li>${statusBadge(item.outcome_status || item.status)} <b>${esc(item.company_name)}</b> · ${esc(item.outcome_stage || item.stage || item.status)}${item.outcome_reason ? `<small>${esc(item.outcome_reason)}</small>` : item.error ? `<small class="error-copy">${esc(item.error)}</small>` : ''}</li>`).join('');
    const running = job.status === 'RUNNING';
    const interrupted = ['INTERRUPTED', 'STOPPED'].includes(job.status);
    return `<div class="job-summary"><b>${esc(job.status)}</b><span>${Number(job.processed_count || 0)} / ${Number(job.queued_count || 0)} processed · ${Number(job.progress || 0)}%</span>
      <span>Найдено ${Number(job.discovered_count || 0)} · исследовано ${Number(job.researched_count || 0)} · проанализировано ${Number(job.analyzed_count || 0)}</span>
      <span>Qualification passed ${Number(job.qualification_passed_count ?? job.qualified_count ?? 0)} · rejected ${Number(job.qualification_rejected_count || 0)} · AI errors ${Number(job.ai_errors_count || 0)}</span>
      <span>Drafts generated ${Number(job.drafts_generated_count ?? job.drafts_count ?? job.letters_generated ?? 0)} · Ready for review ${Number(job.ready_for_review_count ?? job.ready_count ?? 0)} · Errors ${Number(job.error_count ?? job.failed ?? 0)}</span>
      ${(job.error_details || []).length ? `<p class="error-copy">${(job.error_details || []).map(esc).join('<br>')}</p>` : ''}
      ${running ? `<span>Сейчас: ${esc(job.current_company || 'Discovery')} · ${esc(job.current_stage || 'Подготовка')}</span><span>Работает ${elapsed(job.started_at, null)}</span><button class="button secondary" data-action="batch-stop" data-id="${esc(job.batch_id)}">Остановить batch</button>` : `<span>${interrupted ? 'Незавершённый batch обнаружен' : `Завершён: ${esc(job.finished_at || '—')}`}</span>`}
      ${interrupted ? `<p class="error-copy">После перезапуска batch не продолжается автоматически.</p><button class="button secondary" data-action="batch-resume" data-id="${esc(job.batch_id)}">Продолжить</button>` : ''}
      <details><summary>Состояние клиник (${(job.items || []).length})</summary><ul class="job-items">${items || '<li>Очередь пуста.</li>'}</ul></details></div>`;
  }

  function sendStatus(job) {
    if (!job) return '<p class="muted">Send batch ещё не запускался.</p>';
    const items = (job.items || []).map((item) => `<li>${statusBadge(item.status)} <b>${esc(item.company_name)}</b> · ${esc(item.recipient)}${item.reason ? `<small>${esc(item.reason)}</small>` : ''}</li>`).join('');
    const canResume = job.status === 'SEND_INTERRUPTED';
    const completed = Number(job.sent_count || 0) + Number(job.simulated_count || 0) + Number(job.failed_count || 0) + Number(job.skipped_count || 0);
    const remaining = Math.max(0, Number(job.planned_count || 0) - completed);
    const waitSeconds = job.next_send_at ? Math.max(0, Math.ceil((Date.parse(job.next_send_at) - Date.now()) / 1000)) : null;
    return `<div class="job-summary"><b>${esc(job.status)}</b><span>${completed} / ${Number(job.planned_count || 0)} · успешно ${Number(job.sent_count || 0) + Number(job.simulated_count || 0)} · ошибки ${Number(job.failed_count || 0)} · осталось ${remaining}</span>
      ${(job.error_details || []).length ? `<p class="error-copy">${(job.error_details || []).map(esc).join('<br>')}</p>` : ''}
      ${job.status === 'RUNNING' ? `<span>Клиника: ${esc(job.current_company || '—')} · recipient: ${esc(job.current_recipient || '—')} · ${esc(job.current_stage || 'Preparing')}</span>${waitSeconds !== null ? `<span>Следующее письмо примерно через ${waitSeconds} сек.</span>` : ''}<span>Работает ${elapsed(job.started_at, null)}</span><button class="button secondary" data-action="send-stop" data-id="${esc(job.send_id)}">Остановить отправку</button>` : ''}
      ${canResume ? `<p class="error-copy">Отправка прервалась. Неопределённый текущий email не повторяется; будут продолжены только оставшиеся PENDING.</p><button class="button secondary" data-action="send-resume-preview" data-id="${esc(job.send_id)}">Продолжить отправку</button>` : ''}
      <details><summary>Состояние писем (${(job.items || []).length})</summary><ul class="job-items">${items || '<li>Нет писем в плане.</li>'}</ul></details></div>`;
  }

  function updateWorkflowPanels() {
    const batchNode = document.getElementById('batch-panel');
    const sendNode = document.getElementById('send-panel');
    if (batchNode) batchNode.innerHTML = `<span class="kicker">NIGHTLY RUN</span><h2>Анализ + генерация писем</h2>
      <p>Одна кнопка запускает поиск, проверку сайтов, анализ, квалификацию и создание писем. Backend продолжит работу после закрытия вкладки.</p>
      ${state.batchApiAvailable ? '' : `<p class="error-copy">Batch API unavailable: ${esc(state.batchApiError || 'unsupported by the running backend')}.</p>`}
      <div class="workflow-form"><label>Vertical<select id="batch-vertical" ${state.verticals.length ? '' : 'disabled'}>${verticalOptions()}</select></label>
      <label>Город / география<input id="batch-city" value="Astana" required></label>
      <label>Количество компаний<input id="batch-count" type="number" min="1" max="100" step="1" value="20" required></label>
      <button class="button primary" data-action="batch-start" ${!state.batchApiAvailable || !state.verticals.length || state.batch?.status === 'RUNNING' ? 'disabled' : ''}>🚀 Запустить ночной поиск</button></div>
      <div class="job-monitor">${batchStatus(state.batch)}</div>`;
    if (sendNode) sendNode.innerHTML = `<span class="kicker">EXPLICIT SEND · SEQUENTIAL</span><h2>Отправка писем</h2>
      <p>Отправка только из Review Queue по зафиксированному снимку выбранных писем. Подтверждение обязательно.</p>
      <p class="ready-count"><b>${state.queue.filter((item) => item.send_eligible).length}</b> готовы · выбрано <b>${state.queue.filter((item) => item.send_eligible && item.selected).length}</b></p>
      ${state.sendApiAvailable ? '' : `<p class="error-copy">Send API unavailable: ${esc(state.sendApiError || 'unsupported by the running backend')}.</p>`}
      <p><button class="button secondary" data-view="review">Открыть Review Queue и выбрать письма</button></p>
      <small class="muted">Режим: <b>${esc(state.settings.smtp_mode || 'SIMULATED')}</b> · Max/batch ${Number(state.settings.max_sends_per_batch || 50)} · daily provider limit ${Number(state.settings.daily_send_limit || 50)}</small>
      <div class="job-monitor">${sendStatus(state.send)}</div>`;
    batchNode?.querySelector('#batch-vertical')?.addEventListener('change', async (event) => {
      if (!state.batchApiAvailable) return;
      try { const result = await get(`/api/batches/eligible?vertical=${encodeURIComponent(event.target.value)}`); state.batchAvailable = result.available || 0; batchNode.querySelector('#batch-available').textContent = String(state.batchAvailable); }
      catch (error) { toast(error.message); }
    });
    sendNode?.querySelector('#send-vertical')?.addEventListener('change', async (event) => {
      if (!state.sendApiAvailable) return;
      try { const result = await get(`/api/send-batches/ready?vertical=${encodeURIComponent(event.target.value)}`); sendNode.querySelector('.ready-count b').textContent = String(result.count || 0); }
      catch (error) { toast(error.message); }
    });
  }

  async function pollJobs() {
    try {
      const [batch, send] = await Promise.all([get('/api/batches/latest'), get('/api/send-batches/latest')]);
      const previousBatch = state.batch;
      const previousSend = state.send;
      const wasRunning = state.batch?.status === 'RUNNING' || state.send?.status === 'RUNNING';
      state.batch = batch.job; state.send = send.job;
      updateWorkflowPanels();
      if (state.view === 'activity') {
        const activityData = await get('/api/activity'); state.activity = activityData.items || []; activity();
      }
      const batchFinished = state.batch && previousBatch?.batch_id === state.batch.batch_id && previousBatch.status !== state.batch.status && state.batch.status !== 'RUNNING';
      const sendFinished = state.send && previousSend?.send_id === state.send.send_id && previousSend.status !== state.send.status && state.send.status !== 'RUNNING';
      if ((wasRunning || batchFinished || sendFinished) && state.batch?.status !== 'RUNNING' && state.send?.status !== 'RUNNING') await refreshAfterJob();
      if (state.batch?.status !== 'RUNNING' && state.send?.status !== 'RUNNING' && jobPollTimer) {
        clearInterval(jobPollTimer); jobPollTimer = null;
      }
    } catch { /* Current view keeps the last persisted snapshot visible. */ }
  }

  async function refreshAfterJob() {
    await load();
    if (state.view === 'overview') overview();
    else if (state.view === 'review') queueView();
    else if (state.view === 'activity') activity();
  }

  function startJobPolling() {
    if (jobPollTimer) return;
    if (state.batch?.status !== 'RUNNING' && state.send?.status !== 'RUNNING') return;
    jobPollTimer = setInterval(pollJobs, 1500);
  }

  async function startAnalysisBatch(button) {
    const vertical = document.getElementById('batch-vertical')?.value || 'dental';
    const requested_count = Number(document.getElementById('batch-count')?.value || 20);
    const city = document.getElementById('batch-city')?.value.trim() || '';
    if (!city || !Number.isInteger(requested_count) || requested_count < 1 || requested_count > 100) { toast('Укажите город и целое количество от 1 до 100.'); return; }
    button.disabled = true;
    button.textContent = 'Запускаем ночной поиск…';
    try {
      const result = await post('/api/night-run/start', { vertical, city, requested_count });
      state.batch = await get(`/api/batches/${encodeURIComponent(result.batch_id)}`).then((payload) => payload.job);
      toast(`Ночной поиск запущен · ${result.city} · ${result.requested_count} компаний`);
      updateWorkflowPanels(); startJobPolling();
    } catch (error) { toast(error.message); button.disabled = false; button.textContent = '🚀 Запустить ночной поиск'; }
  }

  function openSendConfirmation(preview, action = 'confirm') {
    state.sendPreview = { ...preview, action };
    const simulated = preview.mode === 'SIMULATED_SEND';
    const modal = document.createElement('div');
    modal.className = 'modal-backdrop'; modal.id = 'send-confirmation';
    const countReady = Number(preview.ready_count ?? preview.planned_count ?? 0);
    const planned = Number(preview.planned_count ?? (preview.items || []).filter((item) => item.status === 'PENDING').length);
    const sender = preview.sender_email || state.settings.sender_email || '';
    modal.innerHTML = `<section class="evidence-modal send-confirm-modal" role="dialog" aria-modal="true"><header><h2>${action === 'resume' ? 'Продолжить отправку' : 'Отправка писем'}</h2><button class="icon-button" data-close aria-label="Закрыть">×</button></header>
      <p>Готово к отправке: <b>${countReady}</b><br>Выбрано получателей: <b>${Number(preview.recipient_count ?? planned)}</b><br>Будет ${simulated ? 'симулировано' : 'отправлено'}: <b>${planned}</b></p>
      <p>Режим: <b>${simulated ? 'SIMULATED' : 'REAL SMTP'}</b>${simulated ? '' : `<br>Отправитель: ${esc(sender || 'не настроен')}`}<br>Интервал: ${Number(preview.min_delay_seconds || 0)}–${Number(preview.max_delay_seconds || 0)} сек<br>Vertical: ${esc(preview.vertical || '—')}</p>
      <p>${simulated ? 'Симуляция не связывается с почтовым провайдером.' : `Вы собираетесь отправить ${planned} реальных писем. Письма будут реально отправлены последовательно с указанным интервалом.`}</p>
      <footer class="confirm-actions"><button class="button secondary" data-close>Отмена</button><button class="button primary" data-confirm>${action === 'resume' ? 'Подтвердить продолжение' : simulated ? 'Подтвердить симуляцию' : 'Подтвердить отправку'}</button></footer></section>`;
    document.body.appendChild(modal);
    modal.querySelectorAll('[data-close]').forEach((node) => node.addEventListener('click', () => modal.remove()));
    modal.addEventListener('click', (event) => { if (event.target === modal) modal.remove(); });
    modal.querySelector('[data-confirm]').onclick = async () => {
      const confirmButton = modal.querySelector('[data-confirm]'); confirmButton.disabled = true;
      try {
        const endpoint = `/api/send-batches/${encodeURIComponent(preview.send_id)}/${action === 'resume' ? 'resume' : 'confirm'}`;
        await post(endpoint, {});
        modal.remove(); state.send = await get(`/api/send-batches/${encodeURIComponent(preview.send_id)}`).then((payload) => payload.job);
        updateWorkflowPanels(); startJobPolling();
        if (state.send?.status !== 'RUNNING') await refreshAfterJob();
        toast(simulated ? 'SIMULATED_SEND запущен · email не отправляются' : 'Send batch запущен после подтверждения');
      } catch (error) { confirmButton.disabled = false; toast(error.message); }
    };
  }

  async function startSendPreview(button) {
    button.disabled = true;
    const selectedIds = state.queue.filter((item) => item.send_eligible && item.selected).map((item) => Number(item.id));
    const body = {
      vertical: 'all', draft_ids: selectedIds,
      count: selectedIds.length,
      mode: document.getElementById('send-mode')?.value || 'SIMULATED_SEND',
      min_delay_seconds: Number(document.getElementById('send-min-delay')?.value || 0),
      max_delay_seconds: Number(document.getElementById('send-max-delay')?.value || 0),
    };
    try {
      const preview = await post('/api/send-batches/preview', body);
      state.send = await get(`/api/send-batches/${encodeURIComponent(preview.send_id)}`).then((payload) => payload.job);
      openSendConfirmation(preview); updateWorkflowPanels();
    } catch (error) { toast(error.message); button.disabled = false; }
  }

  async function resumeSendPreview(sendId) {
    const job = await get(`/api/send-batches/${encodeURIComponent(sendId)}`).then((payload) => payload.job);
    openSendConfirmation(job, 'resume');
  }

  function leadCard(lead) {
    const draft = state.queue.find((item) => String(item.clinic_id) === String(lead.id));
    return `<article class="lead-card">
      <div class="card-head">${statusBadge(lead.status || 'DISCOVERED')}${qualificationBadge(lead.qualification_status)}</div>
      <h3>${esc(lead.name)}</h3><p class="muted">${esc(lead.city || 'Город не указан')} · ${esc(lead.vertical || 'vertical неизвестен')}</p>
      <div class="lead-facts">
        <div><span>Website</span><b>${esc(lead.website || 'Не подтверждён')}</b></div>
        <div><span>Contacts</span><b>${esc(lead.emails || lead.phone || 'Не найден')}</b></div>
        <div><span>Source</span><b>${esc(lead.source_url || 'Не указан')}</b></div>
        <div><span>AI analysis</span><b>${lead.analysis_count ? 'Есть результат' : 'Не запускался'}</b></div>
      </div>
      <button class="button secondary full" data-action="detail" data-id="${lead.id}">Открыть research</button>
      ${draft ? '<small class="draft-ready">Draft доступен в Review Queue</small>' : ''}
    </article>`;
  }

  function overview() {
    const data = state.dashboard;
    app.innerHTML = `<section class="hero"><div><span class="kicker">COMMAND CENTER</span><h2>Sales intelligence workspace</h2>
      <p>Публичные источники, evidence-bound анализ и подготовка материалов для ручной проверки.</p></div>
      <div class="ai-card"><b>${esc(state.settings.smtp_mode || 'SIMULATED')}</b>
        <span>${esc(state.settings.lm_studio_model || 'Local model not configured')}</span><small>Real sends: ${Number(state.settings.real_sends || 0)} · Approve never sends · each batch needs confirmation</small></div></section>
      <section class="pipeline">${['DISCOVERED', 'ANALYZING', 'ANALYZED', 'QUALIFIED', 'NEEDS_REVIEW', 'LETTER_DONE', 'READY_TO_SEND', 'SENDING', 'SENT', 'SEND_FAILED'].map((stage) => `<span class="pipeline-step">${esc(stage)}</span>`).join('')}</section>
      <section class="metrics">${metric('Discovered', data.clinics)}${metric('Analyzed', data.analyzed)}${metric('Qualified', data.qualified)}${metric('Needs review', data.needs_review)}${metric('Letter done', data.letters_done)}${metric('Ready to send', data.ready_to_send)}${metric('Sent', data.sent)}${metric('Failed', data.failed)}</section>
      <section class="workflow-grid"><article id="batch-panel" class="surface"></article><article id="send-panel" class="surface"></article></section>
      <section class="section-heading"><div><span class="kicker">RECENT LEADS</span><h2>Latest research</h2></div><button class="button secondary" data-view="leads">Все leads</button></section>
      <div class="lead-grid">${state.clinics.slice(0, 6).map(leadCard).join('') || '<div class="empty-state">Пока нет найденных компаний.</div>'}</div>`;
    updateWorkflowPanels();
    startJobPolling();
  }

  function leads() {
    const verticalOptions = state.verticals.map((vertical) => `<option value="${esc(vertical.key)}">${esc(vertical.label)}</option>`).join('');
    app.innerHTML = `<section class="section-heading"><div><span class="kicker">RESEARCH</span><h2>Leads</h2></div></section>
      <section class="lead-filters"><label>Поиск<input id="lead-search" type="search" placeholder="Компания, город, сайт"></label>
      <label>Статус<select id="lead-status"><option value="">Все статусы</option>${['DISCOVERED','RESEARCHED','AI_ERROR','ANALYZED','QUALIFIED','NEEDS_REVIEW','DISQUALIFIED','DRAFTED','APPROVED'].map((x) => `<option>${x}</option>`).join('')}</select></label>
      <label>Vertical<select id="lead-vertical"><option value="">Все verticals</option>${verticalOptions}</select></label>
      <label>Qualification<select id="lead-qualification"><option value="">Все</option><option>QUALIFIED</option><option>NEEDS_REVIEW</option><option>DISQUALIFIED</option></select></label>
      <label>Source<input id="lead-source" type="search" placeholder="URL или источник"></label></section>
      <div id="lead-results" class="lead-grid"></div>`;
    const draw = () => {
      const query = document.getElementById('lead-search').value.trim().toLowerCase();
      const status = document.getElementById('lead-status').value;
      const vertical = document.getElementById('lead-vertical').value;
      const qualification = document.getElementById('lead-qualification').value;
      const source = document.getElementById('lead-source').value.trim().toLowerCase();
      const rows = state.clinics.filter((lead) => {
        const searchable = `${lead.name || ''} ${lead.city || ''} ${lead.website || ''}`.toLowerCase();
        return (!query || searchable.includes(query)) && (!status || lead.status === status)
          && (!vertical || lead.vertical === vertical || lead.category === vertical)
          && (!qualification || lead.qualification_status === qualification)
          && (!source || `${lead.source_url || ''} ${lead.profile_json || ''}`.toLowerCase().includes(source));
      });
      document.getElementById('lead-results').innerHTML = rows.map(leadCard).join('') || '<div class="empty-state">По заданным фильтрам ничего не найдено.</div>';
    };
    app.querySelectorAll('.lead-filters input, .lead-filters select').forEach((input) => input.addEventListener('input', draw));
    draw();
  }

  function queueView() {
    const eligible = state.queue.filter((draft) => draft.send_eligible);
    const selected = eligible.filter((draft) => draft.selected);
    const mode = state.settings.smtp_configured ? `<option value="REAL_SMTP">REAL SMTP · реальные письма</option>` : '';
    const card = (draft) => {
      const whyValue = draft.analysis?.why_this_lead ?? draft.qualification?.reasons;
      const why = Array.isArray(whyValue)
        ? whyValue.slice(0, 2).map(formatAIValue).filter(Boolean).join(' · ')
        : formatAIValue(whyValue);
      const leadReason = why || draft.analysis?.sales_brief || draft.rationale || 'Обоснование не сохранено.';
      const opportunities = formatAIValue(draft.analysis?.opportunities);
      const qualificationReasons = formatAIValue(draft.qualification?.reasons);
      return `<article class="review-row review-candidate ${draft.send_eligible ? '' : 'not-send-eligible'}">
        <label class="candidate-select"><input type="checkbox" data-selection-id="${Number(draft.id)}" ${draft.selected && draft.send_eligible ? 'checked' : ''} ${draft.send_eligible ? '' : 'disabled'} aria-label="Выбрать ${esc(draft.clinic_name)}"></label>
        <div class="candidate-main">${statusBadge(draft.workflow_status)} <h3>${esc(draft.clinic_name || 'Company')}</h3>
          <p class="muted">${esc(draft.city || 'Город не указан')} · ${draft.website ? `<a href="${esc(draft.website)}" target="_blank" rel="noreferrer">${esc(draft.website)}</a>` : 'Website не найден'}</p>
          <p><b>${esc(draft.email || 'Публичный email не найден')}</b> · Priority ${esc(draft.analysis?.priority?.score ?? '—')}</p>
          <p class="candidate-why"><b>WHY THIS LEAD</b> · ${esc(leadReason)}</p>
          <small>${opportunities ? `Opportunity: ${esc(opportunities)} · ` : ''}Angle: ${esc(draft.analysis?.recommended_angle || 'не записан')} · Qualification: ${esc(draft.qualification?.status || 'UNKNOWN')}${qualificationReasons ? ` · ${esc(qualificationReasons)}` : ''} · Letter: ${esc(draft.status || 'UNKNOWN')}</small>
          ${draft.send_eligible ? '' : '<small class="error-copy">Не проходит проверку отправки; выбор заблокирован.</small>'}</div>
        <button class="button secondary" data-action="email" data-id="${draft.id}">Открыть письмо</button></article>`;
    };
    app.innerHTML = `<section class="section-heading"><div><span class="kicker">MORNING REVIEW</span><h2>Review Queue</h2><p>${eligible.length} клиник готовы · выбор хранится в SQLite.</p></div></section>
      <section class="selection-toolbar"><b>Выбрано: <span id="selected-count">${selected.length}</span> из ${eligible.length}</b>
        <div><button class="button secondary" data-action="select-all">Выбрать всех</button><button class="button secondary" data-action="deselect-all">Снять всех</button></div></section>
      <div class="review-list">${state.queue.map(card).join('') || '<div class="empty-state">Писем для review пока нет.</div>'}</div>
      <section class="send-selection surface"><span class="kicker">EXPLICIT SEND · BACKEND WORKER</span><h3>Отправка выбранных писем</h3>
        <div class="workflow-form send-form"><label>Режим<select id="send-mode"><option value="SIMULATED_SEND" selected>SIMULATED · без реального email</option>${mode}</select></label>
        <label>Интервал min, сек<input id="send-min-delay" type="number" min="0" max="86400" value="${state.settings.smtp_configured ? 45 : 0}"></label>
        <label>Интервал max, сек<input id="send-max-delay" type="number" min="0" max="86400" value="${state.settings.smtp_configured ? 120 : 0}"></label>
        <button class="button primary" data-action="send-selected" ${selected.length === 0 || state.send?.status === 'RUNNING' || state.send?.status === 'SEND_INTERRUPTED' ? 'disabled' : ''}>ЗАПУСТИТЬ ${selected.length} ПИСЕМ</button></div>
        <small class="muted">Снимок получателей фиксируется при запуске. SIMULATED — безопасный режим по умолчанию.</small>
        <div class="job-monitor">${sendStatus(state.send)}</div></section>`;
  }

  function activity() {
    const entries = state.activity.map((event) => {
      const label = event.message || `${String(event.type || 'EVENT').replaceAll('_', ' ')} · ${event.status || ''}`;
      return `<article><time>${esc(event.timestamp || '')}</time><b>${esc(String(event.type || 'EVENT').replaceAll('_', ' '))}</b><p>${esc(event.clinic_name || event.company || label)}</p>${event.error ? `<small class="error-copy">${esc(event.error)}</small>` : ''}</article>`;
    }).join('');
    app.innerHTML = `<section class="section-heading"><div><span class="kicker">CONFIRMED BACKEND EVENTS</span><h2>Activity</h2></div></section>
      <div class="timeline">${entries || '<div class="empty-state">Activity пока пуста.</div>'}</div>`;
  }

  function settings() {
    const settingsData = state.settings;
    app.innerHTML = `<section class="section-heading"><div><span class="kicker">CONFIGURATION</span><h2>Settings</h2></div></section>
      <div class="settings-grid"><article class="surface"><span class="kicker">LOCAL AI</span><h3>${esc(settingsData.lm_studio_model || 'Not configured')}</h3><p>${esc(settingsData.lm_studio_url || '')}</p><p>AI provider: LM Studio on the local machine.</p></article>
      <article class="surface"><span class="kicker">EMAIL SAFETY</span><h3>${esc(settingsData.smtp_mode || 'SIMULATED')}</h3><p>Provider: ${settingsData.smtp_configured ? 'SMTP configured' : 'SIMULATED only'}<br>Sender: ${esc(settingsData.sender_email || 'не настроен')}<br>Real sends recorded: ${Number(settingsData.real_sends || 0)}</p><p>Approval never sends an email. Real sending requires a count-limited preview and explicit confirmation.</p>${settingsData.smtp_config_error ? `<p class="muted">${esc(settingsData.smtp_config_error)}</p>` : ''}<p>Max/batch ${Number(settingsData.max_sends_per_batch || 50)} · Daily limit ${Number(settingsData.daily_send_limit || 50)} · Min delay ${Number(settingsData.min_send_delay_seconds || 30)} sec</p></article>
      <article class="surface"><span class="kicker">VERTICALS</span><h3>${state.verticals.map((item) => esc(item.label)).join(', ')}</h3><p>Discovery keywords and message config are loaded from backend vertical settings.</p></article></div>`;
  }

  const signalNames = { website: 'Website', mobile: 'Mobile', online_booking: 'Booking', whatsapp: 'WhatsApp', chat_widget: 'Chat widget', crm: 'CRM', ai_assistant: 'AI Assistant', online_payment: 'Payment', automation: 'Automation' };
  const statusText = { CONFIRMED: 'Confirmed', INFERRED: 'Inferred', UNKNOWN: 'Unknown', NOT_DETECTED: 'Not detected' };

  function evidenceModal(title, evidence, fallback) {
    const rows = evidence.filter(Boolean).map((item) => `<article class="evidence-item"><b>${esc(item.fact || item.claim || 'Evidence')}</b>
      <p>${esc(item.snippet || item.detection_reason || item.reason || '')}</p>
      <small>Status: ${esc(item.status || 'UNKNOWN')} · Confidence: ${esc(item.confidence || 'Unknown')}</small>
      <small>Observed at: ${esc(item.observed_at || 'Not recorded')}</small>
      ${item.source || item.source_url ? `<a href="${esc(item.source || item.source_url)}" target="_blank" rel="noreferrer">${esc(item.source || item.source_url)}</a>` : '<small>Source URL is unavailable.</small>'}</article>`).join('');
    const modal = document.createElement('div');
    modal.className = 'modal-backdrop';
    modal.innerHTML = `<section class="evidence-modal"><header><h2>${esc(title)}</h2><button class="icon-button" data-close aria-label="Закрыть">×</button></header>
      <p>${esc(fallback || 'No supporting evidence available.')}</p><div class="evidence-list">${rows || '<p class="muted">No supporting evidence available.</p>'}</div></section>`;
    document.body.appendChild(modal);
    modal.querySelector('[data-close]').onclick = () => modal.remove();
    modal.onclick = (event) => { if (event.target === modal) modal.remove(); };
  }

  function qualificationPanel(qualification) {
    if (!qualification) return '<p>Qualification не запускалась.</p>';
    return `<h3>Qualification · ${esc(qualification.status)}</h3><ul class="reason-list">${(qualification.reasons || []).map((reason) => `<li>${esc(reason)}</li>`).join('') || '<li>Нет explanation.</li>'}</ul>
      <div class="factor-list">${Object.entries(qualification.factors || {}).map(([name, value]) => `<span>${esc(name.replaceAll('_', ' '))}: <b>${esc(value)}</b></span>`).join('')}</div>`;
  }

  async function showDetail(id, outcome = null) {
    try {
      const detail = await get(`/api/clinics/${id}`);
      state.detail = detail;
      const clinic = detail.clinic;
      const profile = clinic.profile || {};
      const analysis = detail.analysis;
      const qualification = profile.qualification;
      const evidence = detail.evidence || [];
      const stateData = analysis?.digital_state || {};
      const signalGrid = Object.entries(signalNames).map(([key, label]) => {
        const claim = stateData[key] || { status: 'UNKNOWN', reason: 'No backend analysis for this signal.', evidence_ids: [] };
        return `<div class="signal"><span>${esc(label)}</span><b>${esc(statusText[claim.status] || claim.status || 'Unknown')}</b>
          <button data-action="why" data-key="${esc(key)}">Why?</button></div>`;
      }).join('');
      const contactRows = (detail.contacts || []).map((contact) => `<li>${esc(contact.email)} · ${esc(contact.contact_type || contact.kind || 'PUBLIC_BUSINESS_EMAIL')} · ${esc(contact.confidence || 'unknown')} · <a href="${esc(contact.source_url || '#')}" target="_blank" rel="noreferrer">source</a></li>`).join('');
      const evidenceRows = evidence.map((item) => `<li><b>${esc(item.fact || 'Evidence')}</b> — ${esc(item.status || 'UNKNOWN')} · <a href="${esc(item.source || '#')}" target="_blank" rel="noreferrer">${esc(item.source || 'Source unavailable')}</a></li>`).join('');
      const draft = detail.draft;
      const draftStatus = draft ? (detail.workflow_status || (draft.status === 'DRAFTED' ? 'LETTER DONE' : draft.status)) : '';
      const history = detail.analysis_history || [];
      app.innerHTML = `<button class="button secondary" data-view="leads">← Leads</button><section class="detail-header"><span class="badge real">${esc(clinic.status)}</span>
        <h2>${esc(clinic.name)}</h2><p>${esc(clinic.city || 'Город не указан')} · ${esc(clinic.website || 'Website unavailable')}</p><p>Vertical: ${esc(clinic.category || 'unknown')} · Source: ${esc(clinic.source_url || 'unknown')}</p>
        <button class="button primary" data-action="analyze" data-id="${clinic.id}" data-repeat="${analysis ? 'true' : 'false'}" ${state.analysisBusy.has(String(clinic.id)) ? 'disabled' : ''}>${analysis ? 'Повторить анализ' : 'Анализировать'}</button>
        <span id="analysis-state" role="status"></span></section>
        <div class="detail-grid"><article class="surface"><span class="kicker">DIGITAL PRESENCE</span><div class="signal-grid">${signalGrid}</div></article>
        <article class="surface"><span class="kicker">AI ANALYSIS</span>${analysis ? `<h3>${esc(formatAIValue(analysis.company_summary || analysis.sales_brief) || 'Analysis completed')}</h3>
          <p>Analysis completed · ${esc(analysis.created_at || 'timestamp unavailable')} · ${history.length} run(s)</p><p>Priority: ${esc(analysis.priority?.score ?? 'Unknown')}</p><p>Recommended angle: ${esc(analysis.recommended_angle || 'Unknown')}</p>
          <button class="button secondary" data-action="why-summary">Why?</button>${history.length > 1 ? `<details><summary>История анализов</summary><ul>${history.map((run) => `<li>${esc(run.created_at)} · ${esc(run.recommended_angle || 'Angle unavailable')}</li>`).join('')}</ul></details>` : ''}` : '<p>AI analysis ещё не запускался.</p>'}</article>
        <article class="surface qualification-panel"><span class="kicker">QUALIFICATION</span>${qualificationPanel(qualification)}</article>
        <article class="surface"><span class="kicker">PUBLIC CONTACTS</span><ul class="contact-list">${contactRows || '<li>Публичный контакт с provenance не найден.</li>'}</ul>
          ${(profile.conflicts || []).length ? `<button class="button secondary" data-action="why-conflicts">Contact conflicts · ${(profile.conflicts || []).length}</button>` : ''}</article>
        <article class="surface evidence-section"><span class="kicker">EVIDENCE</span><ul>${evidenceRows || '<li>Подтверждённые evidence пока не сохранены.</li>'}</ul>
          <p>NOT_DETECTED не трактуется как ABSENT.</p></article>
        <article class="surface"><span class="kicker">WEBSITE AUDIT</span><h3>${esc(profile.website_status || profile.website_audit?.website_status || 'UNKNOWN')}</h3>
          <p>${esc(profile.website_audit?.title || 'Название страницы не сохранено')}</p><p>${esc(profile.website_audit?.url || clinic.website || 'URL неизвестен')}</p></article></div>
        ${draft ? `<section class="surface email-inline"><span class="kicker">${esc(draftStatus)}</span><h3>${esc(draft.subject)}</h3><p>Письмо сохранено в SQLite · ${esc(draft.created_at || '')}</p>
          <pre>${esc(draft.plain_text_body || draft.body || '')}</pre><button class="button secondary" data-action="email-detail">Открыть письмо</button>
          <button class="button secondary" data-action="draft-regenerate" data-id="${clinic.id}">Повторно сгенерировать</button></section>` : ''}`;
      if (outcome) {
        const statusNode = document.getElementById('analysis-state');
        if (statusNode) statusNode.innerHTML = `<b class="success-copy">Анализ завершён · ${esc(outcome.elapsed)} сек</b><p>Qualification · ${esc(outcome.status)}${analysis?.recommended_angle ? ` · ${esc(analysis.recommended_angle)}` : ''}</p>`;
      }
      state.detailEvidence = evidence;
      state.detailAnalysis = analysis;
      state.detailProfile = profile;
    } catch (error) {
      app.innerHTML = `<div class="empty-state">Не удалось загрузить компанию: ${esc(error.message)}</div>`;
    }
  }

  async function analyzeLead(id, button, repeat = false) {
    const key = String(id);
    if (state.analysisBusy.has(key)) return;
    if (repeat && !window.confirm('Повторить анализ этой клиники? Старый анализ останется в истории.')) return;
    state.analysisBusy.add(key);
    const started = performance.now();
    const message = document.getElementById('analysis-state');
    const updateTimer = () => {
      const seconds = Math.floor((performance.now() - started) / 1000);
      if (button) button.textContent = `Анализируется · ${seconds} сек`;
      if (message) message.innerHTML = `<span class="spinner inline-spinner"></span> Локальная AI-модель · LM Studio<br>Ожидание ответа от локальной модели... · ${seconds} сек`;
    };
    if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); }
    updateTimer();
    const timer = setInterval(updateTimer, 500);
    try {
      const result = await post(`/api/clinics/${id}/analyze`, { repeat });
      clearInterval(timer);
      const elapsed = ((performance.now() - started) / 1000).toFixed(1);
      await load();
      const status = result.qualification?.status || 'NEEDS_REVIEW';
      await showDetail(id, { elapsed, status });
      toast(`Анализ завершён · ${elapsed} сек · ${status}`);
      if (result.draft_warning) toast(result.draft_warning);
    } catch (error) {
      clearInterval(timer);
      if (message) message.innerHTML = `<b class="error-copy">AI_ERROR · анализ не завершён</b><p>${esc(error.message || 'Локальный анализ не удалось завершить.')}</p>`;
      if (button) { button.disabled = false; button.removeAttribute('aria-busy'); button.textContent = 'Повторить анализ'; }
    } finally {
      state.analysisBusy.delete(key);
      const activeButton = [...document.querySelectorAll('[data-action="analyze"]')].find((item) => item.dataset.id === key);
      if (activeButton) {
        activeButton.disabled = false;
        activeButton.removeAttribute('aria-busy');
        if (activeButton.textContent.startsWith('Анализируется')) activeButton.textContent = repeat ? 'Повторить анализ' : 'Анализировать';
      }
    }
  }

  function openEmail(draft, email) {
    if (!draft) return;
    const evidenceRows = (draft.evidence || []).map((item) => `<li><b>${esc(item.fact || 'Evidence')}</b> · ${esc(item.status || 'UNKNOWN')} · ${item.source || item.source_url ? `<a href="${esc(item.source || item.source_url)}" target="_blank" rel="noreferrer">Source</a>` : 'Source unavailable'}</li>`).join('');
    const analysis = draft.analysis || {};
    const modal = document.createElement('div');
    modal.className = 'modal-backdrop';
    modal.innerHTML = `<section class="email-review"><header><div><span class="kicker">PERSONALIZED OUTREACH</span><h2>Email Review</h2></div><button class="icon-button" data-close aria-label="Закрыть">×</button></header>
      <div class="email-meta"><div><span>TO</span><b>${esc(email || draft.email || 'Публичный email не найден')}</b></div><div><span>SUBJECT</span><b>${esc(draft.subject || '')}</b></div><div><span>STATUS</span><b>${esc(draft.status || '')}</b></div></div>
      <div class="review-tabs"><button class="active" data-tab="plain">Plain text</button><button data-tab="html">HTML preview</button></div>
      <div class="email-content" id="email-content"><pre>${esc(draft.plain_text_body || draft.body || '')}</pre></div>
      <aside class="why-email"><h3>Rationale and evidence</h3><p>${esc(draft.rationale || 'Rationale is not available.')}</p><p>Recommended angle: ${esc(analysis.recommended_angle || 'Not recorded')}</p><p>${esc((draft.source_observations || []).join(' · ') || 'No supporting evidence available.')}</p><ul>${evidenceRows || '<li>No supporting evidence available.</li>'}</ul></aside>
      <footer><button class="button secondary" data-approve ${draft.status === 'SENT' || draft.status === 'APPROVED' ? 'disabled' : ''}>${draft.status === 'APPROVED' ? '✓ Approved' : 'Approve for review'}</button>
      <button class="button primary" disabled>Отправка запускается из Review Queue</button><small>Mode: ${esc(state.settings.smtp_mode || 'SIMULATED')} · real sends recorded: ${Number(state.settings.real_sends || 0)}</small></footer></section>`;
    document.body.appendChild(modal);
    modal.querySelector('[data-close]').onclick = () => modal.remove();
    modal.onclick = (event) => { if (event.target === modal) modal.remove(); };
    modal.querySelector('[data-approve]').onclick = async () => {
      try {
        await post('/api/drafts/status', { id: draft.id, status: 'APPROVED' });
        await load(); modal.remove(); toast('Draft approved for human review');
        if (state.view === 'review') queueView();
      } catch (error) { toast(error.message); }
    };
    modal.querySelectorAll('[data-tab]').forEach((tab) => tab.onclick = () => {
      modal.querySelectorAll('[data-tab]').forEach((node) => node.classList.toggle('active', node === tab));
      const content = modal.querySelector('#email-content');
      content.replaceChildren();
      if (tab.dataset.tab === 'plain') {
        const pre = document.createElement('pre'); pre.textContent = draft.plain_text_body || draft.body || ''; content.append(pre);
      } else {
        const frame = document.createElement('iframe'); frame.className = 'html-frame'; frame.title = 'HTML email preview'; frame.setAttribute('sandbox', '');
        frame.srcdoc = draft.html_body || `<pre>${esc(draft.plain_text_body || draft.body || '')}</pre>`; content.append(frame);
      }
    });
  }

  async function discoveryView() {
    const options = verticalOptions();
    const hasVerticals = state.verticals.length > 0;
    app.innerHTML = `<section class="surface"><span class="kicker">LIVE PUBLIC SOURCE DISCOVERY</span><h2>Найти компании</h2>
      ${hasVerticals ? '' : '<p class="error-copy">No verticals are configured by the backend. Discovery is disabled.</p>'}
      <div class="form-grid"><label>Город<input id="discovery-city" value="Astana" required></label>
        <label>Vertical<select id="discovery-vertical" required ${hasVerticals ? '' : 'disabled'}>${options}</select></label>
        <label>Лимит<input id="discovery-target" type="number" min="1" max="100" step="1" value="10" required></label>
        <button class="button primary" data-action="discover" ${hasVerticals ? '' : 'disabled'}>Запустить поиск</button></div>
      <div id="discovery-status" role="status" aria-live="polite"></div><div id="discovery-log" class="timeline"></div></section>`;
    if (state.discovery) renderDiscoveryState(state.discovery);
    else {
      try {
        const latest = await get('/api/discovery/latest');
        if (latest.run) { state.discovery = latest.run; renderDiscoveryState(state.discovery); }
      } catch (error) {
        document.getElementById('discovery-status').innerHTML = `<p class="error-copy">Backend API unavailable: ${esc(error.message)}</p>`;
      }
    }
  }

  function renderDiscoveryState(run) {
    const box = document.getElementById('discovery-status');
    const log = document.getElementById('discovery-log');
    if (!box || !log) return;
    const running = !['COMPLETE', 'FAILED', 'INTERRUPTED', 'BACKEND_UNAVAILABLE'].includes(run.status);
    const button = app.querySelector('[data-action="discover"]');
    if (button) { button.disabled = running; button.innerHTML = running ? '<span class="spinner inline-spinner"></span> Поиск...' : 'Запустить поиск'; }
    const statusLabels = {
      SEARCHING_SOURCES: 'Searching sources...', RESOLVING_DUPLICATES: 'Resolving duplicates...',
      INSPECTING_WEBSITES: 'Inspecting websites...', FINDING_PUBLIC_CONTACTS: 'Finding public contacts...',
      PERSISTING_RESULTS: 'Сохраняем найденные компании...', INTERRUPTED: 'Поиск прерван перезапуском сервера.',
    };
    const sourceStatuses = Object.values(run.source_status || {});
    const providersUnavailable = sourceStatuses.length > 0 && sourceStatuses.every((value) => value === 'SOURCE_UNAVAILABLE');
    if (run.status === 'COMPLETE') box.innerHTML = `<p class="success-copy">Поиск завершён · найдено ${Number(run.count || 0)}</p>`;
    else if (run.status === 'BACKEND_UNAVAILABLE') box.innerHTML = `<p class="error-copy">Backend API unavailable</p><p>${esc(run.error || 'The discovery endpoint could not be reached.')}</p>`;
    else if (run.status === 'FAILED' || run.status === 'INTERRUPTED') box.innerHTML = `<p class="error-copy">${providersUnavailable ? 'SOURCE_UNAVAILABLE' : run.status === 'INTERRUPTED' ? 'Поиск остановлен' : 'Не удалось выполнить поиск'}</p><p>${esc(run.error || 'Источник не вернул подтверждённые результаты.')}</p>`;
    else box.innerHTML = `<p><span class="spinner inline-spinner"></span> ${esc(statusLabels[run.status] || 'Поиск...')}</p>`;
    const providers = Object.entries(run.source_status || {}).map(([name, value]) => `<span class="badge ${value === 'SUCCESS' ? 'approved' : 'unknown'}">${esc(name)} · ${esc(value)}</span>`).join(' ');
    const events = (run.activity || []).map((event) => `<article><b>${esc(event.type || 'DISCOVERY')}</b><p>${esc(event.message || (event.count != null ? `Найдено: ${event.count}` : event.provider || ''))}</p>${event.error ? `<small class="error-copy">${esc(event.error)}</small>` : ''}</article>`).join('');
    const candidates = (run.candidates || []).map((lead) => `<article class="discovery-result"><b>${esc(lead.name || 'Company name unavailable')}</b><p>${esc(lead.city || run.city || '')} · ${esc(lead.website || 'Website unavailable')}</p>${lead.source_url ? `<a href="${esc(lead.source_url)}" target="_blank" rel="noreferrer">Public source</a>` : ''}</article>`).join('');
    log.innerHTML = `${providers ? `<p>${providers}</p>` : ''}${events}${candidates}`;
  }

  async function startDiscovery(button) {
    const vertical = document.getElementById('discovery-vertical')?.value || '';
    const city = document.getElementById('discovery-city')?.value.trim() || '';
    const targetCount = document.getElementById('discovery-target')?.valueAsNumber;
    if (!vertical || !state.verticals.some((item) => item.key === vertical)) {
      document.getElementById('discovery-status').innerHTML = '<p class="error-copy">Choose a configured vertical.</p>';
      return;
    }
    if (!city || !Number.isInteger(targetCount) || targetCount < 1 || targetCount > 100) {
      document.getElementById('discovery-status').innerHTML = '<p class="error-copy">Enter a city and a whole-number limit from 1 to 100.</p>';
      return;
    }
    button.disabled = true;
    button.innerHTML = '<span class="spinner inline-spinner"></span> Поиск...';
    state.discovery = { status: 'SEARCHING_SOURCES', activity: [], source_status: {} };
    renderDiscoveryState(state.discovery);
    try {
      const started = await post('/api/discovery/start', {
        city,
        vertical,
        target_count: targetCount,
      });
      state.discovery = { ...state.discovery, ...started };
      renderDiscoveryState(state.discovery);
      while (!['COMPLETE', 'FAILED', 'INTERRUPTED'].includes(state.discovery.status)) {
        await new Promise((resolve) => setTimeout(resolve, 600));
        state.discovery = await get(`/api/discovery/${encodeURIComponent(started.run_id)}`);
        renderDiscoveryState(state.discovery);
      }
      await load();
      if (state.discovery.status === 'COMPLETE') toast(`Поиск завершён · ${Number(state.discovery.count || 0)} компаний`);
    } catch (error) {
      state.discovery = { status: 'BACKEND_UNAVAILABLE', error: error.message, source_status: {}, activity: [] };
      renderDiscoveryState(state.discovery);
    }
  }

  async function showActivity() {
    const result = await get('/api/activity');
    state.activity = result.items || [];
    activity();
  }

  async function render(view) {
    nav(view);
    app.innerHTML = '<section class="loading-screen"><span class="spinner"></span><p>Загрузка…</p></section>';
    try {
      if (view === 'overview') overview();
      else if (view === 'leads') leads();
      else if (view === 'review') queueView();
      else if (view === 'activity') await showActivity();
      else if (view === 'settings') settings();
      else if (view === 'discovery') await discoveryView();
    } catch (error) { app.innerHTML = `<div class="empty-state">${esc(error.message)}</div>`; }
  }

  document.addEventListener('click', async (event) => {
    const viewButton = event.target.closest('[data-view]');
    if (viewButton) { event.preventDefault(); await render(viewButton.dataset.view); return; }
    const action = event.target.closest('[data-action]');
    if (!action) return;
    if (action.dataset.action === 'detail') { await showDetail(action.dataset.id); return; }
    if (action.dataset.action === 'discover') { await startDiscovery(action); return; }
    if (action.dataset.action === 'analyze') { await analyzeLead(action.dataset.id, action, action.dataset.repeat === 'true'); return; }
    if (action.dataset.action === 'batch-start') { await startAnalysisBatch(action); return; }
    if (action.dataset.action === 'batch-resume') {
      action.disabled = true;
      try { await post(`/api/batches/${encodeURIComponent(action.dataset.id)}/resume`, {}); state.batch = await get(`/api/batches/${encodeURIComponent(action.dataset.id)}`).then((payload) => payload.job); updateWorkflowPanels(); startJobPolling(); }
      catch (error) { action.disabled = false; toast(error.message); }
      return;
    }
    if (action.dataset.action === 'batch-stop') {
      action.disabled = true;
      try { await post(`/api/batches/${encodeURIComponent(action.dataset.id)}/stop`, {}); await pollJobs(); toast('Остановка batch запрошена. Текущий этап завершится безопасно.'); }
      catch (error) { action.disabled = false; toast(error.message); }
      return;
    }
    if (action.dataset.action === 'send-preview') { await startSendPreview(action); return; }
    if (action.dataset.action === 'send-selected') { await startSendPreview(action); return; }
    if (action.dataset.action === 'send-stop') {
      action.disabled = true;
      try { await post(`/api/send-batches/${encodeURIComponent(action.dataset.id)}/stop`, {}); await pollJobs(); toast('Остановка отправки запрошена. Текущая операция завершится.'); }
      catch (error) { action.disabled = false; toast(error.message); }
      return;
    }
    if (action.dataset.action === 'select-all' || action.dataset.action === 'deselect-all') {
      try { await post('/api/queue/selection', { select_all: action.dataset.action === 'select-all', selected: action.dataset.action === 'select-all' }); await load(); queueView(); }
      catch (error) { toast(error.message); }
      return;
    }
    if (action.dataset.action === 'send-resume-preview') { try { await resumeSendPreview(action.dataset.id); } catch (error) { toast(error.message); } return; }
    if (action.dataset.action === 'draft-regenerate') {
      if (!window.confirm('Повторно сгенерировать письмо? Текущий draft останется в истории как superseded.')) return;
      action.disabled = true;
      try { await post(`/api/clinics/${encodeURIComponent(action.dataset.id)}/draft/regenerate`, {}); await load(); await showDetail(action.dataset.id); toast('Новое письмо сохранено как LETTER DONE'); }
      catch (error) { action.disabled = false; toast(error.message); }
      return;
    }
    if (action.dataset.action === 'email') {
      const draft = state.queue.find((item) => String(item.id) === String(action.dataset.id));
      openEmail(draft, draft?.email); return;
    }
    if (action.dataset.action === 'email-detail') {
      const draft = state.detail?.draft;
      openEmail(draft ? { ...draft, evidence: state.detail.evidence, analysis: state.detail.analysis } : null, state.detail?.contacts?.find((item) => item.email)?.email); return;
    }
    if (action.dataset.action === 'why') {
      const claim = state.detailAnalysis?.digital_state?.[action.dataset.key] || {};
      const ids = new Set(claim.evidence_ids || []);
      const evidence = (state.detailEvidence || []).filter((item) => ids.has(item.evidence_id));
      evidenceModal(`${signalNames[action.dataset.key] || action.dataset.key} · ${statusText[claim.status] || claim.status || 'Unknown'}`, evidence, claim.reason); return;
    }
    if (action.dataset.action === 'why-summary') {
      const analysis = state.detailAnalysis || {};
      const refs = new Set(Object.values(analysis.digital_state || {}).flatMap((claim) => claim.evidence_ids || []));
      evidenceModal('AI analysis · Why?', (state.detailEvidence || []).filter((item) => refs.has(item.evidence_id)), (analysis.why_this_lead || []).join(' ')); return;
    }
    if (action.dataset.action === 'why-conflicts') {
      evidenceModal('Contact conflicts', state.detailProfile?.conflicts || [], 'Значения из разных public sources сохранены для ручной сверки.');
    }
  });

  document.addEventListener('change', async (event) => {
    const checkbox = event.target.closest('[data-selection-id]');
    if (checkbox) {
      checkbox.disabled = true;
      try {
        await post('/api/queue/selection', { draft_ids: [Number(checkbox.dataset.selectionId)], selected: checkbox.checked });
        const draft = state.queue.find((item) => Number(item.id) === Number(checkbox.dataset.selectionId));
        if (draft) draft.selected = checkbox.checked;
        queueView();
      } catch (error) { checkbox.checked = !checkbox.checked; checkbox.disabled = false; toast(error.message); }
      return;
    }
    if (event.target.id === 'send-mode') {
      const button = app.querySelector('[data-action="send-selected"]');
      const count = state.queue.filter((item) => item.send_eligible && item.selected).length;
      if (button) button.textContent = event.target.value === 'REAL_SMTP' ? `ОТПРАВИТЬ ${count} ПИСЕМ` : `ЗАПУСТИТЬ ${count} ПИСЕМ`;
    }
  });

  load().then(() => render('overview')).catch((error) => {
    topStatus.textContent = 'Backend API unavailable';
    app.innerHTML = `<section class="empty-state">Backend API unavailable: ${esc(error.message)}</section>`;
  });
})();
