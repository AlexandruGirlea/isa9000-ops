(() => {
  document.addEventListener('submit', event => {
    const message = event.submitter?.dataset.confirm;
    if (message && !window.confirm(message)) event.preventDefault();
  });
  let lastInput = 0;
  let lastSent = Date.now();
  let pending = false;

  async function keepActive() {
    const playing = document.querySelector('.playlist-track.is-playing');
    if (pending || (!playing && lastInput <= lastSent)) return;
    const csrf = document.cookie.split('; ').find(value => value.startsWith('csrftoken='));
    if (!csrf) return;
    pending = true;
    lastSent = Date.now();
    try {
      const response = await fetch('/session/activity/', {
        method: 'POST', credentials: 'same-origin', redirect: 'manual', cache: 'no-store',
        headers: {'X-CSRFToken': decodeURIComponent(csrf.slice('csrftoken='.length))},
      });
      if (response.status === 401 || response.status === 403 || response.type === 'opaqueredirect') {
        const result = await response.json().catch(() => ({}));
        location.assign(result.signin || '/');
      }
    } catch {
      lastSent = 0;
    } finally {
      pending = false;
    }
  }

  for (const event of ['pointerdown', 'pointermove', 'keydown', 'scroll', 'touchstart']) {
    document.addEventListener(event, action => {
      if (action.isTrusted) lastInput = Date.now();
    }, {passive: true, capture: true});
  }
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      lastInput = Date.now();
      keepActive();
    }
  });
  setInterval(keepActive, 60000);
})();
