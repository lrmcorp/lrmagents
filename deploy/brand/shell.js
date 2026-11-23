/* LRMagent console in the OpenBot layout (styles in brand.css).
 *
 * Rearranges the stock console instead of replacing it, so every feature keeps
 * working: the brand row gains a new-chat button, the conversation list moves
 * from its pop-out panel into the sidebar for good (with a search box), the menu
 * sits at the bottom with the account row, and a new chat opens on a greeting
 * with the composer in the middle and the agents to start with below it.
 * Loaded last (deferred) by chat.html, after the console's own scripts. */
(function () {
    'use strict';
    const $ = (sel, el = document) => el.querySelector(sel);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    function greeting() {
        const h = new Date().getHours();
        const part = h < 5 ? 'Working late' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
        return `${part}, what would you like to work on?`;
    }

    // Sidebar
    function buildSidebar() {
        const sidebar = $('#sidebar');
        const panel = $('#session-panel');
        const nav = $('#sidebar-nav');
        if (!sidebar || !panel || !nav || sidebar.dataset.lrmShell) return;
        sidebar.dataset.lrmShell = '1';

        const brand = $('.sidebar-brand', sidebar);
        const plus = document.createElement('button');
        plus.className = 'lrm-icon-btn lrm-new-chat';
        plus.title = 'New chat';
        plus.setAttribute('aria-label', 'New chat');
        plus.innerHTML = '<i class="fas fa-plus"></i>';
        plus.onclick = (e) => newChatPicker(plus, e);
        brand.appendChild(plus);

        // The sidebar is the menu. "Chats" (top of it) opens the conversation list as a page
        // in the main area: the console's own list, with its search, pin, rename and delete.
        const chats = document.createElement('section');
        chats.className = 'lrm-chats';
        chats.hidden = true;
        chats.innerHTML = `<div class="lrm-chats-head"><h1>Chats</h1>
            <button type="button" class="lrm-chats-new"><i class="fas fa-plus"></i>New chat</button>
            <button type="button" class="lrm-icon-btn lrm-chats-close" title="Close (Esc)" aria-label="Close"><i class="fas fa-xmark"></i></button></div>
            <label class="lrm-search"><i class="fas fa-magnifying-glass"></i><input type="search" placeholder="Search chats..." aria-label="Search chats"></label>
            <div class="lrm-chat-chips" role="group" aria-label="Agent"></div>
            <div class="lrm-chats-body"><div class="lrm-console-head">Console</div></div>
            <div class="lrm-transcript" hidden></div>`;
        ($('#content-area') || $('#main-content') || document.body).appendChild(chats);
        $('.lrm-chats-body', chats).appendChild(panel);
        const wa = document.createElement('div');
        wa.className = 'lrm-wa';
        $('.lrm-chats-body', chats).appendChild(wa);
        $('.lrm-chat-chips', chats).addEventListener('click', (e) => {
            const chip = e.target.closest('.lrm-chip'); if (!chip) return;
            chatFilter.agent = chip.dataset.agent; renderAgentChips(); filterSessions();
        });
        wa.addEventListener('click', (e) => {
            const row = e.target.closest('.lrm-wa-item'); if (!row) return;
            openTranscript(row.dataset.agent, row.dataset.session, row.querySelector('.session-title').textContent);
        });
        panel.classList.remove('hidden');
        $('.lrm-search input', chats).addEventListener('input', filterSessions);

        const chatsItem = document.createElement('a');
        chatsItem.className = 'sidebar-item lrm-chats-item';
        chatsItem.setAttribute('role', 'button');
        chatsItem.innerHTML = '<i class="fas fa-message item-icon"></i><span class="sidebar-label">Chats</span>';
        brand.after(chatsItem);
        chatsItem.after(nav);

        const setChats = (open) => {
            chats.hidden = !open;
            chatsItem.classList.toggle('active', open);
            document.body.classList.toggle('lrm-chats-open', open);
            if (open) {
                if (typeof loadSessionList === 'function') loadSessionList(filterSessions);
                renderAgentChips(); loadWhatsAppChats();
                $('.lrm-transcript', chats).hidden = true; $('.lrm-chats-body', chats).hidden = false;
                // phones: the sidebar slides over the page; close it to show the list
                if (window.innerWidth < 1024 && typeof closeSidebar === 'function') closeSidebar(); else $('input', chats).focus({ preventScroll: true });
            }
        };
        window.lrmOpenChats = () => setChats(true);
        chatsItem.onclick = () => setChats(chats.hidden);
        $('.lrm-chats-close', chats).onclick = () => setChats(false);
        $('.lrm-chats-new', chats).onclick = (e) => newChatPicker($('.lrm-chats-new', chats), e, () => setChats(false));
        // Opening a chat, a page from the menu or a new chat closes the list.
        panel.addEventListener('click', (e) => { if (e.target.closest('.session-item') && !e.target.closest('button')) setChats(false); });
        nav.addEventListener('click', (e) => { if (e.target.closest('.sidebar-item')) setChats(false); });
        plus.addEventListener('click', () => setChats(false));
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !chats.hidden) setChats(false); });
        const more = nav;

        const account = document.createElement('div');
        account.className = 'lrm-account';
        account.innerHTML = `<span class="lrm-avatar"><i class="fas fa-user"></i></span><span class="lrm-name">Owner</span>
            <button class="lrm-icon-btn" id="lrm-logout" title="Sign out" aria-label="Sign out"><i class="fas fa-arrow-right-from-bracket"></i></button>`;
        more.after(account);
        $('#lrm-logout', account).onclick = () => { if (typeof handleLogout === 'function') handleLogout(); };

        keepPanelOpen();
        new MutationObserver(markEmptyList).observe($('#session-list'), { childList: true, subtree: true });
        markEmptyList();
    }

    // The list lives in the sidebar now: opening, closing and toggling it are no-ops,
    // and the code that refreshes it "while open" always does.
    function keepPanelOpen() {
        try { sessionPanelOpen = true; } catch (e) { /* not defined yet */ }
        const refresh = () => { try { sessionPanelOpen = true; } catch (e) { /* ignore */ } $('#session-panel')?.classList.remove('hidden'); };
        window.toggleSessionPanel = refresh;
        window.openSessionPanel = () => { refresh(); if (typeof loadSessionList === 'function') loadSessionList(); };
        window.closeSessionPanel = refresh;
        $('#session-panel-overlay')?.classList.add('hidden');
        if (typeof loadSessionList === 'function') loadSessionList();
    }

    // Chats page: who each chat is with, filters, and the WhatsApp chats
    const chatFilter = { agent: '' };
    const dmy = (t) => { const d = new Date(t * 1000); return `${String(d.getDate()).padStart(2, '0')}-${String(d.getMonth() + 1).padStart(2, '0')}-${d.getFullYear()}`; };
    const when = (t) => { if (!t) return ''; const d = new Date(t * 1000); const today = new Date().toDateString() === d.toDateString();
        return today ? d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : dmy(t); };
    function agentList() { try { return typeof enabledAgents === 'function' ? enabledAgents() : []; } catch (e) { return []; } }
    function defaultAgentId() { try { return (typeof activeAgentId !== 'undefined' && activeAgentId) || agentList()[0]?.id || ''; } catch (e) { return ''; } }
    function sessionInfo(el) {
        try {
            const list = typeof _sessionItems !== 'undefined' ? _sessionItems : [];
            return list.find((s) => s.session_id === el.dataset.sessionId && (!el.dataset.agentId || (s.agent && s.agent.id) === el.dataset.agentId));
        } catch (e) { return null; }
    }
    function decorate() {
        document.querySelectorAll('#session-list .session-item').forEach((el) => {
            const s = sessionInfo(el);
            const name = s?.agent?.name || agentList().find((a) => a.id === (el.dataset.agentId || defaultAgentId()))?.name || '';
            const meta = `${name ? `<b>${esc(name)}</b> · ` : ''}Console${s?.last_active ? ` · ${when(s.last_active)}` : ''}`;
            let m = el.querySelector('.lrm-chat-meta');
            if (!m) { m = document.createElement('span'); m.className = 'lrm-chat-meta'; el.querySelector('.session-title')?.after(m); }
            if (m.innerHTML !== meta) m.innerHTML = meta;
        });
    }
    function filterSessions() {
        decorate();
        const q = ($('.lrm-chats .lrm-search input')?.value || '').trim().toLowerCase();
        const wanted = chatFilter.agent;
        const match = (el, agentId) => (!q || el.textContent.toLowerCase().includes(q)) && (!wanted || agentId === wanted);
        document.querySelectorAll('#session-list .session-item').forEach((el) => {
            el.classList.toggle('lrm-hide', !match(el, el.dataset.agentId || defaultAgentId()));
        });
        // Group headings with nothing left under them are hidden too.
        document.querySelectorAll('#session-list .session-group, #session-list [class*="group"]').forEach((g) => {
            const items = g.querySelectorAll('.session-item');
            if (items.length) g.classList.toggle('lrm-hide', ![...items].some((i) => !i.classList.contains('lrm-hide')));
        });
        document.querySelectorAll('.lrm-wa-item').forEach((el) => el.classList.toggle('lrm-hide', !match(el, el.dataset.agent)));
        const wa = $('.lrm-wa');
        if (wa) wa.hidden = !wa.querySelector('.lrm-wa-item:not(.lrm-hide)');
    }
    function renderAgentChips() {
        const box = $('.lrm-chat-chips');
        if (!box) return;
        const agents = agentList();
        const html = [{ id: '', name: 'All agents' }, ...agents].map((a) => `<button type="button" class="lrm-chip" data-agent="${esc(a.id)}" aria-pressed="${chatFilter.agent === a.id}">${esc(a.name || a.id)}</button>`).join('');
        if (box.innerHTML !== html) box.innerHTML = html;
    }
    async function loadWhatsAppChats() {
        const box = $('.lrm-wa');
        if (!box) return;
        try {
            const r = await fetch('/api/lrm/chats', { credentials: 'same-origin' });
            const data = await r.json();
            const rows = data.chats || [];
            box.innerHTML = rows.length ? `<div class="lrm-wa-head">WhatsApp</div>${rows.map((c) => `<button type="button" class="session-item lrm-wa-item" data-agent="${esc(c.agent.id)}" data-session="${esc(c.session_id)}">
                <i class="fab fa-whatsapp session-icon"></i><span class="session-title">${esc(c.title || c.contact)}</span>
                <span class="lrm-chat-meta"><b>${esc(c.agent.name)}</b> · WhatsApp ${esc(c.contact)} · ${when(c.last_active)}</span></button>`).join('')}` : '';
        } catch (e) { box.innerHTML = ''; }
        filterSessions();
    }
    async function openTranscript(agent, session, title) {
        const view = $('.lrm-transcript');
        view.hidden = false; $('.lrm-chats-body').hidden = true;
        view.innerHTML = `<button type="button" class="lrm-back"><i class="fas fa-arrow-left"></i> All chats</button><h2>${esc(title)}</h2><div class="lrm-t-list">Loading…</div>`;
        $('.lrm-back', view).onclick = () => { view.hidden = true; $('.lrm-chats-body').hidden = false; };
        try {
            const r = await fetch(`/api/lrm/chats?agent=${encodeURIComponent(agent)}&session=${encodeURIComponent(session)}`, { credentials: 'same-origin' });
            const msgs = (await r.json()).messages || [];
            $('.lrm-t-list', view).innerHTML = msgs.map((m) => `<div class="lrm-t ${m.role}"><div class="lrm-t-bubble">${esc(m.text)}</div><small>${m.role === 'user' ? 'Them' : 'Agent'} · ${dmy(m.at)} ${new Date(m.at * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}</small></div>`).join('') || 'No messages.';
        } catch (e) { $('.lrm-t-list', view).textContent = 'Could not load this chat.'; }
    }

    function markEmptyList() {
        const list = $('#session-list');
        if (!list) return;
        const empty = !list.querySelector('.session-item');
        let note = $('.lrm-empty-list');
        if (empty && !note) {
            note = document.createElement('div');
            note.className = 'lrm-empty-list';
            note.innerHTML = '<b>No conversations yet</b>Start a conversation with an agent and it shows up here.';
            list.before(note);
        }
        if (note) note.hidden = !empty;
        filterSessions();
    }

    // New chat: pick the agent (the stock picker lives in the chat list, out of sight)
    function newChatPicker(anchor, event, before) {
        event?.stopPropagation();
        document.querySelector('.lrm-picker')?.remove();
        const agents = agentList();
        const start = (id) => {
            before?.();
            if (typeof startSoloChat === 'function') startSoloChat(id); else if (typeof newChat === 'function') newChat(true);
            setTimeout(() => $('#chat-input')?.focus(), 60);
        };
        if (agents.length < 2) return start(agents[0]?.id);
        const box = document.createElement('div');
        box.className = 'lrm-picker';
        box.innerHTML = `<div class="lrm-picker-head">New chat with</div>${agents.map((a) => `<button type="button" data-id="${esc(a.id)}">
            <span class="lrm-picker-orb">${(() => { try { return typeof agentAvatarHTML === 'function' ? agentAvatarHTML(a, 26) : esc((a.name || a.id)[0]); } catch (e) { return esc((a.name || a.id)[0]); } })()}</span>
            <span><b>${esc(a.name || a.id)}</b><small>${esc(a.description || '')}</small></span></button>`).join('')}
            ${typeof openTeamChatModal === 'function' ? '<button type="button" data-team="1"><span class="lrm-picker-orb"><i class="fas fa-user-group"></i></span><span><b>Team chat</b><small>Several agents in one conversation</small></span></button>' : ''}`;
        document.body.appendChild(box);
        const r = anchor.getBoundingClientRect();
        box.style.top = `${Math.min(r.bottom + 6, innerHeight - box.offsetHeight - 10)}px`;
        box.style.left = `${Math.max(10, Math.min(r.left, innerWidth - box.offsetWidth - 10))}px`;
        box.addEventListener('click', (e) => {
            const b = e.target.closest('button'); if (!b) return;
            box.remove();
            if (b.dataset.team) { before?.(); openTeamChatModal(); } else start(b.dataset.id);
        });
        setTimeout(() => document.addEventListener('click', function off(e) { if (!box.contains(e.target)) { box.remove(); document.removeEventListener('click', off); } }), 0);
    }
    // Starting a chat with an agent always lands on the chat, whatever page was open.
    for (const name of ['startSoloChat', 'startChatWithAgent']) {
        const original = window[name];
        if (typeof original !== 'function') continue;
        window[name] = function (...args) {
            const out = original.apply(this, args);
            try { if (typeof currentView !== 'undefined' && currentView !== 'chat' && typeof navigateTo === 'function') navigateTo('chat'); } catch (e) { /* ignore */ }
            document.querySelector('.lrm-chats')?.setAttribute('hidden', '');
            return out;
        };
    }

    // Tool steps: one line per answer
    // An answer that used tools shows "✓ 24 steps" instead of the full list; click it to
    // see them. While it works, only the step in progress shows under that line.
    function syncSteps() {
        document.querySelectorAll('.agent-steps').forEach((box) => {
            const tools = box.querySelectorAll(':scope > .agent-tool-step');
            let bar = box.querySelector(':scope > .lrm-steps-toggle');
            if (!tools.length) { bar?.remove(); return; }
            if (!bar) {
                bar = document.createElement('button');
                bar.type = 'button';
                bar.className = 'lrm-steps-toggle';
                bar.onclick = () => { box.classList.toggle('lrm-steps-open'); syncSteps(); };
                box.prepend(bar);
            }
            const failed = box.querySelectorAll(':scope > .agent-tool-step.tool-failed').length;
            const running = box.querySelector(':scope > .agent-tool-step.tool-streaming');
            const open = box.classList.contains('lrm-steps-open');
            const label = `${running ? '<i class="fas fa-circle-notch fa-spin"></i>' : '<i class="fas fa-check"></i>'} ${running ? 'Working' : 'Used'} ${tools.length} step${tools.length === 1 ? '' : 's'}`
                + (failed ? ` <span class="lrm-steps-failed">· ${failed} failed</span>` : '')
                + ` <i class="fas fa-chevron-${open ? 'up' : 'down'} lrm-steps-chev"></i>`;
            if (bar.innerHTML !== label) bar.innerHTML = label;
        });
    }
    let stepsQueued = false;
    new MutationObserver(() => {
        if (stepsQueued) return;
        stepsQueued = true;
        requestAnimationFrame(() => { stepsQueued = false; syncSteps(); });
    }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });

    // New chat screen
    function agents() {
        try { return typeof enabledAgents === 'function' ? enabledAgents() : []; } catch (e) { return []; }
    }

    function orb(agent) {
        try {
            if (typeof agentAvatarHTML === 'function') return agentAvatarHTML(agent, 32);
        } catch (e) { /* fall back */ }
        return `<span style="width:32px;height:32px;border-radius:50%;background:linear-gradient(135deg,#7dd3c0,#22c55e)"></span>`;
    }

    function renderExplore() {
        const box = $('#lrm-explore');
        if (!box) return;
        const list = agents();
        const cards = list.map((a) => `<button type="button" class="lrm-bot" data-agent="${esc(a.id)}">
            <span class="lrm-orb">${orb(a)}</span><span><b>${esc(a.name || a.id)}</b><small>${esc(a.description || 'Ready to help')}</small></span></button>`).join('');
        box.innerHTML = `<h2>Explore agents</h2><div class="lrm-bot-grid">${cards}
            <button type="button" class="lrm-bot lrm-bot-new" data-new-agent="1"><span class="lrm-orb"><i class="fas fa-plus"></i></span>
            <span><b>New agent</b><small>Create an agent with its own memory and WhatsApp</small></span></button></div>`;
    }

    function decorateWelcome() {
        const main = $('#chat-main');
        const ws = $('#welcome-screen');
        if (!main) return;
        main.classList.toggle('lrm-empty', !!ws);
        if (!ws || ws.dataset.lrm) return;
        ws.dataset.lrm = '1';
        ws.innerHTML = `<h1 class="lrm-greeting">${esc(greeting())}</h1>`;
        renderExplore();
    }

    function buildWelcome() {
        const main = $('#chat-main');
        const composer = $('#chat-composer');
        if (!main || !composer || $('#lrm-explore')) return;
        const hint = document.createElement('p');
        hint.className = 'lrm-hint lrm-empty-only';
        const explore = document.createElement('section');
        explore.id = 'lrm-explore';
        composer.after(explore);
        explore.addEventListener('click', (e) => {
            const card = e.target.closest('[data-agent]');
            if (card && typeof startSoloChat === 'function') {
                startSoloChat(card.dataset.agent);
                setTimeout(() => $('#chat-input')?.focus(), 50);
                return;
            }
            if (e.target.closest('[data-new-agent]') && typeof navigateTo === 'function') navigateTo('agents');
        });
        new MutationObserver(decorateWelcome).observe($('#chat-messages'), { childList: true });
        decorateWelcome();
        // Agents load after boot; draw the cards again once they are known.
        let tries = 0;
        const wait = setInterval(() => {
            if (agents().length || ++tries > 40) { clearInterval(wait); renderExplore(); }
        }, 250);
    }

    // Approvals inside the chat
    // A risky tool call waits with "Waiting for your approval (code K7QP)" in its
    // card; put Approve / Deny right there.
    function approvalButtons() {
        document.querySelectorAll('#chat-messages .tool-live-output').forEach((out) => {
            const m = /Waiting for your approval \(code ([A-Z0-9]{4,6})\)/.exec(out.textContent || '');
            const bar = out.nextElementSibling?.classList.contains('lrm-approve') ? out.nextElementSibling : null;
            if (!m) { bar?.remove(); return; }
            if (bar && bar.dataset.code === m[1]) return;
            bar?.remove();
            const el = document.createElement('div');
            el.className = 'lrm-approve';
            el.dataset.code = m[1];
            el.innerHTML = `<button type="button" data-d="approve"><i class="fas fa-check"></i> Approve</button>
                <button type="button" data-d="deny"><i class="fas fa-xmark"></i> Deny</button>`;
            el.addEventListener('click', async (e) => {
                const b = e.target.closest('button');
                if (!b) return;
                el.querySelectorAll('button').forEach((x) => { x.disabled = true; });
                try {
                    const r = await fetch('/api/lrm/activity', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ op: 'decide', code: m[1], decision: b.dataset.d }) });
                    const d = await r.json();
                    el.innerHTML = d.status === 'success' ? (b.dataset.d === 'approve' ? '✅ Approved' : '🚫 Denied') : esc(d.message || 'Could not record that');
                } catch (err) { el.textContent = 'Could not record that. Try the Activity page.'; }
            });
            out.after(el);
        });
    }

    function start() {
        buildSidebar();
        buildWelcome();
        document.title = 'LRMagent';
        const msgs = $('#chat-messages');
        if (msgs && !msgs.dataset.lrmApprovals) {
            msgs.dataset.lrmApprovals = '1';
            let t = null;
            new MutationObserver(() => { clearTimeout(t); t = setTimeout(approvalButtons, 150); })
                .observe(msgs, { childList: true, subtree: true, characterData: true });
        }
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
    window.addEventListener('load', () => { start(); keepPanelOpen(); });

    // Tasks form: several WhatsApp recipients ("Also send to" checkboxes)
    // The agent works once and the result goes to each ticked recipient (the owner,
    // designated contacts, ticked groups); the server checks every one against that list.
    const lrmEsc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    function lrmTaskState() {
        // The task modal (js/views/tasks-modal.js) is an ES module, so its state is not global.
        // It publishes a live view on window.__lrmTask; the chosen recipient is also mirrored
        // into the hidden #task-edit-receiver field, which is the reliable source for the primary.
        try {
            const s = window.__lrmTask;
            if (!s) return null;
            const inst = s.inst || '';
            const all = (s.recips || []).filter((r) => (r.instance_id || r.channel_type) === inst);
            const recvEl = document.getElementById('task-edit-receiver');
            const primary = (recvEl && recvEl.value) || '';
            const saved = s.mode !== 'create' && s.task ? (s.task.action?.receivers || []) : [];
            return { inst, all, primary, saved };
        } catch { return null; }
    }
    // The modal is static (closing only hides it), so every add-on box is tied to the task it was built for.
    function lrmTaskKey() { const s = window.__lrmTask; return s ? `${s.mode || ''}|${s.task?.id || 'new'}` : ''; }
    function lrmModalOpen() { const o = document.getElementById('task-edit-modal-overlay'); return Boolean(o && !o.classList.contains('hidden')); }
    const lrmJidOf = (r) => String(r || '').split(':').pop();
    function lrmReceiversBox() {
        const wrap = document.getElementById('task-edit-recipient-wrap');
        let box = document.getElementById('lrm-receivers');
        const st = lrmTaskState();
        if (!wrap || !st || !lrmModalOpen() || !st.inst.startsWith('lrm-wa:') || wrap.classList.contains('hidden')) { box?.remove(); return; }
        const others = st.all.filter((r) => r.receiver !== st.primary);
        const task = lrmTaskKey();
        const key = `${task}|${st.inst}|${st.primary}|${others.map((r) => r.receiver).join(',')}`;
        if (box && box.dataset.key === key) return;
        // Ticks carry over only while the same task stays open (e.g. its recipient list reloaded).
        const ticked = new Set(box && box.dataset.task === task ? [...box.querySelectorAll('input:checked')].map((i) => i.value) : st.saved);
        if (!box) { box = document.createElement('div'); box.id = 'lrm-receivers'; wrap.after(box); }
        box.dataset.key = key; box.dataset.task = task;
        box.innerHTML = '<div class="lrm-recv-title">Also send to</div>' + (others.length
            ? others.map((r) => `<label class="lrm-recv"><input type="checkbox" value="${lrmEsc(r.receiver)}" ${ticked.has(r.receiver) ? 'checked' : ''}><span>${lrmEsc(r.name)}</span><small>${lrmEsc(r.hint || '')}</small></label>`).join('')
            : '<small class="lrm-recv-none">No one else yet: add designated contacts or tick groups on the WhatsApp page.</small>');
    }
    // Mention field: shown only when a WhatsApp group is the recipient. Multi-select of the
    // group's members (from the gateway) plus any number added by hand; saved as E.164.
    const LRM_MENTION_MAX = 10;
    const lrmMembersCache = {}; // successful loads only, so a failed one can be retried
    // No country is guessed: a local number (leading 0) is refused with a hint instead.
    function lrmParseNumber(raw) {
        const t = String(raw ?? '').trim().replace(/[\s().-]/g, '');
        if (!t) return { error: '' };
        if (!/^\+?\d+$/.test(t)) return { error: 'Use digits only, starting with the country code, e.g. +62812…' };
        const d = t.replace(/^\+/, '');
        if (d.startsWith('0')) return { error: 'Start with the country code instead of 0, e.g. +62812…' };
        if (d.length < 8 || d.length > 15) return { error: 'A number has 8 to 15 digits with the country code, e.g. +62812…' };
        return { value: '+' + d };
    }
    const lrmNorm = (n) => lrmParseNumber(n).value || '';
    function lrmSelectedGroupJid() {
        const st = lrmTaskState();
        if (!st || !st.inst.startsWith('lrm-wa:')) return '';
        const picks = [st.primary];
        const rbox = document.getElementById('lrm-receivers');
        if (rbox && rbox.dataset.task === lrmTaskKey()) picks.push(...[...rbox.querySelectorAll('input:checked')].map((i) => i.value));
        for (const r of picks) { const jid = lrmJidOf(r); if (jid.endsWith('@g.us')) return jid; }
        return '';
    }
    async function lrmLoadMembers(jid) {
        if (lrmMembersCache[jid]) return { ok: true, members: lrmMembersCache[jid] };
        try {
            const r = await fetch('/whatsapp/api/groups/members?jid=' + encodeURIComponent(jid), { credentials: 'same-origin' });
            if (!r.ok) return { ok: false, status: r.status };
            const d = await r.json();
            if (!Array.isArray(d.members)) return { ok: false, status: 0 };
            lrmMembersCache[jid] = d.members;
            return { ok: true, members: d.members };
        } catch { return { ok: false, status: 0 }; }
    }
    function lrmMentionPicked(box) {
        const chips = [...box.querySelectorAll('.lrm-ment-chips [data-num]')].map((c) => c.dataset.num);
        // While members load there are no checkboxes yet: the stored picks still count.
        if (box.dataset.state === 'loading') return [...new Set([...(box.lrmSaved || []), ...chips])];
        return [...new Set([...box.querySelectorAll('.lrm-ment-list input:checked')].map((i) => i.value).concat(chips))];
    }
    function lrmMentionLimit(box) {
        const n = lrmMentionPicked(box).length;
        const full = n >= LRM_MENTION_MAX;
        box.querySelector('.lrm-ment-count').textContent = `${n} of ${LRM_MENTION_MAX}`;
        for (const cb of box.querySelectorAll('.lrm-ment-list input')) cb.disabled = full && !cb.checked;
        box.querySelector('.lrm-ment-add input').disabled = full;
        box.querySelector('.lrm-ment-addbtn').disabled = full;
        box.querySelector('.lrm-ment-full').hidden = !full;
    }
    function lrmAddChip(box, v) {
        const chips = box.querySelector('.lrm-ment-chips');
        if (!v || [...chips.querySelectorAll('[data-num]')].some((c) => c.dataset.num === v)) return;
        const chip = document.createElement('span'); chip.className = 'lrm-ment-chip'; chip.dataset.num = v;
        chip.innerHTML = `${lrmEsc(v)} <button type="button" aria-label="Remove ${lrmEsc(v)}">×</button>`;
        chip.querySelector('button').onclick = () => { chip.remove(); lrmMentionLimit(box); };
        chips.appendChild(chip);
    }
    const LRM_MEMBERS_ERROR = {
        401: 'Your sign-in has expired. Sign in again, then retry.',
        404: "This group isn't open to agents yet. Tick it on the WhatsApp page, then retry.",
    };
    function lrmFillMembers(box, key, jid) {
        const list = box.querySelector('.lrm-ment-list');
        const wasError = box.dataset.state === 'error';
        // After a failure the stored numbers are already chips, so a chip removed before Retry stays removed.
        if (wasError) box.lrmSaved = [];
        box.dataset.state = 'loading';
        list.innerHTML = '<small class="lrm-ment-note">Loading the group\'s members…</small>';
        lrmMentionLimit(box);
        lrmLoadMembers(jid).then((res) => {
            if (box.dataset.key !== key || !box.isConnected) return;
            const chips = [...box.querySelectorAll('.lrm-ment-chips [data-num]')].map((c) => c.dataset.num);
            if (!res.ok) {
                const msg = LRM_MEMBERS_ERROR[res.status] || "Couldn't load the group's members. Check that WhatsApp is connected, then retry.";
                list.innerHTML = `<div class="lrm-ment-error" role="alert"><small>${lrmEsc(msg)}</small><button type="button" class="lrm-ment-retry">Retry</button></div>`;
                list.querySelector('.lrm-ment-retry').onclick = () => lrmFillMembers(box, key, jid);
                // The stored numbers stay visible and editable while the list is unavailable.
                if (!wasError) for (const v of box.lrmSaved) lrmAddChip(box, v);
                box.dataset.state = 'error';
                lrmMentionLimit(box);
                return;
            }
            const want = new Set(wasError ? chips : [...box.lrmSaved, ...chips]);
            const rows = res.members.map((m) => {
                const v = lrmNorm(m.number); if (!v) return '';
                return `<label class="lrm-recv"><input type="checkbox" value="${v}" ${want.has(v) ? 'checked' : ''}><span>${lrmEsc(m.name || v)}</span><small>${lrmEsc(v)}</small></label>`;
            }).filter(Boolean);
            list.innerHTML = rows.length ? rows.join('') : '<small class="lrm-ment-note">WhatsApp shared no member numbers for this group. Add them by hand below.</small>';
            const inList = new Set(res.members.map((m) => lrmNorm(m.number)).filter(Boolean));
            box.querySelector('.lrm-ment-chips').innerHTML = '';
            for (const v of want) if (!inList.has(v)) lrmAddChip(box, v);
            box.dataset.state = 'ready';
            lrmMentionLimit(box);
        });
    }
    function lrmMentionBox() {
        const wrap = document.getElementById('task-edit-recipient-wrap');
        let box = document.getElementById('lrm-mentions');
        const jid = lrmSelectedGroupJid();
        if (!wrap || !jid || !lrmModalOpen() || wrap.classList.contains('hidden')) { box?.remove(); return; }
        const key = `${lrmTaskKey()}|${jid}`;
        if (box && box.dataset.key === key) return; // keep the user's edits between polls
        const s = window.__lrmTask;
        const stored = s && s.mode !== 'create' && s.task ? s.task.action || {} : {};
        const saved = [...new Set((stored.mentions || []).map(lrmNorm).filter(Boolean))];
        box?.remove();
        box = document.createElement('div'); box.id = 'lrm-mentions';
        (document.getElementById('lrm-receivers') || wrap).after(box);
        box.dataset.key = key; box.dataset.jid = jid; box.lrmSaved = saved;
        box.innerHTML = `<div class="lrm-ment-head"><span class="lrm-recv-title">Mention in the group</span><small class="lrm-ment-count" aria-live="polite"></small></div>
            <div class="lrm-ment-list"></div>
            <div class="lrm-ment-add"><input type="tel" inputmode="tel" class="px-3 rounded-lg border border-slate-200 dark:border-slate-600 bg-slate-50 dark:bg-white/5 text-sm text-slate-800 dark:text-slate-100 focus:outline-none focus:border-primary-500" placeholder="+62… add a number" aria-label="Add a number to mention" aria-describedby="lrm-ment-hint"><button type="button" class="lrm-ment-addbtn">Add</button></div>
            <small class="lrm-ment-hint" id="lrm-ment-hint" role="status" hidden></small>
            <div class="lrm-ment-chips"></div>
            <small class="lrm-ment-full" hidden>That's the limit of ${LRM_MENTION_MAX} per message. Untick someone to add another.</small>
            <label class="lrm-recv"><input type="checkbox" class="lrm-ment-fromtext" ${stored.mention_from_text ? 'checked' : ''}><span>Also tag @numbers written in the reply</span></label>`;
        const addInput = box.querySelector('.lrm-ment-add input');
        const hint = box.querySelector('.lrm-ment-hint');
        const showHint = (msg) => { hint.textContent = msg; hint.hidden = !msg; addInput.toggleAttribute('aria-invalid', Boolean(msg)); };
        const addNum = () => {
            const { value, error } = lrmParseNumber(addInput.value);
            if (!value) { showHint(error); return; }
            if (lrmMentionPicked(box).length >= LRM_MENTION_MAX) return;
            addInput.value = ''; showHint('');
            const cb = [...box.querySelectorAll('.lrm-ment-list input')].find((i) => i.value === value);
            if (cb) cb.checked = true; else lrmAddChip(box, value);
            lrmMentionLimit(box);
        };
        box.querySelector('.lrm-ment-addbtn').onclick = addNum;
        addInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addNum(); } });
        addInput.addEventListener('input', () => showHint(''));
        box.querySelector('.lrm-ment-list').addEventListener('change', () => lrmMentionLimit(box));
        lrmFillMembers(box, key, jid);
    }
    // Task cards: the list's own GET /api/scheduler response is kept so each card can show its mentions.
    const lrmTaskList = new Map();
    function lrmTaskCards() {
        for (const card of document.querySelectorAll('#tasks-list [data-task-id]')) {
            const nums = (lrmTaskList.get(card.dataset.taskId)?.action?.mentions || []).filter(Boolean);
            let line = card.querySelector('.lrm-card-ment');
            if (!nums.length) { line?.remove(); continue; }
            const text = 'Mentions: ' + (nums.length > 3 ? `${nums.slice(0, 3).join(', ')} and ${nums.length - 3} more` : nums.join(', '));
            if (line && line.textContent === text) continue;
            if (!line) {
                line = document.createElement('p'); line.className = 'lrm-card-ment';
                const desc = card.querySelector(':scope > p');
                if (desc) desc.after(line); else card.append(line);
            }
            line.textContent = text;
        }
    }
    function lrmTaskTick() {
        lrmReceiversBox(); lrmMentionBox(); lrmTaskCards();
        // React at once to the modal opening or closing and to the list re-rendering, not only on the poll.
        const overlay = document.getElementById('task-edit-modal-overlay');
        if (overlay && !overlay.dataset.lrmWatch) {
            overlay.dataset.lrmWatch = '1';
            new MutationObserver(lrmTaskTick).observe(overlay, { attributes: true, attributeFilter: ['class'] });
        }
        const list = document.getElementById('tasks-list');
        if (list && !list.dataset.lrmWatch) {
            list.dataset.lrmWatch = '1';
            new MutationObserver(lrmTaskCards).observe(list, { childList: true });
        }
    }
    setInterval(lrmTaskTick, 500);
    const lrmFetch = window.fetch.bind(window);
    function lrmTaskBody(body) {
        const s = window.__lrmTask;
        const current = lrmTaskKey();
        const task = s && s.mode !== 'create' && s.task && (!body.task_id || s.task.id === body.task_id) ? s.task : null;
        const stored = task?.action || {};
        const rbox = document.getElementById('lrm-receivers');
        if (rbox && rbox.dataset.task === current) {
            const extra = [...rbox.querySelectorAll('input:checked')].map((i) => i.value);
            body.action.receivers = extra.length ? [body.action.receiver, ...extra].filter(Boolean) : [];
        } else if (Array.isArray(stored.receivers) && stored.receiver === body.action.receiver) {
            body.action.receivers = stored.receivers; // saved before the box was built
        }
        const jids = [body.action.receiver, ...(body.action.receivers || [])].map(lrmJidOf);
        const mbox = document.getElementById('lrm-mentions');
        if (!jids.some((j) => j.endsWith('@g.us'))) {
            body.action.mentions = []; // no group chosen: carry no tags
            body.action.mention_from_text = false;
        } else if (mbox && mbox.dataset.key === `${current}|${mbox.dataset.jid}` && jids.includes(mbox.dataset.jid)) {
            body.action.mentions = lrmMentionPicked(mbox).slice(0, LRM_MENTION_MAX);
            body.action.mention_from_text = Boolean(mbox.querySelector('.lrm-ment-fromtext')?.checked);
        } else {
            // No box for this task and group yet: keep what the task already had.
            body.action.mentions = Array.isArray(stored.mentions) ? stored.mentions : [];
            body.action.mention_from_text = Boolean(stored.mention_from_text);
        }
    }
    window.fetch = (input, init) => {
        let listRequest = false;
        try {
            const url = typeof input === 'string' ? input : input?.url || '';
            const path = url.split('?')[0].replace(/^https?:\/\/[^/]+/, '');
            listRequest = path === '/api/scheduler' && (!init || !init.method || init.method === 'GET');
            if (/\/api\/scheduler\/(create|update)$/.test(path) && init && typeof init.body === 'string') {
                const body = JSON.parse(init.body);
                if (body.action && String(body.action.instance_id || '').startsWith('lrm-wa:')) {
                    lrmTaskBody(body);
                    init = { ...init, body: JSON.stringify(body) };
                }
            }
        } catch { /* leave the request as it was */ }
        const p = lrmFetch(input, init);
        if (!listRequest) return p;
        return p.then((r) => {
            r.clone().json().then((d) => {
                if (!d || !Array.isArray(d.tasks)) return;
                lrmTaskList.clear();
                for (const t of d.tasks) lrmTaskList.set(String(t.id), t);
                lrmTaskCards();
            }).catch(() => {});
            return r;
        });
    };

    // The private vault's 7-day clock: using the console counts as the owner being around.
    let seenAt = 0;
    const seen = () => {
        if (document.hidden || Date.now() - seenAt < 10 * 60 * 1000) return;
        seenAt = Date.now();
        fetch('/whatsapp/safe/api/seen', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: '{}' }).catch(() => {});
    };
    seen();
    setInterval(seen, 30 * 60 * 1000);
    document.addEventListener('visibilitychange', seen);
})();
