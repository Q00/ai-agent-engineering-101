(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const storageKey = 'dyishappy.round4.shopping-brief.v1';
  let brief = window.ShopBrief.unset(), lastStateAt = 0, renderedChat = '', renderedActivity = '';
  let activeConsent = null, sending = false;
  try { brief = window.ShopBrief.restore(sessionStorage.getItem(storageKey)); } catch (_) {}
  function fillBrief() {
    $('budget').value = brief.budget_credits ?? '';
    $('interests').value = brief.interests.join(', ');
    $('priority').value = brief.priority;
    $('brief-status').textContent = brief.status === 'saved' ? '저장됨' : '미설정';
    $('brief-feedback').textContent = brief.status === 'saved' ? '에이전트가 이 조건을 읽을 수 있습니다.' : '아직 저장한 조건이 없습니다.';
  }
  function persist() { try { sessionStorage.setItem(storageKey, JSON.stringify(brief)); } catch (_) {} }
  $('brief-form').addEventListener('submit', e => {
    e.preventDefault();
    try {
      brief = window.ShopBrief.normalize({budget: $('budget').value, interests: $('interests').value, priority: $('priority').value});
      persist(); fillBrief(); $('brief-feedback').classList.remove('error');
    } catch (error) { $('brief-feedback').textContent = error.message; $('brief-feedback').classList.add('error'); }
  });
  $('reset-brief').addEventListener('click', () => { brief = window.ShopBrief.unset(); persist(); fillBrief(); $('brief-feedback').classList.remove('error'); });
  fillBrief();

  if (!window.miniapp) {
    $('mode-bar').textContent = '앱 서버에 연결하지 못했습니다. 실행한 서버 주소에서 다시 열어 주세요.';
    $('connection').textContent = '연결 안 됨'; $('send').disabled = true; return;
  }
  window.miniapp.offer('shopping_brief', 'Read the user-saved shopping budget in credits, interests and preference. No arguments. Informational only: not permission to order, pay or transfer money.', () => JSON.parse(JSON.stringify(brief)))
    .then(response => {
      if (!response.ok) throw new Error('offer failed');
      $('tool-status').textContent = '구매 조건 기능 연결됨';
    }).catch(() => { $('tool-status').textContent = '구매 조건 기능 연결 실패 · 새로고침해 주세요'; });

  fetch('/api/preparation').then(response => response.ok ? response.json() : null).then(mode => {
    $('mode-bar').textContent = mode?.mode === 'fixture'
      ? '로컬 미리보기 · 응답은 테스트용이며 시장 거래와 송금은 실행되지 않습니다.'
      : mode?.mode === 'claude'
        ? 'Claude 대화 테스트 · 시장에 연결하지 않고 앱 대화만 확인합니다.'
        : '에이전트 앱 · 구매 조건을 저장하고 대화를 시작하세요.';
  }).catch(() => { $('mode-bar').textContent = '에이전트 앱 · 연결 상태를 확인해 주세요.'; });

  function node(tag, className, text) {
    const el = document.createElement(tag); if (className) el.className = className;
    if (text !== undefined) el.textContent = text; return el;
  }
  function renderChat(chat) {
    const signature = JSON.stringify(chat);
    if (signature === renderedChat) return;
    renderedChat = signature;
    const scroll = $('chat-scroll'), nearEnd = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 90;
    const fragment = document.createDocumentFragment();
    for (const msg of chat) {
      const agent = msg.who === 'agent', system = msg.who === 'kit';
      const item = node('article', 'message' + (!agent && !system ? ' user' : ''));
      item.append(node('span', 'message-label', agent ? '덕용상점' : system ? '안내' : '나'));
      item.append(node('div', 'message-body', String(msg.text ?? ''))); fragment.append(item);
    }
    $('messages').replaceChildren(fragment); $('welcome').hidden = chat.length > 0;
    if (nearEnd || sending) scroll.scrollTop = scroll.scrollHeight;
  }
  function renderActivity(state) {
    const signature = JSON.stringify([state.forged, state.moves]);
    if (signature === renderedActivity) return;
    renderedActivity = signature;
    const tools = Object.entries(state.forged || {});
    $('tool-count').textContent = '도구 ' + tools.length + '개';
    $('tools').replaceChildren();
    for (const [name, description] of tools) {
      const item = node('li'); item.append(node('strong', '', name), node('p', '', description)); $('tools').append(item);
    }
    if (!tools.length) $('tools').append(node('li', 'empty', '아직 생성한 도구가 없습니다.'));
    $('moves').replaceChildren();
    for (const move of (state.moves || []).slice(0, 8)) {
      const item = node('li'); item.append(node('strong', '', move.action), node('p', '', move.thought || '')); $('moves').append(item);
    }
    if (!(state.moves || []).length) $('moves').append(node('li', 'empty', '활동이 생기면 여기에 표시됩니다.'));
  }
  function renderConsent(calls) {
    if (activeConsent && calls.some(c => c.id === activeConsent.id)) return;
    activeConsent = calls[0] || null;
    const dialog = $('consent-dialog');
    if (!activeConsent) { if (dialog.open) dialog.close(); return; }
    $('consent-name').textContent = activeConsent.name;
    $('consent-args').textContent = JSON.stringify(activeConsent.args, null, 2);
    $('consent-feedback').textContent = '';
    $('allow-consent').disabled = $('deny-consent').disabled = false;
    if (!dialog.open) dialog.showModal();
  }
  window.miniapp.onState(state => {
    lastStateAt = Date.now(); $('connection').textContent = sending ? '답변 기다리는 중' : '앱 연결됨';
    renderChat(state.chat || []); renderActivity(state); renderConsent(state.consents || []);
  });
  setInterval(() => { if (!lastStateAt || Date.now() - lastStateAt > 7000) $('connection').textContent = '연결 확인 중'; }, 3000);

  async function consent(approved) {
    if (!activeConsent) return;
    $('allow-consent').disabled = $('deny-consent').disabled = true;
    try {
      const response = await window.miniapp.consent(activeConsent.id, approved);
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error('request expired');
      activeConsent = null; $('consent-dialog').close();
    } catch (_) { $('consent-feedback').textContent = '요청이 만료됐거나 연결이 끊겼습니다. 상태를 다시 확인해 주세요.'; }
  }
  $('allow-consent').addEventListener('click', () => consent(true));
  $('deny-consent').addEventListener('click', () => consent(false));
  $('consent-dialog').addEventListener('cancel', e => { e.preventDefault(); consent(false); });

  $('chat-form').addEventListener('submit', async e => {
    e.preventDefault(); const text = $('message').value.trim(); if (!text || sending) return;
    sending = true; $('send').disabled = true; $('message').readOnly = true;
    $('chat-feedback').classList.remove('error'); $('chat-feedback').textContent = '에이전트가 답변하고 있습니다…';
    try {
      const answer = await window.miniapp.chat(text, 'owner');
      if (answer.error || answer.reply === null || typeof answer.reply !== 'string') throw new Error('busy');
      $('message').value = ''; $('chat-feedback').textContent = '답변을 받았습니다.';
    } catch (_) {
      $('chat-feedback').textContent = '답변을 받지 못했습니다. 연결과 실행 로그를 확인한 뒤 다시 보내세요.';
      $('chat-feedback').classList.add('error');
    } finally { sending = false; $('send').disabled = false; $('message').readOnly = false; }
  });
  $('message').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('chat-form').requestSubmit(); }
  });
  document.querySelectorAll('[data-prompt]').forEach(button => button.addEventListener('click', () => {
    $('message').value = button.dataset.prompt; $('message').focus();
  }));
})();
