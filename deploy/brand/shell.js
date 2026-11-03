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

    // ---- sidebar ------------------------------------------------------------
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
        plus.onclick = (e) => { if (typeof onNewChatButton === 'function') onNewChatButton(e); };
        brand.appendChild(plus);

        const search = document.createElement('label');
        search.className = 'lrm-search';
        search.innerHTML = '<i class="fas fa-magnifying-glass"></i><input type="search" placeholder="Search..." aria-label="Search conversations">';
        brand.after(search);
        search.querySelector('input').addEventListener('input', filterSessions);

        // Conversations: the stock panel, always open, in the sidebar.
        search.after(panel);
        panel.classList.remove('hidden');
        panel.after(nav);

        const more = document.createElement('button');
        more.type = 'button';
        more.className = 'lrm-more';
        const setMore = (open) => {
            sidebar.classList.toggle('lrm-more-open', open);
            more.innerHTML = open ? '<i class="fas fa-chevron-up"></i>Less' : '<i class="fas fa-ellipsis"></i>More';
            try { localStorage.setItem('lrm_menu_more', open ? '1' : '0'); } catch (e) { /* private mode */ }
        };
        let moreOpen = false;
        try { moreOpen = localStorage.getItem('lrm_menu_more') === '1'; } catch (e) { /* private mode */ }
        setMore(moreOpen);
        more.onclick = () => setMore(!sidebar.classList.contains('lrm-more-open'));
        nav.after(more);

        const account = document.createElement('div');
        account.className = 'lrm-account';
        account.innerHTML = `<span class="lrm-avatar"><i class="fas fa-user"></i></span><span class="lrm-name">Owner</span>
            <button class="lrm-icon-btn" id="lrm-theme" title="Light or dark" aria-label="Switch light or dark"><i class="fas fa-circle-half-stroke"></i></button>
            <button class="lrm-icon-btn" id="lrm-logout" title="Sign out" aria-label="Sign out"><i class="fas fa-arrow-right-from-bracket"></i></button>`;
        more.after(account);
        $('#lrm-theme', account).onclick = () => { if (typeof toggleTheme === 'function') toggleTheme(); };
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

    function filterSessions() {
        const q = ($('.lrm-search input')?.value || '').trim().toLowerCase();
        document.querySelectorAll('#session-list .session-item').forEach((el) => {
            el.style.display = !q || el.textContent.toLowerCase().includes(q) ? '' : 'none';
        });
        // Group headings with nothing left under them are hidden too.
        document.querySelectorAll('#session-list .session-group, #session-list [class*="group"]').forEach((g) => {
            const items = g.querySelectorAll('.session-item');
            if (items.length) g.style.display = [...items].some((i) => i.style.display !== 'none') ? '' : 'none';
        });
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

    // ---- new chat screen --------------------------------------------------------
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

    function start() {
        buildSidebar();
        buildWelcome();
        document.title = 'LRMagent';
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
    window.addEventListener('load', () => { start(); keepPanelOpen(); });
})();
