'use strict';

const state = { me: null, csrf: '', view: '', contacts: [], templates: [], campaigns: [], settings: null, importFile: null, importRegion: '', importPreview: null };
const $ = (selector, root = document) => root.querySelector(selector);
const fmt = n => new Intl.NumberFormat('en-US').format(Number(n || 0));
const money = n => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(n || 0));
const date = value => value ? new Date(value).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : 'Not recorded';
const titleCase = text => String(text || '').replaceAll('_', ' ').replace(/\b\w/g, s => s.toUpperCase());
const icons = {
 overview: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
 contacts: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M16 3a4 4 0 0 1 0 8 M22 21v-2a4 4 0 0 0-3-3.87 M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
 upload: 'M12 16V3 M7 8l5-5 5 5 M20 16v4H4v-4',
 templates: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z M14 2v6h6 M8 13h8 M8 17h6',
 campaign: 'M3 11l18-8-8 18-2-8-8-2z M11 13l5-5',
 inbox: 'M21 11.5a8.5 8.5 0 0 1-8.5 8.5H3l2-5a8.5 8.5 0 1 1 16-3.5z',
 settings: 'M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8 M20 12l2-1-2-4-2 1-2-1V4H8v3L6 8 4 7 2 11l2 1v2l-2 1 2 4 2-1 2 1v2h8v-2l2-1 2 1 2-4-2-1z',
 diagnostics: 'M3 17l5-6 4 3 6-10 M3 3v18h18',
 check: 'M20 6L9 17l-5-5',
 clock: 'M12 8v4l3 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
 shield: 'M12 3l8 4v5c0 5-8 9-8 9s-8-4-8-9V7z M8 12l3 3 5-5',
 pause: 'M8 4v16 M16 4v16',
 search: 'M21 21l-6-6 M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0',
 plus: 'M12 5v14 M5 12h14',
 arrow: 'M5 12h14 M14 7l5 5-5 5',
 download: 'M12 3v13 M7 11l5 5 5-5 M4 18v3h16v-3',
 cost: 'M12 2v20 M17 5H9a4 4 0 0 0 0 8h6a4 4 0 0 1 0 8H6',
 warning: 'M12 3L2 21h20z M12 9v5 M12 17h.01',
};
function icon(name) {
 const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
 for (const [k, v] of Object.entries({ viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.6', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' })) svg.setAttribute(k, v);
 const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', icons[name] || icons.overview); svg.append(path); return svg;
}
function el(tag, attrs = {}, ...children) {
 const node = document.createElement(tag);
 for (const [key, value] of Object.entries(attrs || {})) {
  if (value == null || value === false) continue;
  if (key === 'class') node.className = value;
  else if (key === 'text') node.textContent = value;
  else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
  else if (key === 'checked' || key === 'disabled' || key === 'hidden' || key === 'multiple' || key === 'required') node[key] = Boolean(value);
  else node.setAttribute(key, value === true ? '' : value);
 }
 for (const child of children.flat(Infinity)) if (child != null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
 return node;
}
function button(label, action, cls = '', iconName) {
 const b = el('button', { class: 'button ' + cls, type: 'button' }, iconName ? icon(iconName) : null, label);
 b.addEventListener('click', async () => { if (b.disabled) return; b.disabled = true; try { await action(); } catch (e) { toast(e.message, true); } finally { b.disabled = false; } }); return b;
}
function badge(text, kind) {
 const good = ['verified', 'approved', 'delivered', 'sent', 'accepted', 'eligible', 'completed', 'interested', 'qualified'];
 const bad = ['suppressed', 'failed', 'filtered', 'complaint', 'wrong_number', 'optout', 'opt_out', 'revoked'];
 const warn = ['pending', 'blocked', 'unknown', 'paused', 'draft', 'review', 'ambiguous', 'renewal_review', 'reply hold'];
 return el('span', { class: 'badge ' + (kind || (good.includes(text) ? 'good' : bad.includes(text) ? 'bad' : warn.includes(text) ? 'warn' : '')) }, titleCase(text));
}
function toast(message, error = false) {
 const node = el('div', { class: 'toast' + (error ? ' error' : '') }, String(message)); $('#toast-root').append(node); setTimeout(() => node.remove(), error ? 9000 : 5000);
}
async function api(path, method = 'GET', data) {
 const options = { method, credentials: 'same-origin', headers: {} };
 if (method !== 'GET') options.headers['X-CSRF-Token'] = state.csrf;
 if (data instanceof FormData) options.body = data;
 else if (data != null) { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(data); }
 const response = await fetch(path, options);
 const type = response.headers.get('content-type') || '';
 const payload = type.includes('json') ? await response.json() : { detail: await response.text() };
 if (!response.ok) {
  if (response.status === 401 && path !== '/api/login') showLogin();
  const detail = payload.detail || payload.error || 'The request could not be completed.';
  throw new Error(typeof detail === 'string' ? detail : detail.message ? detail.message + ((detail.reasons || []).length ? ': ' + detail.reasons.join('; ') : '') : JSON.stringify(detail));
 }
 return payload;
}
function showLogin() { if (activeModalClose) activeModalClose(); $('#app').hidden = true; $('#login').hidden = false; state.me = null; state.csrf = ''; }
function showApp(me) {
 state.me = me.user || me; state.csrf = me.csrf || ''; state.mode = me.mode || 'simulation'; state.businessName = me.business_name || '101XVC';
 $('#login').hidden = true; $('#app').hidden = false;
 $('#user-email').textContent = state.me.email || 'Operator'; $('#user-role').textContent = titleCase(state.me.role || 'operator');
 $('#user-avatar').textContent = (state.me.email || 'OP').slice(0, 2).toUpperCase();
 $('#workspace-name').textContent = state.businessName + ' workspace';
 $('#sidebar-environment').textContent = titleCase(state.mode); $('#environment-pill').textContent = state.mode.toUpperCase(); $('#environment-pill').classList.toggle('production', state.mode === 'production'); $('#global-pause').disabled = !canWrite();
 buildNav(); navigate();
}
function isAdmin() { return state.me && ['admin', 'administrator'].includes(state.me.role); }
function canWrite() { return state.me && ['admin', 'administrator', 'reviewer', 'operator'].includes(state.me.role); }
function canApprove() { return state.me && ['admin', 'administrator', 'reviewer'].includes(state.me.role); }
const navItems = [ ['dashboard', 'Overview', 'overview'], ['contacts', 'Contacts & properties', 'contacts'], ['import', 'Import contacts', 'upload'], ['templates', 'Messages & variants', 'templates'], ['campaigns', 'Campaigns', 'campaign'], ['inbox', 'Conversation inbox', 'inbox'], ['settings', 'Twilio & safeguards', 'settings'], ['diagnostics', 'Diagnostics & audit', 'diagnostics'] ];
function buildNav() {
 const nav = $('#navigation'); nav.replaceChildren();
 navItems.forEach(([id, label, ic], i) => { if (i === 0 || i === 6) nav.append(el('div', { class: 'nav-section-label' }, i === 0 ? 'WORKSPACE' : 'OPERATIONS')); nav.append(el('a', { href: '#' + id, class: 'nav-link', 'data-view': id }, icon(ic), label)); });
}
function heading(title, description, actions = []) {
 return el('div', { class: 'page-heading' }, el('div', {}, el('span', { class: 'eyebrow' }, 'YOUR OUTREACH, IN VIEW'), el('h1', {}, title), el('p', {}, description)), el('div', { class: 'heading-actions' }, actions));
}
function notice(title, message, kind = '') { return el('div', { class: 'notice ' + kind }, icon(kind === 'danger' || kind === 'warning' ? 'warning' : 'shield'), el('div', {}, el('strong', {}, title + ' '), el('span', {}, message))); }
function card(title, subtitle, content, actions = []) { return el('section', { class: 'card' }, el('div', { class: 'card-header' }, el('div', {}, el('h3', {}, title), subtitle ? el('p', {}, subtitle) : null), el('div', { class: 'table-actions' }, actions)), content); }
function empty(title, description, action) { return el('div', { class: 'empty' }, icon('inbox'), el('strong', {}, title), el('p', {}, description), action); }
function table(headers, rows, emptyText = 'No records yet.') {
 const t = el('table', {}, el('thead', {}, el('tr', {}, headers.map(h => el('th', { scope: 'col' }, h)))), el('tbody', {}, rows.map(row => el('tr', {}, row.map(cell => el('td', {}, cell))))));
 return el('div', { class: 'table-wrap' }, rows.length ? t : empty(emptyText, 'Records will appear here as you work.'));
}
function names(c) { return [c.first_name, c.last_name].filter(Boolean).join(' ') || c.phone || 'Contact'; }
function reasons(items) { return el('ul', { class: 'reason-list' }, (items || []).map(item => el('li', {}, typeof item === 'string' ? item : JSON.stringify(item)))); }
function detail(label, value) { return el('div', { class: 'detail-item' }, el('span', {}, label), el('strong', {}, value == null || value === '' ? 'Not recorded' : value)); }
function safeJSON(data) { return el('pre', { class: 'code-text' }, JSON.stringify(data, null, 2)); }
let activeModalClose = null;
function modal(title, subtitle, wide = false) {
 if (activeModalClose) activeModalClose();
 const overlay = el('div', { class: 'modal-overlay' }); const panel = el('section', { class: 'modal' + (wide ? ' wide' : ''), role: 'dialog', 'aria-modal': 'true', 'aria-label': title, tabindex: '-1' });
 const close = () => { overlay.remove(); document.removeEventListener('keydown', handleKey); previousFocus?.focus(); if (activeModalClose === close) activeModalClose = null; };
 activeModalClose = close;
 const previousFocus = document.activeElement;
 const handleKey = e => { if (e.key === 'Escape') close(); if (e.key === 'Tab') { const all = [...panel.querySelectorAll('button:not(:disabled), input, select, textarea, a[href]')]; if (!all.length) { e.preventDefault(); return; } const first = all[0], last = all[all.length - 1]; if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); } } };
 const body = el('div', { class: 'modal-body' }); panel.append(el('div', { class: 'modal-header' }, el('div', {}, el('h2', {}, title), el('p', {}, subtitle)), button('×', close, 'ghost small')), body); overlay.append(panel); $('#modal-root').replaceChildren(overlay); overlay.addEventListener('click', e => { if (e.target === overlay) close(); }); document.addEventListener('keydown', handleKey); panel.focus(); return { body, close, panel };
}
let fieldSequence = 0;
function field(label, name, options = {}) {
 const id = 'field-' + (++fieldSequence);
 let input;
 if (options.type === 'select') input = el('select', { name, required: options.required }, (options.options || []).map(o => { const value = typeof o === 'object' ? o.value : o, text = typeof o === 'object' ? o.label : titleCase(o); return el('option', { value }, text); }));
 else if (options.type === 'textarea') input = el('textarea', { name, rows: options.rows || 5, required: options.required, placeholder: options.placeholder || '' });
 else input = el('input', { name, type: options.type || 'text', required: options.required, placeholder: options.placeholder, min: options.min, max: options.max, step: options.step, autocomplete: options.autocomplete || 'off' });
 if (options.value != null) input.value = options.value;
 input.id = id; if (options.hint) input.setAttribute('aria-describedby', id + '-hint');
 const container = el('div', { class: 'field' + (options.wide ? ' field-wide' : '') }, el('label', { for: id }, label), input, options.hint ? el('span', { class: 'field-hint', id: id + '-hint' }, options.hint) : null); return { node: container, input };
}
function formModal(title, subtitle, fields, submitLabel, onSubmit, extra) {
 const m = modal(title, subtitle); const form = el('form'); const grid = el('div', { class: 'form-grid' }); fields.forEach(f => grid.append(field(f.label, f.name, f).node)); const err = el('p', { class: 'form-error', role: 'alert' }); const save = el('button', { class: 'button primary', type: 'submit' }, submitLabel); if (extra) form.append(extra); form.append(grid, err, el('div', { class: 'form-buttons' }, button('Cancel', m.close), save)); form.addEventListener('submit', async e => { e.preventDefault(); save.disabled = true; err.textContent = ''; try { const data = Object.fromEntries(new FormData(form)); await onSubmit(data); m.close(); } catch (error) { err.textContent = error.message; } finally { save.disabled = false; } }); m.body.append(form); return m;
}
async function navigate() {
 if (!state.me) return;
 const view = location.hash.slice(1).split('?')[0] || 'dashboard'; state.view = navItems.some(n => n[0] === view) ? view : 'dashboard';
 $('#sidebar').classList.remove('open'); document.querySelectorAll('.nav-link').forEach(a => a.classList.toggle('active', a.dataset.view === state.view)); $('#topbar-title').textContent = navItems.find(n => n[0] === state.view)[1]; const main = $('#main'); main.replaceChildren(el('div', { class: 'loading' }, 'Loading workspace…'));
 try { await ({ dashboard: dashboardView, contacts: contactsView, import: importView, templates: templatesView, campaigns: campaignsView, inbox: inboxView, settings: settingsView, diagnostics: diagnosticsView })[state.view](); }
 catch (error) { main.replaceChildren(heading('Unable to load this view', 'Your records remain stored.'), notice('Request failed.', error.message, 'danger'), button('Try again', navigate, 'primary')); }
}
function refresh() { return navigate(); }

async function dashboardView() {
 const d = await api('/api/dashboard'); if (state.view !== 'dashboard') return;
 const counts = d.counts || {}, inbound = d.inbound || {};
 const metrics = [ ['Delivered', counts.delivered, 'Confirmed delivery callbacks', 'check'], ['Awaiting dispatch', (counts.queued || 0), 'Eligibility checked again before sending', 'clock'], ['Interested replies', (inbound.interested || 0) + (inbound.qualified || 0), 'Opt-outs excluded from engagement', 'inbox'], ['Eligible contacts', d.eligible_contacts, fmt(d.contacts) + ' total contacts', 'contacts'], ['Estimated cost', money(d.estimated_cost), d.actual_cost == null ? 'Actual not yet reported' : 'Actual reported: ' + money(d.actual_cost), 'cost'] ];
 const flow = [ ['Connect your account', 'Read-only verification of service and sender access.', 'settings', false], ['Import & review eligibility', 'Document consent and recipient timezone.', 'contacts', d.eligible_contacts > 0], ['Write & approve', 'Exact previews of an immutable message version.', 'templates', false], ['Schedule & monitor', 'One policy gate governs every dispatch.', 'campaigns', false] ];
 const main = $('#main'); main.replaceChildren(heading('Overview', 'Campaigns, conversations, and operational health in one view.', canWrite() ? [button('New campaign', () => { location.hash = '#campaigns'; }, 'primary', 'plus')] : []));
 main.append(d.global_pause ? notice('Sending is paused.', d.pause_reason || 'Review the cause in settings before resuming.', 'danger') : notice(state.mode === 'simulation' ? 'Simulation is active.' : 'Production mode is active.', state.mode === 'simulation' ? 'Work through the complete workflow using synthetic delivery events. No carrier messages are sent.' : 'All outbound messages remain subject to consent, suppression, approval, and operational gates.'));
 main.append(el('div', { class: 'metrics' }, metrics.map(([label, val, sub, ic]) => el('div', { class: 'card metric' }, el('div', { class: 'metric-top' }, label, icon(ic)), el('strong', { class: 'metric-value' }, typeof val === 'string' ? val : fmt(val)), el('div', { class: 'metric-detail' }, sub)))));
 const outcomes = ['accepted', 'delivered', 'sent', 'queued', 'blocked', 'failed', 'filtered', 'unknown']; const maximum = Math.max(1, ...outcomes.map(key => Number(counts[key] || 0)));
 const chart = el('div', { class: 'chart' }, outcomes.map(key => { const bar = el('div', { class: 'chart-bar' }); bar.style.height = Math.max(3, Number(counts[key] || 0) / maximum * 125) + 'px'; if (['failed', 'filtered'].includes(key)) bar.style.background = 'var(--red)'; if (['blocked', 'unknown'].includes(key)) bar.style.background = 'var(--amber)'; return el('div', { class: 'chart-column' }, el('strong', {}, fmt(counts[key])), bar, el('span', {}, titleCase(key))); }));
 const flowList = el('ol', { class: 'steps' }, flow.map(([name, desc, target, done], index) => el('li', { class: 'step' }, el('span', { class: 'step-number' + (done ? ' done' : '') }, done ? '✓' : index + 1), el('div', {}, el('strong', {}, name), el('p', {}, desc)), el('a', { href: '#' + target }, 'Open →'))));
 const deliveryCard = card('Delivery outcomes', 'Counts across all campaigns in the current workspace', el('div', { class: 'card-body' }, chart, el('p', { class: 'inline-note spaced' }, 'Unknown attempts stay held for reconciliation. A timeout can still mean provider acceptance.')), [button('Diagnostics', () => { location.hash = '#diagnostics'; }, 'small ghost')]);
 const flowCard = card('Your next steps', 'Connect → Import → Review → Write → Schedule', el('div', { class: 'card-body' }, flowList));
 main.append(el('div', { class: 'dashboard-grid' }, deliveryCard, flowCard));
 const signalTiles = el('div', { class: 'summary-items' }, ['interested', 'opt_out', 'negative', 'complaint', 'wrong_number', 'review'].map(key => el('div', { class: 'info-tile' }, el('span', {}, titleCase(key)), el('strong', {}, fmt(inbound[key])))));
 const signalCard = card('Conversation signals', 'Interest and adverse responses tracked separately', el('div', { class: 'card-body' }, signalTiles, el('p', { class: 'inline-note' }, 'Oldest queued message: ' + ((counts.queued || 0) ? fmt(d.queue_age_seconds) + ' seconds' : 'none') + '. Replies hold further automated outreach.')));
 main.append(el('div', { class: 'section-grid' }, card('Campaign activity', 'Reviewed campaigns and their current state', campaignTable((d.campaigns || []).slice(0, 5), false)), signalCard));
}

async function contactsView(search = '') {
 let offset = 0; const limit = 250;
 const contacts = await api('/api/contacts?limit=' + limit + '&offset=0' + (search ? '&search=' + encodeURIComponent(search) : ''));
 state.contacts = Array.isArray(contacts) ? contacts : contacts.contacts || [];
 if (state.view !== 'contacts') return;
 const main = $('#main'); main.replaceChildren(heading('Contacts & properties', 'Review consent, properties, and eligibility for each recipient.', canWrite() ? [button('Import CSV', () => { location.hash = '#import'; }, 'primary', 'upload')] : []));
 const searchBox = el('input', { type: 'search', placeholder: 'Search name, phone, or property…', 'aria-label': 'Search contacts', value: search }); let timer;
 const count = el('span', { class: 'inline-note' }); const holder = el('div');
 async function loadPage(nextOffset) { const rows = await api('/api/contacts?limit=' + limit + '&offset=' + nextOffset + '&search=' + encodeURIComponent(searchBox.value)); offset = nextOffset; state.contacts = Array.isArray(rows) ? rows : rows.contacts || []; drawContacts(); }
 searchBox.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => loadPage(0).catch(e => toast(e.message, true)), 300); });
 function drawContacts() {
  count.textContent = 'Showing ' + (state.contacts.length ? offset + 1 : 0) + '–' + (offset + state.contacts.length);
  const prev = button('← Previous', () => loadPage(Math.max(0, offset - limit)), 'small'); prev.disabled = offset === 0;
  const next = button('Next →', () => loadPage(offset + limit), 'small'); next.disabled = state.contacts.length < limit;
  const rows = state.contacts.map(c => [el('div', {}, el('span', { class: 'table-main' }, names(c)), el('span', { class: 'table-sub' }, fmt(c.property_count || (c.properties || []).length) + ' properties')), c.phone, c.timezone || el('span', { class: 'muted' }, 'Needs review'), badge((c.eligibility || []).some(r => String(r).toLowerCase().startsWith('suppressed')) ? 'suppressed' : c.reply_hold ? 'reply hold' : (c.eligibility || []).length ? 'blocked' : c.eligible === false ? 'blocked' : 'eligible'), button('Review', () => contactDetail(c.id), 'small')]);
  holder.replaceChildren(card('Contact directory', 'One recipient record, with separate property records', table(['Contact', 'Phone', 'Timezone', 'Eligibility', ''], rows, 'No contacts match this view.')), el('div', { class: 'form-buttons spaced' }, prev, next));
 }
 main.append(notice('Importing organizes records.', 'Only documented, verified consent appropriate to your business and purpose can establish messaging eligibility.'), el('div', { class: 'toolbar' }, el('div', { class: 'search-input' }, icon('search'), searchBox), count), holder); drawContacts();
}
async function contactDetail(id) {
 const d = await api('/api/contacts/' + encodeURIComponent(id)), c = d.contact || d;
 const m = modal(names(c), 'Recipient record, property context, and permission history.', true);
 const info = el('div', { class: 'detail-grid' }, detail('Phone', c.phone), detail('Recipient timezone', c.timezone), detail('Timezone evidence', c.timezone_source), detail('Automatic reply hold', c.reply_hold ? 'Active: a person replied' : 'Inactive'));
 const blocked = [...(d.eligibility || c.eligibility || [])]; if (c.reply_hold) blocked.push('Automated outreach is held after an inbound reply. Review this conversation before allowing future automation.');
 m.body.append(info, blocked.length ? notice('Messaging is blocked.', 'Resolve the evidence gaps below before approval.', 'warning') : notice('Eligible for policy review.', 'Sending schedule, limits, sender verification, and campaign approval still apply.'), reasons(blocked));
 if (d.suppression?.active) m.body.append(el('div', { class: 'small-card spaced' }, badge('suppressed'), el('p', {}, typeof d.suppression === 'object' ? (d.suppression.reason || 'Suppression record is active.') : 'Suppression record is active.')));
 m.body.append(el('div', { class: 'table-actions spaced' }, canApprove() ? button('Record recipient timezone', () => timezoneModal(id, c), 'small') : null, canWrite() ? button('Add consent evidence', () => consentModal(id), 'small primary') : null, canWrite() ? button('Suppress contact', () => suppressModal(id), 'small danger-outline') : null, c.reply_hold && canApprove() ? button('Review automation hold', () => resumeContactModal(id), 'small') : null));
 m.body.append(el('div', { class: 'inline-heading' }, el('h3', {}, 'Properties')), (d.properties || []).length ? table(['Street address', 'City / state', 'Property ID'], d.properties.map(p => [p.street_address || 'No address', [p.city, p.state, p.zip].filter(Boolean).join(', '), p.property_id || p.id])) : empty('No properties recorded.', 'Property records are kept separately from recipient records.'));
 if ((d.suppression_history || []).length) m.body.append(el('div', { class: 'inline-heading' }, el('h3', {}, 'Suppression history')), table(['Recorded', 'Action', 'Reason'], d.suppression_history.map(h => [date(h.created_at), titleCase(h.action), h.reason])));
 m.body.append(el('div', { class: 'inline-heading' }, el('h3', {}, 'Consent evidence'), el('span', { class: 'inline-note' }, 'CSV consent remains pending')));
 for (const consent of d.consents || []) {
  m.body.append(el('div', { class: 'small-card' }, el('div', { class: 'card-row' }, el('div', {}, el('h3', {}, consent.business || 'Business not specified'), el('p', {}, (consent.channel || 'sms') + ' · ' + (consent.purpose || 'Purpose not specified'))), badge(consent.status)), el('div', { class: 'detail-grid spaced' }, detail('Source', consent.source), detail('Recorded permission', date(consent.occurred_at)), detail('Disclosure version', consent.disclosure_version), detail('Evidence reference', consent.evidence_ref)), consent.status === 'pending' && canApprove() ? button('Review & verify this evidence', () => verifyConsentModal(consent), 'small') : null));
 }
 if (!(d.consents || []).length) m.body.append(empty('No consent evidence.', 'An uploaded phone number is not permission to send SMS.'));
}
function timezoneModal(id, c) { formModal('Record recipient timezone', 'Use recipient evidence. A property address or area code is not proof.', [{ label: 'IANA timezone', name: 'timezone', required: true, value: c.timezone || '', placeholder: 'America/Denver' }, { label: 'Evidence source', name: 'source', required: true, wide: true, placeholder: 'Recipient-provided timezone recorded on…' }], 'Save evidence', async data => { await api('/api/contacts/' + id + '/timezone', 'POST', { timezone: data.timezone, source: data.source, timezone_source: data.source }); toast('Recipient timezone evidence saved.'); }); }
function consentModal(id) { formModal('Add consent evidence', 'Saved as pending until a reviewer verifies the documented evidence.', [{ label: 'Business identity', name: 'business', required: true, value: state.businessName }, { label: 'Channel', name: 'channel', type: 'select', options: ['sms'] }, { label: 'Purpose', name: 'purpose', required: true, value: 'seller_outreach' }, { label: 'Source', name: 'source', required: true, placeholder: 'Website opt-in form' }, { label: 'Permission timestamp', name: 'occurred_at', type: 'datetime-local', required: true }, { label: 'Disclosure version', name: 'disclosure_version', required: true }, { label: 'Evidence reference', name: 'evidence_ref', required: true, wide: true, placeholder: 'Internal record reference or evidence location' }], 'Record evidence', async data => { data.occurred_at = new Date(data.occurred_at).toISOString(); await api('/api/contacts/' + id + '/consents', 'POST', data); toast('Consent evidence recorded for review.'); }); }
function verifyConsentModal(c) { formModal('Verify consent evidence', 'Verify identity, purpose, disclosure, timestamp, and supporting evidence before proceeding.', [{ label: 'Evidence review note', name: 'review_note', type: 'textarea', required: true, wide: true, placeholder: 'Describe what you reviewed and why it establishes permission.' }], 'Verify evidence', async data => { await api('/api/consents/' + c.id + '/verify', 'POST', data); toast('Consent evidence verified. Suppression restoration remains evidence-dependent.'); }, notice('Renewed permission is required for restoration.', 'An admin toggle or a generic positive reply is insufficient. New evidence must postdate revocation.')); }
function suppressModal(id) { formModal('Suppress this recipient', 'Pending outreach will be canceled across business senders.', [{ label: 'Reason', name: 'reason', required: true, type: 'select', options: [{ value: 'manual_review', label: 'Manual review' }, { value: 'optout', label: 'Opt-out request' }, { value: 'wrong_number', label: 'Wrong number' }, { value: 'complaint', label: 'Complaint' }] }, { label: 'Notes', name: 'note', type: 'textarea', wide: true }], 'Apply suppression', async data => { await api('/api/contacts/' + id + '/suppress', 'POST', data); toast('Recipient suppressed. Pending outreach canceled.'); }); }

const importFields = ['first_name', 'last_name', 'phone', 'street_address', 'city', 'state', 'zip', 'property_id', 'timezone', 'timezone_source', 'timezone_evidence_ref', 'opted_out', 'custom_fields', 'consent_business', 'consent_channel', 'consent_purpose', 'consent_source', 'consent_occurred_at', 'consent_disclosure_version', 'consent_evidence_ref', 'consent_status'];
async function importView() {
 const main = $('#main'); main.replaceChildren(heading('Import contacts', 'Preview the file, map its columns, and review the result before saving.'));
 const container = el('div'); main.append(notice('Consent is reviewed separately.', 'CSV evidence is recorded as pending. Importing, reimporting, and new campaigns preserve the authoritative suppression history.'), container);
 const progress = step => el('div', { class: 'import-progress' }, ['Upload', 'Map & review', 'Confirm'].map((name, i) => el('div', { class: 'import-step' + (step === i ? ' active' : '') }, el('span', {}, i + 1), name)));
 function showUpload() {
  state.importPreview = null; const fileInput = el('input', { type: 'file', accept: '.csv,text/csv', required: true, 'aria-label': 'Choose CSV file' });
  const region = field('Country for national-format phone numbers', 'region', { type: 'select', options: [{ value: '', label: 'Require internationally formatted numbers' }, { value: 'US', label: 'United States (+1)' }, { value: 'CA', label: 'Canada (+1)' }, { value: 'GB', label: 'United Kingdom (+44)' }, { value: 'AU', label: 'Australia (+61)' }], hint: 'Select explicitly when the file contains numbers without a country code.' });
  const form = el('form'); const err = el('p', { class: 'form-error' }); const submit = el('button', { type: 'submit', class: 'button primary' }, 'Preview file →');
  form.append(el('div', { class: 'dropzone' }, icon('upload'), el('h3', {}, 'Start with a CSV file'), el('p', {}, 'Up to 8 MiB or 50,000 rows. Nothing is committed until you confirm.'), fileInput), region.node, err, el('div', { class: 'form-buttons' }, submit));
  form.addEventListener('submit', async e => { e.preventDefault(); if (!fileInput.files.length) return; state.importFile = fileInput.files[0]; state.importRegion = region.input.value; submit.disabled = true; err.textContent = ''; try { state.importPreview = await previewImport(); showMapping(); } catch (error) { err.textContent = error.message; } finally { submit.disabled = false; } });
  container.replaceChildren(progress(0), card('Upload your source file', 'Phone is required. Contact, property, and evidence columns may be mapped.', el('div', { class: 'card-body' }, form)));
 }
 async function previewImport(mapping) { const form = new FormData(); form.append('file', state.importFile); if (state.importRegion) form.append('region', state.importRegion); if (mapping) form.append('mapping', JSON.stringify(mapping)); return api('/api/imports/preview', 'POST', form); }
 function showMapping() {
  const p = state.importPreview; const grid = el('div', { class: 'mapping-grid' }); const controls = {};
  importFields.forEach(name => { const f = field(titleCase(name), name, { type: 'select', options: [{ value: '', label: 'Not mapped' }, ...(p.headers || []).map(h => ({ value: h, label: h }))], value: (p.mapping || {})[name] || '' }); controls[name] = f.input; grid.append(f.node); });
  const stats = el('div', { class: 'import-stats' }, [[p.accepted, 'Accepted rows'], [p.rejected, 'Rejected rows'], [p.duplicates, 'Duplicate rows']].map(([value, label]) => el('div', {}, el('strong', {}, fmt(value)), el('span', {}, label))));
  const review = button('Revalidate mapping', async () => { const mapping = Object.fromEntries(Object.entries(controls).filter(([, input]) => input.value).map(([key, input]) => [key, input.value])); state.importPreview = await previewImport(mapping); showMapping(); }, '', 'check');
  const previewRows = (p.rows || []).slice(0, 12).map(r => [r.row_number, r.phone, [r.first_name, r.last_name].filter(Boolean).join(' ') || 'No name', r.property?.street_address || 'No property', el('div', {}, badge((r.eligibility || []).length ? 'blocked' : 'pending'), reasons([...(r.eligibility || []), ...(r.warnings || [])]))]);
  container.replaceChildren(progress(1), card('Column mapping', state.importFile.name + ' · suggestions are editable', el('div', { class: 'card-body' }, grid, el('div', { class: 'form-buttons' }, review))), el('div', { class: 'spaced' }, card('Validated preview', 'First 12 normalized rows. No records have been saved.', el('div', {}, el('div', { class: 'card-body' }, stats, reasons(p.warnings || [])), table(['Row', 'Phone', 'Name', 'Property', 'Eligibility / warnings'], previewRows)), p.id && p.rejected ? [el('a', { href: '/api/imports/' + p.id + '/rejections', class: 'button small' }, 'Download rejected rows')] : [])), el('div', { class: 'form-buttons spaced' }, button('Choose a different file', showUpload), button('Review import →', showConfirm, 'primary')));
 }
 function showConfirm() {
  const p = state.importPreview;
  container.replaceChildren(progress(2), card('Confirm this import', 'A final review before contact and property records are saved.', el('div', { class: 'card-body' }, el('div', { class: 'detail-grid' }, detail('File', state.importFile.name), detail('Accepted rows', fmt(p.accepted)), detail('Rejected rows', fmt(p.rejected)), detail('Duplicate rows', fmt(p.duplicates))), notice('Importing does not authorize outreach.', 'Consent evidence remains pending, timezone uncertainty stays held, and existing suppression records remain active.'), button('Commit ' + fmt(p.accepted) + ' rows', async () => { if (!p.id) throw new Error('Import preview is missing its job identifier. Please preview again.'); const result = await api('/api/imports/' + p.id + '/commit', 'POST', {}); toast('Import saved. Review each recipient’s eligibility.'); container.replaceChildren(card('Import complete', 'Your records are ready for evidence review.', el('div', { class: 'card-body' }, safeJSON(result), el('div', { class: 'form-buttons spaced' }, button('Import another file', showUpload), button('Review contacts', () => { location.hash = '#contacts'; }, 'primary'))))); }, 'primary', 'check')), [button('Back to mapping', showMapping, 'small')]));
 }
 showUpload();
}

async function templatesView() {
 const [templates, contacts] = await Promise.all([api('/api/templates'), api('/api/contact-options')]); state.templates = templates; state.contacts = contacts; if (state.view !== 'templates') return;
 const main = $('#main'); main.replaceChildren(heading('Messages & variants', 'Write truthful, relevant SMS. Save a version, preview real fields, and submit it for review.'));
 const name = field('Template name', 'name', { required: true, placeholder: 'Property inquiry · consented sellers' });
 const body = field('Message', 'body', { type: 'textarea', rows: 8, required: true, value: 'Hi {{first_name}}, this is {{business_name}}. Are you interested in discussing your property at {{street_address}}? Reply STOP to unsubscribe.' });
 const fields = ['first_name', 'street_address', 'city', 'state', 'business_name'];
 const mergeButtons = el('div', { class: 'merge-fields' }, fields.map(f => { const b = el('button', { type: 'button', class: 'merge-button' }, '{{' + f + '}}'); b.addEventListener('click', () => { const input = body.input, start = input.selectionStart, end = input.selectionEnd; input.setRangeText('{{' + f + '}}', start, end, 'end'); input.focus(); }); return b; }));
 const err = el('p', { class: 'form-error' }); const save = el('button', { class: 'button primary', type: 'submit', disabled: !canWrite() }, 'Save new version'); const form = el('form', {}, name.node, body.node, mergeButtons, el('p', { class: 'inline-note' }, 'A missing first name becomes “there”; missing property fields block rendering. Each saved version is immutable. Approved variants must be selected deliberately; sending never creates new wording.'), err, el('div', { class: 'form-buttons' }, save));
 form.addEventListener('submit', async e => { e.preventDefault(); save.disabled = true; err.textContent = ''; try { const t = await api('/api/templates', 'POST', { name: name.input.value, body: body.input.value }); toast('Draft template version saved.'); await templatesView(); } catch (error) { err.textContent = error.message; } finally { save.disabled = false; } });
 const previewContent = templatePreviewForm(templates, contacts);
 main.append(el('div', { class: 'composer-grid' }, card('Message composer', 'Clear identity. Relevant context. One simple question.', el('div', { class: 'card-body' }, form)), card('Exact recipient preview', 'Rendered by the same server policy used for dispatch', el('div', { class: 'card-body' }, previewContent))));
 main.append(el('div', { class: 'inline-heading spaced' }, el('h2', {}, 'Version library'), el('span', { class: 'inline-note' }, fmt(templates.length) + ' versions')));
 main.append(templates.length ? el('div', { class: 'template-list' }, templates.map(t => el('div', { class: 'card card-padding' }, el('div', { class: 'card-row' }, el('div', {}, el('h3', {}, t.name), el('p', { class: 'inline-note' }, 'Version ' + t.version + ' · ' + date(t.created_at))), badge(t.status)), el('p', { class: 'template-body' }, t.body), el('div', { class: 'table-actions' }, t.status !== 'approved' && canApprove() ? button('Review & approve', () => approveTemplate(t), 'small primary') : null, button('Use as new draft', () => { name.input.value = t.name; body.input.value = t.body; body.input.focus(); window.scrollTo({ top: 0, behavior: 'smooth' }); }, 'small'))))) : empty('No saved versions.', 'Start with a concise message above.'));
}
function templatePreviewForm(templates, contacts) {
 const form = el('form');
 const template = field('Saved template version', 'template_id', { type: 'select', required: true, options: [{ value: '', label: 'Choose a saved version' }, ...templates.map(t => ({ value: t.id, label: t.name + ' · v' + t.version + ' · ' + t.status }))] });
 const contact = field('Recipient', 'contact_id', { type: 'select', required: true, options: [{ value: '', label: 'Choose a contact' }, ...contacts.map(c => ({ value: c.id, label: names(c) + ' · ' + c.phone }))] });
 const property = field('Property context', 'property_id', { type: 'select', options: [{ value: '', label: 'Use first recorded property' }] });
 const output = el('div'); const err = el('p', { class: 'form-error' }); const submit = el('button', { type: 'submit', class: 'button' }, 'Render exact preview');
 contact.input.addEventListener('change', async () => { property.input.replaceChildren(el('option', { value: '' }, 'Use first recorded property')); if (contact.input.value) { try { const d = await api('/api/contacts/' + contact.input.value); (d.properties || []).forEach(p => property.input.append(el('option', { value: p.id }, p.street_address || p.property_id || p.id))); } catch (e) { err.textContent = e.message; } } });
 form.append(template.node, contact.node, property.node, err, submit, output);
 form.addEventListener('submit', async e => { e.preventDefault(); submit.disabled = true; err.textContent = ''; try { const p = await api('/api/preview', 'POST', { template_id: Number(template.input.value), contact_id: Number(contact.input.value), property_id: property.input.value ? Number(property.input.value) : null }); const metrics = p.metrics || p; output.replaceChildren(el('div', { class: 'sms-preview' }, p.body || p.rendered_body || p.rendered || ''), el('div', { class: 'preview-metrics' }, el('span', {}, 'Encoding', el('strong', {}, metrics.encoding || '—')), el('span', {}, 'Segments', el('strong', {}, metrics.segments ?? '—')), el('span', {}, 'Est. cost', el('strong', {}, money(metrics.estimated_cost)))), (p.eligibility || []).length ? reasons(p.eligibility) : null); } catch (error) { err.textContent = error.message; output.replaceChildren(); } finally { submit.disabled = false; } }); return form;
}
function approveTemplate(t) { formModal('Review template version ' + t.version, 'Check business identity, truthful content, approved fields, and supported STOP language.', [{ label: 'Review note', name: 'review_note', type: 'textarea', required: true, wide: true }], 'Approve immutable version', async data => { await api('/api/templates/' + t.id + '/approve', 'POST', data); toast('Template version approved.'); await templatesView(); }, el('div', { class: 'sms-preview' }, t.body)); }

async function campaignsView() {
 const campaigns = await api('/api/campaigns'); state.campaigns = campaigns; if (state.view !== 'campaigns') return;
 const main = $('#main'); main.replaceChildren(heading('Campaigns', 'Build from approved versions, validate the audience, and schedule only after review.', canWrite() ? [button('New campaign', campaignBuilder, 'primary', 'plus')] : []));
 main.append(notice('One recipient, one campaign assignment.', 'Multiple properties do not cause duplicate outreach. Approved variants are assigned deliberately and share the same consent and suppression gates.'));
 main.append(card('Campaign workspace', 'Launch permissions and policy checks are enforced by the server', campaignTable(campaigns)));
}
function campaignTable(campaigns, controls = true) {
 return table(['Campaign', 'Status', 'Environment', 'Schedule', ...(controls ? ['Actions'] : [])], campaigns.map(c => [el('div', {}, el('span', { class: 'table-main' }, c.name), el('span', { class: 'table-sub' }, (c.contact_count != null ? fmt(c.contact_count) + ' recipients · ' : '') + 'Template ' + c.template_id)), badge(c.status), badge(c.mode || 'simulation', (c.mode || 'simulation') === 'simulation' ? 'good' : 'warn'), c.scheduled_at ? date(c.scheduled_at) : 'Not scheduled', ...(controls ? [el('div', { class: 'table-actions' }, button('Validate', () => validateCampaign(c), 'small'), ['draft', 'paused'].includes(c.status) && canApprove() ? button('Approve', () => approveCampaign(c), 'small primary') : null, c.status === 'approved' && canApprove() ? button('Schedule', () => scheduleCampaign(c), 'small primary') : null, ['approved', 'scheduled'].includes(c.status) && canWrite() ? button('Pause', () => pauseCampaign(c), 'small danger-outline') : null, canWrite() ? button('Clone', async () => { await api('/api/campaigns/' + c.id + '/clone', 'POST', {}); toast('Campaign cloned as a draft. Suppression remains active.'); await campaignsView(); }, 'small') : null, c.status === 'paused' && canApprove() ? button('Resume', () => resumeCampaignModal(c), 'small primary') : null)] : [])]), 'No campaigns created.');
}
async function campaignBuilder() {
 const [templates, contacts, settings] = await Promise.all([api('/api/templates'), api('/api/contact-options'), api('/api/settings')]);
 const m = modal('Build a campaign', 'Select a reviewed message, assign recipients, then validate before approval.', true);
 const form = el('form'); const name = field('Campaign name', 'name', { required: true, placeholder: 'Seller inquiries · October' });
 const template = field('Primary approved template', 'template_id', { type: 'select', required: true, options: [{ value: '', label: 'Choose an approved version' }, ...templates.filter(t => t.status === 'approved').map(t => ({ value: t.id, label: t.name + ' · v' + t.version }))] });
 const creds = (settings.credentials || []).find(c => c.environment === 'production') || {};
 const verified = creds.verification || {};
 const production = state.mode === 'production';
 const approvedRouting = Boolean(creds.verified_at && verified.account_ok && verified.service_ok && verified.campaign_ok && settings.readiness?.sender_associations_reviewed);
 const service = field('Messaging Service SID', 'service_sid', production ? { type: 'select', required: true, options: [{ value: '', label: 'Choose the reviewed service' }, ...(approvedRouting ? [{ value: creds.service_sid, label: (verified.service?.friendly_name || 'Reviewed service') + ' · ' + creds.service_sid }] : [])], hint: 'Only a verified service with reviewed sender associations is available.' } : { value: 'SIMULATION', hint: 'Synthetic routing. No provider account is used.' });
 const sender = field('Sender routing', 'sender', production ? { type: 'select', options: [{ value: '', label: 'Messaging Service routing' }, ...(approvedRouting ? (verified.authorized_senders || []).map(n => ({ value: n, label: n })) : [])], hint: 'Only observed service members with reviewed associations are available.' } : { value: '+12025550199', placeholder: 'Synthetic sender', hint: 'A synthetic sender preserves the conversation context in simulation.' });
 const variants = el('div', { class: 'list-selector' });
 templates.filter(t => t.status === 'approved').forEach(t => variants.append(el('label', { class: 'checkbox-label' }, el('input', { type: 'checkbox', name: 'variant_ids', value: t.id }), t.name + ' · v' + t.version)));
 const selected = new Set(); const propertyIds = new Map(); const audience = el('div', { class: 'list-selector' }); const count = el('span', { class: 'inline-note' }, '0 selected');
 const audienceSearch = el('input', { type: 'search', placeholder: 'Find recipients across all imported contacts…', 'aria-label': 'Filter campaign audience' });
 function matchingContacts() { const query = audienceSearch.value.trim().toLowerCase(); return contacts.filter(c => !query || (names(c) + ' ' + c.phone + ' ' + (c.properties || []).map(p => p.street_address).join(' ')).toLowerCase().includes(query)); }
 function drawAudience() {
  const matches = matchingContacts(); audience.replaceChildren(); count.textContent = fmt(selected.size) + ' selected · ' + fmt(matches.length) + ' matching';
  for (const c of matches.slice(0, 200)) {
   const input = el('input', { type: 'checkbox', checked: selected.has(c.id), value: c.id });
   const row = el('div', { class: 'audience-row' });
   row.append(el('label', { class: 'checkbox-label' }, input, el('span', {}, names(c), el('span', { class: 'table-sub' }, c.phone)), el('small', {}, 'Evidence checked at validation')));
   input.addEventListener('change', () => { if (input.checked) selected.add(c.id); else selected.delete(c.id); count.textContent = fmt(selected.size) + ' selected · ' + fmt(matches.length) + ' matching'; });
   if ((c.properties || []).length) {
    const props = el('select', { 'aria-label': 'Property context for ' + names(c) }, c.properties.map(p => el('option', { value: p.id }, p.street_address || p.property_id || 'Property ' + p.id)));
    props.value = propertyIds.get(c.id) || c.properties[0].id; propertyIds.set(c.id, Number(props.value));
    props.addEventListener('change', () => propertyIds.set(c.id, Number(props.value))); row.append(props);
   }
   audience.append(row);
  }
  if (matches.length > 200) audience.append(el('p', { class: 'inline-note audience-note' }, 'Showing the first 200 matches. Search to find others or select all matching recipients.'));
 }
 audienceSearch.addEventListener('input', drawAudience); drawAudience();
 const audienceTools = el('div', { class: 'table-actions' }, button('Select all matching', () => { matchingContacts().forEach(c => { selected.add(c.id); if (c.properties?.length && !propertyIds.has(c.id)) propertyIds.set(c.id, c.properties[0].id); }); drawAudience(); }, 'small'), button('Clear selection', () => { selected.clear(); drawAudience(); }, 'small'));
 const err = el('p', { class: 'form-error' }); const save = el('button', { type: 'submit', class: 'button primary' }, 'Create draft campaign');
 form.append(notice('Environment: ' + titleCase(state.mode) + '.', 'Environment selection is controlled by the server configuration.'), el('div', { class: 'form-grid' }, name.node, template.node, service.node, sender.node), el('div', {}, el('h3', {}, 'Optional reviewed A/B variants'), el('p', { class: 'inline-note' }, 'Only approved immutable versions can be assigned. Do not include the primary version again.'), variants.childElementCount ? variants : el('p', { class: 'inline-note' }, 'Approve a template version to make it available here.')), el('div', {}, el('div', { class: 'inline-heading' }, el('h3', {}, 'Recipient audience'), count), audienceSearch, el('div', { class: 'spaced' }, audienceTools), audience.childElementCount ? audience : empty('No contacts.', 'Import and review recipient evidence first.'), el('p', { class: 'inline-note spaced' }, 'Contacts with missing evidence remain blocked. A campaign does not establish consent.')), err, el('div', { class: 'form-buttons' }, button('Cancel', m.close), save));
 form.addEventListener('submit', async e => { e.preventDefault(); save.disabled = true; err.textContent = ''; try { const data = new FormData(form), contactIds = [...selected].map(Number), primaryId = Number(template.input.value); if (!contactIds.length) throw new Error('Select at least one recipient.'); await api('/api/campaigns', 'POST', { name: name.input.value, template_id: primaryId, variant_ids: data.getAll('variant_ids').map(Number).filter(id => id !== primaryId), contact_ids: contactIds, property_ids: Object.fromEntries([...propertyIds].filter(([id]) => selected.has(id)).map(([id, pid]) => [String(id), Number(pid)])), mode: state.mode, service_sid: service.input.value || null, sender: sender.input.value || null }); m.close(); toast('Draft campaign created. Validate before approval.'); await campaignsView(); } catch (error) { err.textContent = error.message; } finally { save.disabled = false; } }); m.body.append(form);
}
async function validateCampaign(c) {
 const result = await api('/api/campaigns/' + c.id + '/validate', 'POST', {}); const m = modal('Campaign validation', c.name + ' · consent, rendering, and operations checks.', true);
 m.body.append(notice('Validation is a current snapshot.', 'Every recipient is checked again at dispatch. New opt-outs and health incidents can block a previously reviewed campaign.'), safeJSON(result), el('div', { class: 'form-buttons' }, button('Close', m.close), c.status === 'draft' && canApprove() ? button('Review approval', () => { m.close(); approveCampaign(c); }, 'primary') : null));
}
function approveCampaign(c) { formModal('Approve campaign', c.name + ' · verify the audience, purpose, sender, and approved message versions.', [{ label: 'Approval review note', name: 'review_note', type: 'textarea', required: true, wide: true }], 'Approve campaign', async data => { await api('/api/campaigns/' + c.id + '/approve', 'POST', data); toast('Campaign approved. Schedule after readiness is satisfied.'); await campaignsView(); }, notice('Approval does not bypass recipient safeguards.', 'Missing consent, suppression, timezone uncertainty, and operational holds remain effective.')); }
function scheduleCampaign(c) { formModal('Schedule campaign', c.name + ' · recipient-local sending windows and queue expiry remain enforced.', [{ label: 'Dispatch start in your browser timezone', name: 'scheduled_at', type: 'datetime-local', required: true, wide: true, hint: 'The selected instant is converted to UTC. Individual recipients still require verified local timezone evidence.' }], 'Schedule campaign', async data => { await api('/api/campaigns/' + c.id + '/schedule', 'POST', { scheduled_at: new Date(data.scheduled_at).toISOString() }); toast('Campaign scheduled. Blocked recipients stay held.'); await campaignsView(); }); }
function pauseCampaign(c) { formModal('Pause this campaign', 'Stop further application dispatch for ' + c.name + '.', [{ label: 'Pause reason', name: 'reason', type: 'textarea', required: true, wide: true }], 'Pause campaign', async data => { await api('/api/campaigns/' + c.id + '/pause', 'POST', data); toast('Campaign paused.'); await campaignsView(); }, notice('Provider-accepted messages may still arrive.', 'Pausing stops new dispatch. Already accepted messages may be impossible to recall.', 'warning')); }

async function inboxView() {
 const inbound = await api('/api/inbox'); if (state.view !== 'inbox') return;
 const main = $('#main'); main.replaceChildren(heading('Conversation inbox', 'Replies hold automatic outreach. Review interest, opt-outs, ownership, and the next response.'));
 main.append(notice('Every outbound reply uses the policy gate.', 'A manual reply cannot bypass suppression, verified consent, recipient-local schedules, or operational safeguards.'));
 if (!inbound.length) { main.append(card('Conversation inbox', 'Inbound replies appear after signature-validated processing', empty('No conversations yet.', 'Use the simulation tools in diagnostics to practice inbound handling with synthetic recipients.'))); return; }
 const list = el('div', { class: 'inbox-list' }); const conversation = el('div', { class: 'conversation' }); const layout = el('div', { class: 'card inbox-layout' }, list, conversation); main.append(layout);
 const grouped = new Map(); inbound.forEach(i => { const key = i.contact_id || i.from_phone; if (!grouped.has(key)) grouped.set(key, i); });
 for (const i of grouped.values()) { const b = el('button', { type: 'button', class: 'inbox-thread' }, el('div', { class: 'card-row' }, el('strong', {}, i.contact_name || i.from_phone), badge(i.classification)), el('p', {}, i.body), el('time', {}, date(i.created_at))); b.addEventListener('click', () => select(i, b)); list.append(b); }
 async function select(i, b) {
  list.querySelectorAll('.inbox-thread').forEach(n => n.classList.toggle('selected', n === b)); conversation.replaceChildren(el('div', { class: 'loading' }, 'Loading conversation…'));
  try {
   const messages = await api('/api/messages'); const history = [...inbound.filter(n => n.contact_id === i.contact_id && n.from_phone === i.from_phone).map(n => ({ ...n, inbound: true })), ...messages.filter(n => n.contact_id && n.contact_id === i.contact_id).map(n => ({ ...n, inbound: false }))].sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
   const bubbles = el('div'); history.forEach(n => bubbles.append(el('div', { class: 'message-bubble' + (n.inbound ? '' : ' outbound') }, n.body), el('p', { class: 'message-meta' + (n.inbound ? '' : ' outbound-meta') }, date(n.created_at) + ' · ' + titleCase(n.classification || n.state))));
   const owner = field('Conversation owner', 'owner', { value: i.owner || '', placeholder: 'Owner name or email' }); const lead = field('Lead status', 'lead_status', { type: 'select', options: ['new', 'review', 'interested', 'qualified', 'not_interested', 'complaint', 'wrong_number', 'suppressed', 'closed'], value: i.lead_status || 'new' });
   const reply = field('Manual reply', 'body', { type: 'textarea', rows: 3, placeholder: 'Identify ' + state.businessName + ', write your response, and include Reply STOP to unsubscribe.', required: true }); const err = el('p', { class: 'form-error' }); const submit = el('button', { type: 'submit', class: 'button primary', disabled: !canApprove() }, state.mode === 'simulation' ? 'Queue simulated reply' : 'Queue reviewed reply');
   const replyForm = el('form', {}, reply.node, err, el('div', { class: 'form-buttons' }, submit)); replyForm.addEventListener('submit', async e => { e.preventDefault(); submit.disabled = true; err.textContent = ''; try { await api('/api/inbox/' + i.id + '/reply', 'POST', { body: reply.input.value }); toast('Manual reply queued through the policy gate.'); reply.input.value = ''; await select(i, b); } catch (error) { err.textContent = error.message; } finally { submit.disabled = false; } });
   conversation.replaceChildren(el('div', { class: 'conversation-head' }, el('div', {}, el('h3', {}, i.contact_name || i.from_phone), el('p', { class: 'inline-note' }, 'Automated outreach held after reply')), i.contact_id ? button('Recipient evidence', () => contactDetail(i.contact_id), 'small') : badge('review')), bubbles, el('div', { class: 'conversation-controls' }, el('div', { class: 'split-fields' }, owner.node, lead.node), el('div', { class: 'form-buttons' }, button('Save assignment', async () => { await api('/api/inbox/' + i.id, 'PATCH', { owner: owner.input.value, lead_status: lead.input.value }); i.owner = owner.input.value; i.lead_status = lead.input.value; toast('Conversation assignment saved.'); }, 'small')), ['optout', 'opt_out', 'complaint', 'wrong_number', 'ambiguous', 'review', 'renewal_review'].includes(i.classification) ? notice('Review this conversation before responding.', 'Suppression and review holds remain authoritative. A positive reply is not renewed consent.', 'warning') : null, replyForm));
  } catch (e) { conversation.replaceChildren(notice('Unable to load conversation.', e.message, 'danger')); }
 }
 await select(grouped.values().next().value, list.firstElementChild);
}

const policyFields = [
 ['window_start', 'Local window start hour', 0, 23, 1], ['window_end', 'Local window end hour', 1, 24, 1],
 ['max_recipient_24h', 'Messages per recipient / 24h', 1, 10, 1], ['max_recipient_7d', 'Messages per recipient / 7d', 1, 30, 1],
 ['max_account_daily', 'Account messages / day', 1, 100000, 1], ['max_campaign_daily', 'Campaign messages / day', 1, 100000, 1],
 ['max_per_minute', 'Application dispatches / minute', 1, 60000, 1], ['max_segments', 'Segments / message', 1, 10, 1],
 ['max_daily_cost', 'Account daily budget ($)', .01, 100000, .01], ['max_campaign_cost', 'Campaign budget ($)', .01, 100000, .01],
 ['max_queue_age_seconds', 'Queue lifetime (seconds)', 60, 86400, 1], ['max_provider_validity_seconds', 'Provider validity (seconds)', 6, 300, 1],
 ['health_min_sample', 'Rate alert minimum sample', 1, 10000, 1], ['max_filter_rate', 'Filtering pause fraction', .001, 1, .001], ['max_optout_rate', 'Opt-out pause fraction', .001, 1, .001]
];
const readinessFields = [
 ['advanced_optout_reviewed', 'Advanced Opt-Out and webhook behavior reviewed in Twilio Console'],
 ['sender_associations_reviewed', 'Messaging Service, registered campaign, and authorized sender associations reviewed'],
 ['campaign_content_reviewed', 'Consent collection, purpose, disclosures, and message content reviewed against registration'],
 ['smoke_test_reviewed', 'Controlled smoke test with explicitly authorized recipients completed and reviewed']
];
async function settingsView() {
 const s = await api('/api/settings'); state.settings = s; if (state.view !== 'settings') return;
 const main = $('#main'); main.replaceChildren(heading('Twilio & safeguards', 'Connect your existing Twilio account and review the safeguards that govern every message.'));
 main.append(s.global_pause ? notice('Global pause is active.', s.pause_reason || 'Review the cause before resuming.', 'danger') : notice('Configuration is preserved.', 'Connecting or verifying is read-only. Blastio does not change your registration, sender pool, or Twilio settings.'));
 const connection = el('div', { class: 'card-body' });
 const credentials = s.credentials || [];
 if (!credentials.length) connection.append(empty('Connect your Twilio account.', 'Use a scoped API key where supported. Credentials are encrypted on the server.'), isAdmin() ? button('Connect credentials', connectModal, 'primary', 'plus') : notice('Administrator access required.', 'Only administrators can change credentials.'));
 for (const c of credentials) {
  const verification = typeof c.verification === 'string' ? (() => { try { return JSON.parse(c.verification); } catch { return {}; } })() : c.verification || {};
  connection.append(el('div', { class: 'small-card' }, el('div', { class: 'card-row' }, el('h3', {}, titleCase(c.environment) + ' connection'), badge(verification.production_eligible ? 'verified' : c.verified_at ? 'review' : 'pending')), el('div', { class: 'detail-grid spaced' }, detail('Account SID', c.account_sid_masked || masked(c.account_sid)), detail('API Key SID', c.api_key_sid_masked || masked(c.api_key_sid)), detail('Messaging Service', c.service_sid), detail('Last verified', date(c.verified_at))), el('p', { class: 'inline-note' }, 'API secret and webhook Auth Token: stored encrypted; never returned to this browser.'), el('div', { class: 'settings-actions' }, isAdmin() ? button('Verify read-only connection', async () => { const result = await api('/api/twilio/' + c.environment + '/verify', 'POST', {}); const m = modal('Connection verification', 'Provider-observed facts and manual review requirements.', true); m.body.append(safeJSON(result)); await settingsView(); }, 'small primary') : null, isAdmin() ? button('Probe webhook paths', async () => { const result = await api('/api/twilio/production/probe', 'POST', {}); const d = modal('Signed webhook probe', 'Health checks exercise inbound and callback paths without outbound messaging.', true); d.body.append(safeJSON(result)); await settingsView(); }, 'small') : null, isAdmin() ? button('Replace', () => connectModal(c), 'small') : null, isAdmin() ? button('Revoke', () => revokeModal(c), 'small danger-outline') : null), verificationSummary(verification), Object.keys(verification).length ? el('details', { class: 'spaced' }, el('summary', { class: 'inline-note' }, 'Verified provider facts & sender associations'), safeJSON(verification)) : null));
 }
 connection.append(el('p', { class: 'inline-note spaced' }, 'The Twilio Auth Token is used to validate webhook signatures. API verification does not prove carrier delivery or eliminate filtering. Required Console configuration and registration review are separate.'));
 const operations = el('div', { class: 'card-body' }, el('div', { class: 'detail-grid' }, detail('Server environment', titleCase(s.mode)), detail('Production sending enabled', s.production_enabled ? 'Enabled by server configuration' : 'Disabled'), detail('Inbound health observed', date(s.health?.inbound_health_at)), detail('Callback health observed', date(s.health?.callback_health_at)), detail('Suppression store check', date(s.health?.suppression_health_at))), notice('Pause stops new dispatch.', 'Messages already accepted by the provider may be impossible to recall.', 'warning'), s.global_pause && isAdmin() ? button('Review & resume operations', resumeModal, 'primary') : button('Pause all sending', pauseGlobalModal, 'danger-outline', 'pause'));
 main.append(el('div', { class: 'settings-grid' }, card('Twilio connection', 'Secrets stay on the server. Verification never sends a message.', connection), card('Operational controls', 'Global pause and observed callback health', operations)));
 const policy = s.policy || {}; const controls = {};
 const policyForm = el('form'); const grid = el('div', { class: 'form-grid' });
 policyFields.forEach(([key, label, min, max, step]) => { const f = field(label, key, { type: 'number', min, max, step, value: policy[key], required: true }); if (!isAdmin()) f.input.disabled = true; controls[key] = f.input; grid.append(f.node); });
 const note = field('Safeguard review note', 'review_note', { type: 'textarea', rows: 3, required: true, hint: 'Explain changes and why these thresholds are appropriate for your campaign.' }); const err = el('p', { class: 'form-error' }); const save = el('button', { type: 'submit', class: 'button primary', disabled: !isAdmin() }, 'Save reviewed safeguards');
 const checks = el('div', { class: 'checklist' }); const checkInputs = {};
 readinessFields.forEach(([key, label]) => { const input = el('input', { type: 'checkbox', name: key, checked: Boolean((s.readiness || {})[key]), disabled: !isAdmin() }); checkInputs[key] = input; checks.append(el('label', { class: 'checkbox-label' }, input, label)); });
 policyForm.append(notice('These are conservative product safeguards.', 'Rate and incident thresholds are reviewable operational settings. They are not universal legal limits or a guarantee against provider enforcement.'), grid, el('div', { class: 'inline-heading' }, el('h3', {}, 'Production readiness review')), checks, note.node, err, el('div', { class: 'form-buttons' }, save));
 policyForm.addEventListener('submit', async e => { e.preventDefault(); save.disabled = true; err.textContent = ''; try { const policyData = Object.fromEntries(Object.entries(controls).map(([key, input]) => [key, Number(input.value)])), readiness = Object.fromEntries(Object.entries(checkInputs).map(([key, input]) => [key, input.checked])); await api('/api/settings', 'PUT', { policy: policyData, readiness, review_note: note.input.value }); toast('Reviewed safeguards saved.'); await settingsView(); } catch (error) { err.textContent = error.message; } finally { save.disabled = false; } });
 main.append(el('div', { class: 'spaced' }, card('Sending safeguards & readiness', 'Recipient schedules, shared limits, cost ceilings, and incident stops', el('div', { class: 'card-body' }, policyForm))));
}
function connectModal(existing = {}) {
 const fields = [
  { label: 'Credential environment', name: 'environment', type: 'select', options: ['production'], value: existing.environment || 'production' },
  { label: 'Account SID', name: 'account_sid', required: true, placeholder: 'AC…', value: existing.account_sid || '' },
  { label: 'API Key SID', name: 'api_key_sid', required: false, placeholder: 'SK…', value: existing.api_key_sid || '' },
  { label: 'API Key secret', name: 'api_secret', type: 'password', required: false, autocomplete: 'new-password', hint: existing.environment ? 'Leave blank to retain the saved secret when keeping the same key.' : 'Required when using an API key. Prefer a key scoped for this integration.' },
  { label: 'Account Auth Token for webhook validation', name: 'auth_token', type: 'password', required: !existing.environment, autocomplete: 'new-password', wide: true, hint: existing.environment ? 'Leave blank to retain the saved webhook token.' : 'Required for signed webhook validation. Also supports Account SID REST authentication when no API key is supplied.' },
  { label: 'Existing registered Messaging Service SID', name: 'service_sid', required: true, wide: true, placeholder: 'MG…', value: existing.service_sid || '', hint: 'Blastio does not create or modify the service.' }
 ];
 const services = existing.verification?.services || [];
 if (services.length) { fields[5].type = 'select'; fields[5].options = services.map(v => ({ value: v.sid, label: (v.friendly_name || 'Messaging Service') + ' · ' + v.sid })); }
 formModal(existing.environment ? 'Replace Twilio credentials' : 'Connect Twilio credentials', 'Encrypted server storage. Read-only validation is a separate next step.', fields, 'Store encrypted credentials', async data => { await api('/api/twilio', 'POST', data); toast('Credentials stored. Verify the connection before production review.'); await settingsView(); }, notice('Replacing resets verification.', 'Use appropriately scoped credentials and retain the existing registration and Messaging Service. No message is sent by connection.'));
}
function revokeModal(c) { formModal('Revoke stored credentials', 'Remove Blastio’s stored connection for ' + c.environment + '.', [{ label: 'Review note', name: 'review_note', type: 'textarea', required: true, wide: true }], 'Revoke connection', async data => { await api('/api/twilio/' + c.environment + '/revoke', 'POST', data); toast('Stored connection revoked. Review the key separately in Twilio Console.'); await settingsView(); }, notice('Provider key management is separate.', 'This revokes the application’s stored credentials. Rotate or delete the provider key in Twilio Console as appropriate.', 'warning')); }
function pauseGlobalModal() { formModal('Pause all sending', 'Stop further dispatch across all campaigns and business senders.', [{ label: 'Pause reason', name: 'reason', type: 'textarea', required: true, wide: true }], 'Pause operations', async data => { await api('/api/operations/pause', 'POST', data); toast('Global pause is active.'); await refresh(); }, notice('Provider-accepted messages may still arrive.', 'The pause prevents new application dispatch and does not promise recall of accepted messages.', 'warning')); }
function resumeModal() { formModal('Review & resume sending', 'Document the incident cause and recovery before allowing dispatch.', [{ label: 'Cause, resolution, and verification performed', name: 'review_note', type: 'textarea', required: true, wide: true }], 'Resume after review', async data => { await api('/api/operations/resume', 'POST', data); toast('Operations resumed. All policy gates remain active.'); await refresh(); }, notice('Health requirements remain enforced.', 'Resolving a global pause does not remove campaign holds or recipient suppression.')); }

async function diagnosticsView() {
 const [messages, audit, settings, performance] = await Promise.all([api('/api/messages'), api('/api/audit'), api('/api/settings'), api('/api/performance')]); if (state.view !== 'diagnostics') return;
 const main = $('#main'); main.replaceChildren(heading('Diagnostics & audit', 'Inspect outcomes, diagnose holds, and export evidence for operational review.', [el('a', { class: 'button', href: '/api/evidence/export' }, icon('download'), 'Export evidence')]));
 const tabs = el('div', { class: 'tabs' }); const content = el('div');
 const views = [ ['delivery', 'Delivery diagnostics', () => card('Outbound message ledger', 'Rendered bodies, state changes, sender details, and unresolved attempts', table(['Message', 'Recipient', 'Outcome', 'Sender / service', 'Cost', ''], messages.map(m => [el('div', {}, el('span', { class: 'table-main' }, '#' + m.id + ' · Campaign ' + m.campaign_id), el('span', { class: 'table-sub' }, date(m.created_at))), m.phone || 'Contact ' + m.contact_id, el('div', {}, badge(m.state), el('span', { class: 'table-sub' }, m.reason || '')), el('div', {}, el('span', {}, m.sender || 'Service routing'), el('span', { class: 'table-sub' }, m.service_sid || 'Simulation')), el('div', {}, money(m.estimated_cost), el('span', { class: 'table-sub' }, m.actual_cost == null ? 'Actual unavailable' : 'Actual ' + money(m.actual_cost))), button('Inspect', () => inspectMessage(m), 'small')])) ) ], ['audit', 'Audit history', () => card('Decision history', 'Server-recorded changes and actor attribution', table(['Time', 'Actor', 'Action', 'Entity', 'Detail'], audit.map(a => [date(a.created_at), a.actor, titleCase(a.action), [a.entity_type, a.entity_id].filter(Boolean).join(' · '), el('div', { class: 'audit-meta' }, typeof a.detail === 'string' ? a.detail : JSON.stringify(a.detail || {}))]))) ], ['performance', 'Performance', () => performancePanel(performance)], ['simulation', 'Simulation lab', () => simulationPanel(settings)] ];
 views.forEach(([id, name, render], i) => { const tab = el('button', { class: 'tab' + (i === 0 ? ' active' : ''), type: 'button' }, name); tab.addEventListener('click', () => { tabs.querySelectorAll('button').forEach(t => t.classList.toggle('active', t === tab)); content.replaceChildren(render()); }); tabs.append(tab); });
 main.append(notice('Unknown is a held state.', 'Ambiguous provider acceptance is never blindly retried. Reconcile using provider records and review the queue before recovery.', 'warning'), tabs, content); content.append(views[0][2]());
}
function simulationPanel(settings) {
 if (settings.mode !== 'simulation') return card('Simulation is disabled', 'This server is configured for production.', empty('Production environment.', 'Use a separate simulation instance and synthetic recipients for development.'));
 const tick = button('Run one simulated dispatch', async () => { const result = await api('/api/simulation/tick', 'POST', {}); toast((result.processed || result.dispatched) ? 'One eligible message processed.' : 'No message was eligible for this dispatch.'); const m = modal('Simulation dispatch', 'Synthetic provider response; no carrier message was sent.'); m.body.append(safeJSON(result)); }, 'primary', 'campaign');
 const phone = field('Synthetic recipient phone', 'phone', { required: true, placeholder: '+12025550101' }); const body = field('Incoming message', 'body', { type: 'textarea', required: true, value: 'STOP', rows: 3 }); const err = el('p', { class: 'form-error' }); const submit = el('button', { type: 'submit', class: 'button primary' }, 'Process simulated inbound'); const form = el('form', {}, phone.node, body.node, err, el('div', { class: 'form-buttons' }, submit));
 form.addEventListener('submit', async e => { e.preventDefault(); submit.disabled = true; err.textContent = ''; try { const result = await api('/api/simulation/inbound', 'POST', { phone: phone.input.value, body: body.input.value }); const m = modal('Inbound simulation result', 'Classification, suppression, and automatic outreach hold.'); m.body.append(safeJSON(result)); toast('Synthetic inbound message processed.'); } catch (error) { err.textContent = error.message; } finally { submit.disabled = false; } });
 return el('div', { class: 'section-grid' }, card('Dispatch simulation', 'Exercise queue claims, gates, and synthetic provider acceptance', el('div', { class: 'card-body' }, notice('No real messages are sent.', 'Simulation exercises application behavior. It does not establish real delivery or provider enforcement behavior.'), tick, el('p', { class: 'inline-note spaced' }, 'Create and schedule a campaign with reviewed synthetic contacts. The server still enforces evidence, local windows, limits, and pause controls.'))), card('Inbound simulation', 'Test opt-outs, natural-language requests, and reply holds', el('div', { class: 'card-body' }, form)));
}

$('#login-form').addEventListener('submit', async event => {
 event.preventDefault(); const form = event.currentTarget, submit = form.querySelector('button[type="submit"]'); $('#login-error').textContent = ''; submit.disabled = true;
 try { const data = Object.fromEntries(new FormData(form)); const me = await api('/api/login', 'POST', data); form.elements.password.value = ''; showApp(me); } catch (error) { $('#login-error').textContent = error.message; } finally { submit.disabled = false; }
});
$('#mobile-menu').addEventListener('click', () => $('#sidebar').classList.toggle('open'));
$('#global-pause').addEventListener('click', pauseGlobalModal);
$('#user-button').addEventListener('click', () => {
 const m = modal('Signed in', state.me?.email || 'Operator');
 m.body.append(el('div', { class: 'detail-grid' }, detail('Role', titleCase(state.me?.role)), detail('Environment', titleCase(state.mode))), button('Sign out', async () => { await api('/api/logout', 'POST', {}); m.close(); showLogin(); }, 'primary'));
});
window.addEventListener('hashchange', navigate);
(async () => { try { showApp(await api('/api/me')); } catch { showLogin(); } })();

function masked(value) { return value ? String(value).slice(0, 2) + '••••••••' + String(value).slice(-6) : 'Not configured'; }
function verificationSummary(v) {
 if (!v || !Object.keys(v).length) return null;
 const checks = el('div', { class: 'summary-items spaced' }, [['account_ok', 'Account access'], ['service_ok', 'Service association'], ['campaign_ok', 'A2P campaign'], ['webhooks_ok', 'Configured callbacks']].map(([key, title]) => el('div', { class: 'info-tile' }, el('span', {}, title), badge(v[key] ? 'verified' : 'review'))));
 const senders = v.senders || []; const registered = v.campaigns || [];
 return el('div', {}, checks, el('div', { class: 'detail-grid' }, detail('Observed campaign ID', v.campaign_id), detail('Checked at', date(v.checked_at))), registered.length ? table(['Campaign', 'Registration status', 'Purpose'], registered.map(c => [c.campaign_id || c.sid, badge(String(c.campaign_status || 'review').toLowerCase()), c.us_app_to_person_usecase || 'Manual review'])) : null, senders.length ? table(['Sender', 'SMS capable', 'Association'], senders.map(n => [n.phone_number || n.phone || n.sid, n.capabilities?.sms || n.sms_enabled ? 'Yes' : 'Needs review', (v.authorized_senders || []).includes(n.phone_number || n.phone) ? 'Observed service member' : 'Manual review'])) : null, reasons(v.manual_review || []));
}
function resumeContactModal(id) { formModal('Review automatic outreach hold', 'Review the person’s reply and documented permission before allowing future automation.', [{ label: 'Review and next-step evidence', name: 'review_note', type: 'textarea', required: true, wide: true }], 'Resume future automation', async data => { await api('/api/contacts/' + id + '/resume-automation', 'POST', data); toast('Future automation reviewed. Canceled messages remain canceled.'); }, notice('Suppression cannot be cleared here.', 'Verified consent, suppression absence, and documented review are required. This does not reinstate canceled messages.')); }
function resumeCampaignModal(c) { formModal('Review & resume campaign', c.name + ' · resolve the pause cause before continuing queued work.', [{ label: 'Cause, resolution, and review', name: 'review_note', type: 'textarea', required: true, wide: true }], 'Resume reviewed campaign', async data => { await api('/api/campaigns/' + c.id + '/resume', 'POST', data); toast('Campaign resumed after review. Dispatch safeguards remain active.'); await campaignsView(); }, notice('All dispatch safeguards remain active.', 'No sender substitution or message change is used to bypass a hold.')); }
function inspectMessage(m) {
 const d = modal('Message #' + m.id, 'Persisted dispatch record and attempts. Unknown acceptance stays held.', true);
 d.body.append(safeJSON(m));
 if (m.state === 'unknown' && canApprove()) d.body.append(el('div', { class: 'spaced' }, button('Reconcile with provider record', () => reconcileMessageModal(m), 'primary')));
}
function reconcileMessageModal(message) { formModal('Reconcile unknown acceptance', 'Use a provider SID found in Twilio records. This performs a read-only lookup and never retries sending.', [{ label: 'Provider Message SID (optional if already attached)', name: 'provider_sid', placeholder: 'SM…', value: message.provider_sid || '', wide: true }, { label: 'Record lookup and review note', name: 'review_note', type: 'textarea', required: true, wide: true }], 'Reconcile read-only', async data => { if (!data.provider_sid) delete data.provider_sid; const result = await api('/api/messages/' + message.id + '/reconcile', 'POST', data); toast('Provider reconciliation completed. Review the resulting state.'); await diagnosticsView(); }, notice('Do not resend an ambiguous attempt.', 'If provider acceptance cannot be established, keep the message held for review.', 'warning')); }
function performancePanel(data) {
 const groups = [['by_campaign', 'By campaign'], ['by_template', 'By template version'], ['by_sender', 'By sender']];
 return el('div', {}, notice('Reply attribution is specific.', data.reply_attribution || 'Replies are attributed to the latest accepted outbound to this recipient on the inbound sender before the reply. Unmatched replies are omitted.'), groups.map(([key, label]) => el('div', { class: 'spaced' }, card(label, 'Interest, opt-outs, adverse replies, delivery, and cost tracked separately', table(['Group', 'Total', 'Delivered', 'Interested / qualified', 'Opt-outs / adverse', 'Failures / filtering', 'Cost'], (data[key] || []).map(r => [el('div', {}, el('span', { class: 'table-main' }, r.name || r.sender || r.id || 'Service routing'), r.version ? el('span', { class: 'table-sub' }, 'Version ' + r.version) : null), fmt(r.total), fmt(r.delivered), fmt(r.interested) + ' / ' + fmt(r.qualified), fmt(r.opt_out || r.optout) + ' / ' + fmt((r.negative || 0) + (r.complaint || 0) + (r.wrong_number || 0)), fmt(r.failed) + ' / ' + fmt(r.filtered), el('div', {}, money(r.estimated_cost), el('span', { class: 'table-sub' }, r.actual_cost == null ? 'Actual unavailable' : 'Actual ' + money(r.actual_cost)))]))))));
}
