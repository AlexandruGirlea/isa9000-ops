(() => {
  for (const toast of document.querySelectorAll('.portal-toast')) {
    const dismiss = () => toast.remove();
    toast.querySelector('button').addEventListener('click', dismiss);
    setTimeout(dismiss, 8000);
  }
  window.portalConfirm = (message, action) => new Promise(resolve => {
    const dialog = document.createElement('dialog');
    dialog.className = 'portal-confirm';
    const text = document.createElement('p');
    text.textContent = message;
    const stay = document.createElement('button');
    stay.type = 'button';
    stay.textContent = 'Go back';
    const proceed = document.createElement('button');
    proceed.type = 'button';
    proceed.className = 'portal-confirm-action';
    proceed.textContent = action;
    const actions = document.createElement('div');
    actions.append(stay, proceed);
    dialog.append(text, actions);
    let answer = false;
    stay.addEventListener('click', () => dialog.close());
    proceed.addEventListener('click', () => { answer = true; dialog.close(); });
    dialog.addEventListener('close', () => { dialog.remove(); resolve(answer); });
    document.body.append(dialog);
    dialog.showModal();
  });
  const confirmed = new WeakSet();
  document.addEventListener('submit', async event => {
    const submitter = event.submitter;
    const message = submitter?.dataset.confirm;
    if (!message || confirmed.has(event.target)) return;
    event.preventDefault();
    if (await window.portalConfirm(message, submitter.textContent.trim())) {
      confirmed.add(event.target);
      event.target.requestSubmit(submitter);
    }
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
