(() => {
  'use strict';

  const app = document.getElementById('app');
  const pageTitle = document.getElementById('page-title');
  const topStatus = document.getElementById('top-status');
  const state = {
    view: 'overview', clinics: [], queue: [], settings: {}, dashboard: {},
    activity: [], verticals: [], detail: null, discovery: null, analysisBusy: new Set(),
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
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
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
    Object.assign(state, {
      dashboard, clinics: clinics.items || [], queue: queue.items || [],
      activity: activity.items || [], settings, verticals: verticals.items || [],
    });
    topStatus.textContent = health.lm_studio
      ? `Локальная AI-модель · ${settings.lm_studio_model}`
      : 'LM Studio недоступен';
    document.getElementById('sidebar-provider').textContent = health.lm_studio
      ? `LM Studio · ${settings.lm_studio_model}` : 'LM Studio недоступен';
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
      <div class="ai-card"><b>${state.settings.smtp_enabled ? 'SMTP disabled in this build' : 'Sending disabled'}</b>
        <span>${esc(state.settings.lm_studio_model || 'Local model not configured')}</span><small>Real sends: 0</small></div></section>
      <section class="pipeline">${['DISCOVERED', 'RESEARCHED', 'ANALYZED', 'QUALIFIED', 'DRAFTED', 'REVIEW', 'APPROVED', 'READY_TO_SEND', 'SENT'].map((stage) => `<span class="pipeline-step">${esc(stage)}</span>`).join('')}</section>
      <section class="metrics">${metric('Total leads', data.clinics)}${metric('Researched', data.researched)}${metric('Qualified', data.qualified)}${metric('Needs review', data.needs_review)}${metric('Drafts', data.drafted)}${metric('Ready to review', data.ready_to_review)}</section>
      <section class="section-heading"><div><span class="kicker">RECENT LEADS</span><h2>Latest research</h2></div><button class="button secondary" data-view="leads">Все leads</button></section>
      <div class="lead-grid">${state.clinics.slice(0, 6).map(leadCard).join('') || '<div class="empty-state">Пока нет найденных компаний.</div>'}</div>`;
  }

  function leads() {
    const verticalOptions = state.verticals.map((vertical) => `<option value="${esc(vertical.key)}">${esc(vertical.label)}</option>`).join('');
    app.innerHTML = `<section class="section-heading"><div><span class="kicker">RESEARCH</span><h2>Leads</h2></div></section>
      <section class="lead-filters"><label>Поиск<input id="lead-search" type="search" placeholder="Компания, город, сайт"></label>
      <label>Статус<select id="lead-status"><option value="">Все статусы</option>${['DISCOVERED','RESEARCHED','ANALYZED','QUALIFIED','NEEDS_REVIEW','DISQUALIFIED','DRAFTED','APPROVED'].map((x) => `<option>${x}</option>`).join('')}</select></label>
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
    app.innerHTML = `<section class="section-heading"><div><span class="kicker">HUMAN REVIEW</span><h2>Review Queue</h2></div></section>
      <div class="review-list">${state.queue.map((draft) => `<article class="review-row"><div>${statusBadge(draft.status)}<h3>${esc(draft.clinic_name || 'Company')}</h3><p class="muted">${esc(draft.email || 'Публичный email не найден')} · ${esc(draft.subject || '')}</p></div>
      ${qualificationBadge(state.clinics.find((lead) => String(lead.id) === String(draft.clinic_id))?.qualification_status)}
      <button class="button secondary" data-action="email" data-id="${draft.id}">Открыть письмо</button></article>`).join('') || '<div class="empty-state">Нет сохранённых draft для ручной проверки.</div>'}</div>`;
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
      <article class="surface"><span class="kicker">EMAIL SAFETY</span><h3>SMTP disabled</h3><p>Provider: simulated only<br>Real sends: 0</p><p>Draft approval never sends an email.</p></article>
      <article class="surface"><span class="kicker">VERTICALS</span><h3>${state.verticals.map((item) => esc(item.label)).join(', ')}</h3><p>Discovery keywords and message config are loaded from backend vertical settings.</p></article></div>`;
  }

  const signalNames = { website: 'Website', mobile: 'Mobile', online_booking: 'Booking', whatsapp: 'WhatsApp', crm: 'CRM', ai_assistant: 'AI Assistant', online_payment: 'Payment', automation: 'Automation' };
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
      app.innerHTML = `<button class="button secondary" data-view="leads">← Leads</button><section class="detail-header"><span class="badge real">${esc(clinic.status)}</span>
        <h2>${esc(clinic.name)}</h2><p>${esc(clinic.city || 'Город не указан')} · ${esc(clinic.website || 'Website unavailable')}</p><p>Vertical: ${esc(clinic.category || 'unknown')} · Source: ${esc(clinic.source_url || 'unknown')}</p>
        <button class="button primary" data-action="analyze" data-id="${clinic.id}" ${state.analysisBusy.has(String(clinic.id)) ? 'disabled' : ''}>Анализировать</button>
        <span id="analysis-state" role="status"></span></section>
        <div class="detail-grid"><article class="surface"><span class="kicker">DIGITAL PRESENCE</span><div class="signal-grid">${signalGrid}</div></article>
        <article class="surface"><span class="kicker">AI ANALYSIS</span>${analysis ? `<h3>${esc(formatAIValue(analysis.company_summary || analysis.sales_brief) || 'Analysis completed')}</h3>
          <p>Priority: ${esc(analysis.priority?.score ?? 'Unknown')}</p><p>Recommended angle: ${esc(analysis.recommended_angle || 'Unknown')}</p>
          <button class="button secondary" data-action="why-summary">Why?</button>` : '<p>AI analysis ещё не запускался.</p>'}</article>
        <article class="surface qualification-panel"><span class="kicker">QUALIFICATION</span>${qualificationPanel(qualification)}</article>
        <article class="surface"><span class="kicker">PUBLIC CONTACTS</span><ul class="contact-list">${contactRows || '<li>Публичный контакт с provenance не найден.</li>'}</ul>
          ${(profile.conflicts || []).length ? `<button class="button secondary" data-action="why-conflicts">Contact conflicts · ${(profile.conflicts || []).length}</button>` : ''}</article>
        <article class="surface evidence-section"><span class="kicker">EVIDENCE</span><ul>${evidenceRows || '<li>Подтверждённые evidence пока не сохранены.</li>'}</ul>
          <p>NOT_DETECTED не трактуется как ABSENT.</p></article>
        <article class="surface"><span class="kicker">WEBSITE AUDIT</span><h3>${esc(profile.website_status || profile.website_audit?.website_status || 'UNKNOWN')}</h3>
          <p>${esc(profile.website_audit?.title || 'Название страницы не сохранено')}</p><p>${esc(profile.website_audit?.url || clinic.website || 'URL неизвестен')}</p></article></div>
        ${draft ? `<section class="surface email-inline"><span class="kicker">SAVED DRAFT · ${esc(draft.status)}</span><h3>${esc(draft.subject)}</h3><p>Plain text сохраняется в SQLite. Отправка отключена.</p>
          <pre>${esc(draft.plain_text_body || draft.body || '')}</pre><button class="button secondary" data-action="email-detail">Открыть письмо</button></section>` : ''}`;
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

  async function analyzeLead(id, button) {
    const key = String(id);
    if (state.analysisBusy.has(key)) return;
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
      const result = await post(`/api/clinics/${id}/analyze`, {});
      clearInterval(timer);
      const elapsed = ((performance.now() - started) / 1000).toFixed(1);
      await load();
      const status = result.qualification?.status || 'NEEDS_REVIEW';
      await showDetail(id, { elapsed, status });
      toast(`Анализ завершён · ${elapsed} сек · ${status}`);
      if (result.draft_warning) toast(result.draft_warning);
    } catch (error) {
      clearInterval(timer);
      if (message) message.innerHTML = `<b class="error-copy">Не удалось завершить анализ</b><p>Проверьте, что LM Studio запущен и модель доступна.</p><small>${esc(error.message)}</small>`;
      if (button) { button.disabled = false; button.removeAttribute('aria-busy'); button.textContent = 'Повторить анализ'; }
    } finally {
      state.analysisBusy.delete(key);
      const activeButton = [...document.querySelectorAll('[data-action="analyze"]')].find((item) => item.dataset.id === key);
      if (activeButton) {
        activeButton.disabled = false;
        activeButton.removeAttribute('aria-busy');
        if (activeButton.textContent.startsWith('Анализируется')) activeButton.textContent = 'Анализировать';
      }
    }
  }

  function openEmail(draft, email) {
    if (!draft) return;
    const modal = document.createElement('div');
    modal.className = 'modal-backdrop';
    modal.innerHTML = `<section class="email-review"><header><div><span class="kicker">PERSONALIZED OUTREACH</span><h2>Email Review</h2></div><button class="icon-button" data-close aria-label="Закрыть">×</button></header>
      <div class="email-meta"><div><span>TO</span><b>${esc(email || draft.email || 'Публичный email не найден')}</b></div><div><span>SUBJECT</span><b>${esc(draft.subject || '')}</b></div><div><span>STATUS</span><b>${esc(draft.status || '')}</b></div></div>
      <div class="review-tabs"><button class="active" data-tab="plain">Plain text</button><button data-tab="html">HTML preview</button></div>
      <div class="email-content" id="email-content"><pre>${esc(draft.plain_text_body || draft.body || '')}</pre></div>
      <aside class="why-email"><h3>Rationale and evidence</h3><p>${esc(draft.rationale || 'Rationale is not available.')}</p><p>${esc((draft.source_observations || []).join(' · '))}</p></aside>
      <footer><button class="button secondary" data-approve ${draft.status === 'SENT' || draft.status === 'APPROVED' ? 'disabled' : ''}>${draft.status === 'APPROVED' ? '✓ Approved' : 'Approve for review'}</button>
      <button class="button primary" disabled>Отправка отключена</button><small>SMTP disabled · real sends: 0</small></footer></section>`;
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
    const options = state.verticals.map((item) => `<option value="${esc(item.key)}">${esc(item.label)}</option>`).join('');
    app.innerHTML = `<section class="surface"><span class="kicker">LIVE PUBLIC SOURCE DISCOVERY</span><h2>Найти компании</h2>
      <div class="form-grid"><label>Город<input id="discovery-city" value="Астана"></label>
        <label>Vertical<select id="discovery-vertical">${options}</select></label>
        <label>Лимит<input id="discovery-target" type="number" min="1" max="100" value="5"></label>
        <button class="button primary" data-action="discover">Запустить поиск</button></div>
      <div id="discovery-status" role="status" aria-live="polite"></div><div id="discovery-log" class="timeline"></div></section>`;
    if (state.discovery) renderDiscoveryState(state.discovery);
    else {
      try {
        const latest = await get('/api/discovery/latest');
        if (latest.run) { state.discovery = latest.run; renderDiscoveryState(state.discovery); }
      } catch (error) {
        document.getElementById('discovery-status').innerHTML = `<p class="error-copy">Не удалось загрузить сохранённый результат: ${esc(error.message)}</p>`;
      }
    }
  }

  function renderDiscoveryState(run) {
    const box = document.getElementById('discovery-status');
    const log = document.getElementById('discovery-log');
    if (!box || !log) return;
    const running = !['COMPLETE', 'FAILED', 'INTERRUPTED'].includes(run.status);
    const button = app.querySelector('[data-action="discover"]');
    if (button) { button.disabled = running; button.innerHTML = running ? '<span class="spinner inline-spinner"></span> Поиск...' : 'Запустить поиск'; }
    const statusLabels = {
      SEARCHING_SOURCES: 'Searching sources...', RESOLVING_DUPLICATES: 'Resolving duplicates...',
      INSPECTING_WEBSITES: 'Inspecting websites...', FINDING_PUBLIC_CONTACTS: 'Finding public contacts...',
      PERSISTING_RESULTS: 'Сохраняем найденные компании...', INTERRUPTED: 'Поиск прерван перезапуском сервера.',
    };
    if (run.status === 'COMPLETE') box.innerHTML = `<p class="success-copy">Поиск завершён · найдено ${Number(run.count || 0)}</p>`;
    else if (run.status === 'FAILED' || run.status === 'INTERRUPTED') box.innerHTML = `<p class="error-copy">${run.status === 'INTERRUPTED' ? 'Поиск остановлен' : 'Не удалось выполнить поиск'}</p><p>${esc(run.error || 'Источник не вернул подтверждённые результаты.')}</p>`;
    else box.innerHTML = `<p><span class="spinner inline-spinner"></span> ${esc(statusLabels[run.status] || 'Поиск...')}</p>`;
    const providers = Object.entries(run.source_status || {}).map(([name, value]) => `<span class="badge ${value === 'SUCCESS' ? 'approved' : 'unknown'}">${esc(name)} · ${value === 'SUCCESS' ? 'доступен' : 'источник недоступен'}</span>`).join(' ');
    const events = (run.activity || []).map((event) => `<article><b>${esc(event.type || 'DISCOVERY')}</b><p>${esc(event.message || (event.count != null ? `Найдено: ${event.count}` : event.provider || ''))}</p>${event.error ? `<small class="error-copy">${esc(event.error)}</small>` : ''}</article>`).join('');
    const candidates = (run.candidates || []).map((lead) => `<article class="discovery-result"><b>${esc(lead.name || 'Company name unavailable')}</b><p>${esc(lead.city || run.city || '')} · ${esc(lead.website || 'Website unavailable')}</p>${lead.source_url ? `<a href="${esc(lead.source_url)}" target="_blank" rel="noreferrer">Public source</a>` : ''}</article>`).join('');
    log.innerHTML = `${providers ? `<p>${providers}</p>` : ''}${events}${candidates}`;
  }

  async function startDiscovery(button) {
    button.disabled = true;
    button.innerHTML = '<span class="spinner inline-spinner"></span> Поиск...';
    state.discovery = { status: 'SEARCHING_SOURCES', activity: [], source_status: {} };
    renderDiscoveryState(state.discovery);
    try {
      const started = await post('/api/discovery/start', {
        city: document.getElementById('discovery-city').value,
        vertical: document.getElementById('discovery-vertical').value,
        target_count: Number(document.getElementById('discovery-target').value || 5),
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
      state.discovery = { status: 'FAILED', error: error.message, source_status: {}, activity: [] };
      renderDiscoveryState(state.discovery);
    }
  }

  function showActivity() { activity(); }

  async function render(view) {
    nav(view);
    app.innerHTML = '<section class="loading-screen"><span class="spinner"></span><p>Загрузка…</p></section>';
    try {
      if (view === 'overview') overview();
      else if (view === 'leads') leads();
      else if (view === 'review') queueView();
      else if (view === 'activity') showActivity();
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
    if (action.dataset.action === 'analyze') { await analyzeLead(action.dataset.id, action); return; }
    if (action.dataset.action === 'email') {
      const draft = state.queue.find((item) => String(item.id) === String(action.dataset.id));
      openEmail(draft, draft?.email); return;
    }
    if (action.dataset.action === 'email-detail') {
      const draft = state.detail?.draft;
      openEmail(draft, state.detail?.contacts?.find((item) => item.email)?.email); return;
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

  load().then(() => render('overview')).catch((error) => {
    topStatus.textContent = 'API unavailable';
    app.innerHTML = `<section class="empty-state">Не удалось загрузить workspace: ${esc(error.message)}</section>`;
  });
})();
