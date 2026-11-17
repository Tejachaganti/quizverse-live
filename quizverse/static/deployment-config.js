/* Local development defaults to the FastAPI origin. The Vercel build replaces this file. */
(() => {
  'use strict';
  const configured = String(window.QUIZVERSE_API_BASE_URL || '').trim();
  const base = new URL(configured || window.location.origin);
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password || base.search || base.hash || base.pathname !== '/') {
    throw new Error('QuizVerse API URL must be an origin without credentials or a path.');
  }
  const apiOrigin = base.origin;
  window.QuizVerseAPI = Object.freeze({
    url(path) {
      return new URL(path, apiOrigin).toString();
    },
    fetch(path, options) {
      return window.fetch(new URL(path, apiOrigin).toString(), options);
    },
    websocket(path) {
      const socketOrigin = new URL(apiOrigin);
      socketOrigin.protocol = socketOrigin.protocol === 'https:' ? 'wss:' : 'ws:';
      return new URL(path, socketOrigin).toString();
    },
  });
})();
