"use strict";
(() => {
  const field = document.getElementById('commandInput');
  const base = 'https://padlet-sender-panel.onrender.com';
  let pending = false;
  // No Google handlers, navigation, form submissions or ordinary searches.
  for (const type of ['click', 'auxclick', 'submit', 'dragstart']) {
    document.addEventListener(type, event => {
      if (type === 'click' && event.target === field) return;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
  }

  function fields(text) {
    const values = [];
    let i = 0;
    while (i < text.length) {
      while (/\s/.test(text[i] || '') && i < text.length) i++;
      let value = '';
      if (text[i] === '"') {
        i++;
        let closed = false;
        while (i < text.length) {
          if (text[i] === '"') {
            if (text[i + 1] === '"') { value += '"'; i += 2; continue; }
            i++; closed = true; break;
          }
          value += text[i++];
        }
        if (!closed) return null;
      } else if (text[i] === '(') {
        i++;
        let depth = 1;
        while (i < text.length && depth) {
          const char = text[i++];
          if (char === '(') depth++;
          if (char === ')') depth--;
          if (depth) value += char;
        }
        if (depth) return null;
      } else {
        while (i < text.length && text[i] !== ',') value += text[i++];
      }
      while (i < text.length && /\s/.test(text[i])) i++;
      if (i < text.length && text[i] !== ',') return null;
      values.push(value.trim());
      if (values.length > 6) return null;
      if (i < text.length) {
        i++;
        if (!text.slice(i).trim()) values.push('');
      }
    }
    return values;
  }

  function command(text) {
    const parts = fields(text.trim());
    if (!parts || (parts.length !== 5 && parts.length !== 6)) return null;
    const [address] = parts;
    let url;
    try { url = new URL(address); } catch (_) { return null; }
    if (url.protocol !== 'https:' || !['padlet.com', 'www.padlet.com'].includes(url.hostname) ||
        url.username || url.password || url.port || !/^\/[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)?\/?$/.test(url.pathname) ||
        ['api', 'dashboard', 'auth', 'login'].includes(url.pathname.split('/')[1]) ||
        address.length > 2000) return null;
    const count = parts.at(-2), interval = parts.at(-1);
    if (!/^\d+$/.test(count) || !/^(?:\d+(?:\.\d+)?|\.\d+)$/.test(interval)) return null;
    const limit = Number(count), delay = Number(interval);
    if (!Number.isSafeInteger(limit) || limit > 1000000 || !Number.isFinite(delay) || delay > 86400) return null;
    const subject = parts.at(-4), body = parts.at(-3);
    if ((!subject && !body) || subject.length > 1000 || body.length > 20000) return null;
    const result = {url: address, subject, body, limit, delay};
    if (parts.length === 6) {
      if (!parts[1] || parts[1].length > 1024) return null;
      result.password = parts[1];
    }
    return result;
  }

  async function request(path, token, data, requestId) {
    const response = await fetch(base + path, {
      method: data === undefined ? 'GET' : 'POST',
      headers: {Authorization: 'Bearer ' + token,
        ...(data === undefined ? {} : {'Content-Type': 'application/json', 'X-Request-ID': requestId})},
      body: data === undefined ? undefined : JSON.stringify(data),
      cache: 'no-store', signal: AbortSignal.timeout(20000),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '요청을 처리하지 못했습니다.');
    return result;
  }

  field.addEventListener('input', () => field.setCustomValidity(''));
  field.addEventListener('keydown', async event => {
    if (event.key !== 'Enter' || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    if (pending || event.repeat) return;
    const data = command(field.value);
    if (!data) return;
    pending = true;
    field.value = ''; // Never retain a board password in the search box.
    field.setCustomValidity('');
    try {
      const token = sessionStorage.getItem('padlet-session');
      if (!token) throw new Error('뒤로 가기로 돌아가 이용 키를 먼저 인증하세요.');
      const state = await request('/api/state', token);
      const access = state.access;
      if (state.running || state.opening || state.deleting || state.phase === 'stopping') {
        throw new Error('이미 진행 중인 작업이 있습니다. 뒤로 가기에서 상태를 확인하세요.');
      }
      if (!access?.known || access.can_run === false || access.blocked || access.remaining === 0 ||
          (access.expires != null && Date.now() >= access.expires * 1000)) {
        throw new Error('이용 키를 다시 확인하세요. 뒤로 가기에서 인증할 수 있습니다.');
      }
      if (data.limit > (access.post_limit ?? 5000)) throw new Error('이 이용권의 섹션당 게시물 개수 제한을 초과했습니다.');
      await request('/api/run', token, data, crypto.randomUUID());
    } catch (error) {
      field.setCustomValidity(error instanceof TypeError || error.name === 'TimeoutError'
        ? '서버 응답을 확인하지 못했습니다. 뒤로 가기로 실행 상태를 확인하세요.' : error.message);
      field.reportValidity();
    } finally {
      if (data.password) data.password = '';
      pending = false;
    }
  });
  window.addEventListener('pagehide', () => { field.value = ''; });
})();
