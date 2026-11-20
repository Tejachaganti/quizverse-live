(() => {
  'use strict';

  const OWNER_TOKEN_KEY = 'qv-owner-capability';
  const UNSAVED_KEY = 'qv-unsaved-quiz';
  const state = {
    root: null,
    notify: () => {},
    page: 'library',
    tab: 'public',
    publicQuizzes: [],
    ownedQuizzes: [],
    draft: null,
    previewReturn: 'editor',
    ownerToken: sessionStorage.getItem(OWNER_TOKEN_KEY) || '',
    pendingOwner: null,
    ownerRejected: false,
    pendingDuplicate: null,
    notice: '',
    loading: false,
    filters: { category: '', difficulty: '', q: '' },
    generationTicket: '',
    generationMethod: 'topic',
    aiAvailable: false,
    taxonomyCategories: [],
    taxonomyCategoriesLoading: false,
    taxonomyCategoriesLoaded: false,
  };

  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character]);
  const categoryKey = value => String(value || '').trim().toLowerCase().replace(/\s+/g, '-');

  function rememberDraft() {
    if (!state.draft) return;
    try {
      sessionStorage.setItem(UNSAVED_KEY, JSON.stringify(state.draft));
    } catch {
      state.notice = 'This browser could not keep an unsaved copy. Save your changes before leaving the editor.';
    }
  }

  function restoreDraft() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(UNSAVED_KEY) || 'null');
      if (saved && typeof saved === 'object' && typeof saved.title === 'string' && Array.isArray(saved.questions)) {
        return saved;
      }
    } catch {
      sessionStorage.removeItem(UNSAVED_KEY);
    }
    return null;
  }

  function request(path, options = {}, owner = false) {
    const headers = new Headers(options.headers || {});
    if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    if (owner && state.ownerToken) headers.set('Authorization', `Bearer ${state.ownerToken}`);
    return QuizVerseAPI.fetch(path, { ...options, headers, cache: 'no-store' }).then(async response => {
      let data = null;
      try { data = await response.json(); } catch { /* Empty and malformed error bodies are handled safely. */ }
      return { response, data };
    });
  }

  function errorText(status) {
    if (status === 401) return 'This owner key was not accepted. It was not replaced; use another key or create a separate owner.';
    if (status === 404) return 'That quiz is unavailable or you do not have access to it.';
    if (status === 409) return 'This quiz changed or cannot be shared in its current state. Refresh your library and try again.';
    if (status === 422) return 'The server rejected some quiz details. Review the highlighted fields and try again.';
    if (status === 429) return 'Too many owner keys were requested. Wait a little before trying again.';
    if (status >= 500) return 'Quiz storage is temporarily unavailable. Your unsaved edits remain in this tab.';
    return 'That action could not be completed. Check the quiz details and try again.';
  }

  function showError(status) {
    state.notice = errorText(status);
    if (status === 401) {
      state.ownerRejected = true;
      state.page = 'owner-setup';
    }
    render();
  }

  function setNotice(message, announce = true) {
    state.notice = message;
    render();
    if (announce) state.notify(message);
  }

  function newDraft() {
    return {
      id: null,
      version: 1,
      title: '',
      description: '',
      category: '',
      difficulty: 'medium',
      visibility: 'private',
      default_time_limit_seconds: 15,
      questions: [],
      generation_ticket: '',
    };
  }

  function questionDraft(question = {}) {
    const questionFormat = question.format || 'multiple_choice';
    return {
      prompt: question.prompt || '',
      format: questionFormat,
      format_payload: question.format_payload ? JSON.parse(JSON.stringify(question.format_payload)) : null,
      options: Array.isArray(question.options) ? [...question.options] : questionFormat === 'multiple_choice' ? ['', '', '', ''] : null,
      correct_option_index: Number.isInteger(question.correct_option_index) ? question.correct_option_index : questionFormat === 'multiple_choice' ? 0 : null,
      explanation: question.explanation || '',
      category: question.category || '',
      category_id: question.category_id || null,
      tags: Array.isArray(question.tags) ? [...question.tags] : [],
      difficulty: question.difficulty || '',
      time_limit_seconds: question.time_limit_seconds ?? '',
      language: question.language || 'en',
      learning_levels: Array.isArray(question.learning_levels) ? [...question.learning_levels] : [],
      source_name: question.source_name || null,
      source_url: question.source_url || null,
      source_published_at: question.source_published_at || null,
      verified_at: question.verified_at || null,
      review_due_at: question.review_due_at || null,
      review_status: question.review_status || 'unreviewed',
    };
  }

  function ownerGate() {
    state.page = 'owner-setup';
    render();
  }

  function createOwner() {
    state.pendingOwner = null;
    state.notice = '';
    state.loading = true;
    render();
    request('/api/quiz-owners', { method: 'POST' }).then(({ response, data }) => {
      state.loading = false;
      if (!response.ok || !data || !data.capability) {
        showError(response.status);
        return;
      }
      state.pendingOwner = { token: data.capability, ownerId: data.owner_id };
      state.page = 'owner-key';
      render();
    }).catch(() => {
      state.loading = false;
      state.notice = 'Quiz storage could not be reached. No new owner key was saved.';
      render();
    });
  }

  function saveOwnerKey(token, ownerId = null) {
    const previousToken = state.ownerToken;
    try {
      sessionStorage.setItem(OWNER_TOKEN_KEY, token);
    } catch {
      state.ownerToken = token;
      state.notice = 'The key is active for this tab but could not be stored. Copy it before leaving.';
      return false;
    }
    state.ownerToken = token;
    if (ownerId) {
      try { sessionStorage.setItem('qv-owner-id', ownerId); } catch { /* Owner token remains usable in this tab. */ }
    }
    state.ownerRejected = false;
    if (previousToken && previousToken !== token) {
      state.draft = null;
      sessionStorage.removeItem(UNSAVED_KEY);
    }
    return true;
  }

  function continueWithNewOwner() {
    if (!state.pendingOwner) return;
    saveOwnerKey(state.pendingOwner.token, state.pendingOwner.ownerId);
    state.pendingOwner = null;
    state.page = 'library';
    state.tab = 'mine';
    state.notice = 'Owner key is active in this tab. Existing quizzes were not moved or replaced.';
    loadOwned();
  }

  async function restoreOwnerKey() {
    const field = state.root.querySelector('#owner-key-entry');
    const token = field?.value.trim() || '';
    if (token.length < 40 || token.length > 128) {
      state.notice = 'Paste a valid owner key to open its quiz library.';
      render();
      return;
    }
    state.ownerToken = token;
    state.loading = true;
    render();
    try {
      const { response, data } = await request('/api/quizzes/mine', {}, true);
      state.loading = false;
      if (!response.ok || !data) {
        state.ownerToken = sessionStorage.getItem(OWNER_TOKEN_KEY) || '';
        showError(response.status);
        return;
      }
      saveOwnerKey(token);
      state.ownedQuizzes = data.quizzes;
      state.ownerRejected = false;
      state.notice = 'Owner key accepted. Your quiz library is ready.';
      state.page = 'library';
      state.tab = 'mine';
      render();
    } catch {
      state.loading = false;
      state.ownerToken = sessionStorage.getItem(OWNER_TOKEN_KEY) || '';
      state.notice = 'Quiz storage could not be reached. The key was not changed.';
      render();
    }
  }

  async function loadPublic() {
    state.loading = true;
    render();
    const params = new URLSearchParams();
    if (state.filters.category.trim()) params.set('category', categoryKey(state.filters.category));
    if (state.filters.difficulty) params.set('difficulty', state.filters.difficulty);
    if (state.filters.q.trim()) params.set('q', state.filters.q.trim());
    params.set('limit', '100');
    try {
      const { response, data } = await request(`/api/quizzes?${params.toString()}`);
      state.loading = false;
      if (!response.ok || !data) {
        showError(response.status);
        return;
      }
      state.publicQuizzes = data.quizzes;
      state.notice = '';
      render();
    } catch {
      state.loading = false;
      state.notice = 'The quiz library could not be reached. Check your connection and try again.';
      render();
    }
  }

  async function loadOwned() {
    if (!state.ownerToken) {
      ownerGate();
      return;
    }
    state.loading = true;
    render();
    try {
      const { response, data } = await request('/api/quizzes/mine', {}, true);
      state.loading = false;
      if (!response.ok || !data) {
        showError(response.status);
        return;
      }
      state.ownedQuizzes = data.quizzes;
      state.notice = '';
      render();
    } catch {
      state.loading = false;
      state.notice = 'Your quiz library could not be reached. Your owner key was not changed.';
      render();
    }
  }

  function beginNewQuiz() {
    if (!state.ownerToken) {
      state.pendingDuplicate = null;
      ownerGate();
      return;
    }
    const recoverable = restoreDraft();
    state.draft = recoverable && !recoverable.id ? recoverable : newDraft();
    state.generationTicket = state.draft.generation_ticket || '';
    state.page = 'editor';
    state.notice = '';
    rememberDraft();
    render();
  }

  async function editQuiz(id) {
    if (!state.ownerToken) {
      ownerGate();
      return;
    }
    state.loading = true;
    render();
    try {
      const { response, data } = await request(`/api/quizzes/${encodeURIComponent(id)}`, {}, true);
      state.loading = false;
      if (!response.ok || !data || !Array.isArray(data.questions)) {
        showError(response.status);
        return;
      }
      state.draft = {
        id: data.id,
        version: data.version,
        title: data.title,
        description: data.description || '',
        category: data.category,
        difficulty: data.difficulty,
        visibility: data.visibility,
        default_time_limit_seconds: data.default_time_limit_seconds,
        status: data.status,
        questions: data.questions.map(questionDraft),
      };
      rememberDraft();
      state.page = 'editor';
      state.notice = '';
      render();
    } catch {
      state.loading = false;
      state.notice = 'The quiz could not be opened. Your existing drafts are unchanged.';
      render();
    }
  }

  function readEditor() {
    if (!state.draft || !state.root) return;
    const draft = state.draft;
    state.root.querySelectorAll('[data-quiz-field]').forEach(field => {
      const name = field.dataset.quizField;
      draft[name] = field.type === 'number' ? Number(field.value) : field.value;
    });
    state.root.querySelectorAll('[data-question-index]').forEach(card => {
      const index = Number(card.dataset.questionIndex);
      const question = draft.questions[index];
      if (!question) return;
      card.querySelectorAll('[data-question-field]').forEach(field => {
        const name = field.dataset.questionField;
        question[name] = field.type === 'number'
          ? (field.value === '' ? null : Number(field.value))
          : field.value;
      });
      card.querySelectorAll('[data-option-index]').forEach(field => {
        question.options[Number(field.dataset.optionIndex)] = field.value;
      });
    });
    rememberDraft();
  }

  function validateDraft() {
    const draft = state.draft;
    const problems = [];
    if (!draft.title.trim() || draft.title.trim().length > 100) problems.push('Add a title (1–100 characters).');
    const normalizedCategory = categoryKey(draft.category);
    if (!/^[a-z0-9][a-z0-9_-]{0,49}$/.test(normalizedCategory)) {
      problems.push('Use a category with letters, numbers, hyphens, or underscores.');
    }
    if (!Number.isInteger(Number(draft.default_time_limit_seconds)) || draft.default_time_limit_seconds < 5 || draft.default_time_limit_seconds > 120) {
      problems.push('Default question time must be between 5 and 120 seconds.');
    }
    if (draft.questions.length > 200) problems.push('A quiz can contain at most 200 questions.');
    draft.questions.forEach((question, index) => {
      const label = `Question ${index + 1}`;
      if (!question.prompt.trim() || question.prompt.trim().length > 500) problems.push(`${label}: add a prompt (up to 500 characters).`);
      if (question.format === 'multiple_choice') {
        if (!Array.isArray(question.options) || question.options.length !== 4 || question.options.some(option => !String(option).trim() || String(option).length > 200)) {
          problems.push(`${label}: provide four answer options (up to 200 characters each).`);
        } else if (new Set(question.options.map(option => option.trim().normalize('NFC').toLocaleLowerCase())).size !== 4) {
          problems.push(`${label}: answer options must be distinct.`);
        }
        if (!Number.isInteger(Number(question.correct_option_index)) || Number(question.correct_option_index) < 0 || Number(question.correct_option_index) > 3) {
          problems.push(`${label}: select the correct answer.`);
        }
      } else if (!question.format_payload || typeof question.format_payload !== 'object') {
        problems.push(`${label}: its structured format content is missing.`);
      }
      if (question.explanation.length > 1000) problems.push(`${label}: explanation must be at most 1,000 characters.`);
      if (question.time_limit_seconds !== null && question.time_limit_seconds !== '' && (question.time_limit_seconds < 5 || question.time_limit_seconds > 120)) {
        problems.push(`${label}: time limit must be between 5 and 120 seconds.`);
      }
    });
    return problems;
  }

  function payload() {
    const draft = state.draft;
    return {
      title: draft.title.trim(),
      description: draft.description.trim() || null,
      category: categoryKey(draft.category),
      difficulty: draft.difficulty,
      visibility: draft.visibility,
      default_time_limit_seconds: Number(draft.default_time_limit_seconds),
      questions: draft.questions.map(question => ({
        prompt: question.prompt.trim(),
        format: question.format || 'multiple_choice',
        format_payload: question.format_payload,
        options: Array.isArray(question.options) ? question.options.map(option => option.trim()) : null,
        correct_option_index: Number.isInteger(Number(question.correct_option_index)) ? Number(question.correct_option_index) : null,
        explanation: question.explanation.trim() || null,
        // The author response contract requires resolved values; mirror the quiz
        // defaults used by the player projection when an override is blank.
        category: question.format === 'multiple_choice'
          ? question.category.trim() || categoryKey(draft.category)
          : question.category.trim() || null,
        difficulty: question.format === 'multiple_choice' ? question.difficulty || draft.difficulty : question.difficulty || null,
        time_limit_seconds: question.format !== 'multiple_choice'
          ? (question.time_limit_seconds === '' ? null : question.time_limit_seconds)
          : question.time_limit_seconds === '' || question.time_limit_seconds == null
            ? Number(draft.default_time_limit_seconds)
            : Number(question.time_limit_seconds),
        category_id: question.category_id,
        tags: question.tags,
        language: question.language,
        learning_levels: question.learning_levels,
        source_name: question.source_name,
        source_url: question.source_url,
        source_published_at: question.source_published_at,
        verified_at: question.verified_at,
        review_due_at: question.review_due_at,
      })),
      ...(state.generationTicket ? { generation_ticket: state.generationTicket } : {}),
    };
  }

  async function generateQuestions(form) {
    if (state.loading) return;
    if (!state.ownerToken) { state.notice = 'An owner key is required to generate questions.'; render(); return; }
    const values = new FormData(form);
    const method = String(values.get('method') || 'topic');
    const fields = {
      category: String(values.get('category') || ''),
      subject: method === 'document' ? 'Document' : String(values.get('subject') || '').trim(),
      topic: method === 'document' ? (String(values.get('document_topic') || '').trim() || 'Uploaded document') : String(values.get('topic') || '').trim(),
      objectives: String(values.get('objectives') || '').trim(),
      question_count: Number(values.get('question_count') || 5),
      difficulty: String(values.get('difficulty') || 'medium'),
    };
    state.loading = true; state.notice = ''; render();
    try {
      let options;
      let path;
      if (method === 'document') {
        const file = values.get('document');
        if (!(file instanceof File) || !file.size) throw new Error('Choose a PDF, DOCX, or TXT document.');
        const body = new FormData();
        Object.entries(fields).forEach(([key, value]) => body.set(key, String(value)));
        body.set('document', file);
        path = '/api/ai/generations/document';
        options = { method: 'POST', body };
      } else {
        path = '/api/ai/generations';
        options = { method: 'POST', body: JSON.stringify({ method: 'topic', ...fields }) };
      }
      const { response, data } = await request(path, options, true);
      if (!response.ok || !Array.isArray(data?.questions)) {
        const message = data?.error?.message || 'Question generation could not be completed.';
        throw new Error(message);
      }
      const topic = fields.topic || String(values.get('topic') || 'Document quiz');
      const subject = fields.subject || 'Document';
      state.draft = {
        ...newDraft(), title: `${topic} quiz`.slice(0, 100), category: fields.category.replace(/^category:/, ''),
        difficulty: fields.difficulty, description: `Generated from ${subject}. Review every answer before publishing.`,
        questions: data.questions.map(questionDraft), generation_ticket: String(data.generation_ticket || ''),
      };
      state.generationTicket = state.draft.generation_ticket;
      state.page = 'editor'; state.notice = 'Generated questions are ready to review. Check every answer before saving.';
      rememberDraft(); render();
    } catch (error) {
      state.loading = false;
      state.notice = error instanceof Error ? error.message : 'Question generation could not be completed.';
      render();
      return;
    }
    state.loading = false;
    render();
  }

  async function saveDraft() {
    readEditor();
    const problems = validateDraft();
    if (problems.length) {
      state.notice = problems.slice(0, 3).join(' ');
      render();
      return false;
    }
    if (!state.ownerToken) {
      state.notice = 'An owner key is required. Your unsaved edits are still in this tab.';
      render();
      return false;
    }
    state.loading = true;
    state.notice = '';
    render();
    try {
      const creating = !state.draft.id;
      const body = creating ? payload() : { ...payload(), expected_version: state.draft.version };
      const path = creating ? '/api/quizzes' : `/api/quizzes/${encodeURIComponent(state.draft.id)}`;
      const { response, data } = await request(path, {
        method: creating ? 'POST' : 'PATCH',
        body: JSON.stringify(body),
      }, true);
      state.loading = false;
      if (!response.ok || !data) {
        if (response.status === 401) {
          state.ownerRejected = true;
          state.page = 'owner-setup';
        }
        state.notice = errorText(response.status);
        render();
        return false;
      }
      state.draft.id = data.id;
      state.draft.version = data.version;
      state.draft.status = data.status;
      state.draft.questions = data.questions.map(questionDraft);
      state.generationTicket = '';
      state.draft.generation_ticket = '';
      rememberDraft();
      state.notice = creating ? 'Draft created and saved.' : 'Changes saved.';
      render();
      return true;
    } catch {
      state.loading = false;
      state.notice = 'Quiz storage could not be reached. Your unsaved edits remain in this tab.';
      render();
      return false;
    }
  }

  function publicCard(quiz) {
    return `<article class="library-card"><div class="quiz-card-top"><span class="library-category">${escapeHtml(quiz.category)}</span><span class="library-difficulty">${escapeHtml(quiz.difficulty)}</span></div><h3>${escapeHtml(quiz.title)}</h3><p>${escapeHtml(quiz.description || 'A community quiz ready for your next game night.')}</p><div class="library-meta"><span>${quiz.questions.length} questions</span><span>${quiz.default_time_limit_seconds}s default</span></div><div class="library-card-actions"><button class="btn btn-secondary" type="button" data-quiz-action="open-public" data-quiz-id="${escapeHtml(quiz.id)}">Preview quiz</button>${state.ownerToken ? `<button class="btn btn-quiet" type="button" data-quiz-action="duplicate" data-quiz-id="${escapeHtml(quiz.id)}">Duplicate</button>` : ''}</div></article>`;
  }

  function ownedCard(quiz) {
    return `<article class="library-card owned-card"><div class="quiz-card-top"><span class="library-category">${escapeHtml(quiz.category)}</span><span class="quiz-status ${quiz.status === 'published' ? 'published' : ''}">${quiz.status === 'published' ? 'Published' : 'Draft'}</span></div><h3>${escapeHtml(quiz.title)}</h3><p>${escapeHtml(quiz.description || 'No description yet.')}</p><div class="library-meta"><span>${quiz.questions.length} questions</span><span>${escapeHtml(quiz.visibility)} · ${escapeHtml(quiz.difficulty)}</span></div><div class="library-card-actions"><button class="btn btn-primary" type="button" data-quiz-action="edit-quiz" data-quiz-id="${escapeHtml(quiz.id)}">Edit quiz</button><button class="btn btn-quiet" type="button" data-quiz-action="preview-owned" data-quiz-id="${escapeHtml(quiz.id)}">Preview</button></div></article>`;
  }

  function ownerSetup() {
    const rejectedMessage = state.ownerRejected
      ? '<p class="library-alert" role="alert">The saved key was not accepted. It has not been replaced. A new owner key creates a separate, empty library and cannot recover quizzes belonging to the old key.</p>'
      : '<p class="library-alert" role="alert"><strong>Before creating an owner key:</strong> this is a long-lived secret with no password reset or account recovery. If it is lost, its quizzes cannot be recovered through QuizVerse. Save the one-time key somewhere private. Creating a new key will not restore any existing quizzes.</p>';
    return `<section class="owner-setup library-panel"><button class="back-button" type="button" data-quiz-action="back-library">← Back to public quizzes</button><div class="section-kicker">PRIVATE QUIZ LIBRARY</div><h1>${state.ownerRejected ? 'Use an owner key' : 'Your quizzes, your key.'}</h1>${rejectedMessage}<form class="owner-key-form" data-owner-form="restore"><label class="form-label" for="owner-key-entry">${state.ownerRejected ? 'Enter a different owner key' : 'Already have an owner key?'}</label><input class="field" id="owner-key-entry" type="password" autocomplete="off" spellcheck="false" maxlength="128" placeholder="Paste your saved key"><button class="btn btn-secondary" type="submit" ${state.loading ? 'disabled' : ''}>Open my library</button></form><div class="owner-create-block"><label class="acknowledge-row"><input id="owner-key-acknowledged" type="checkbox"><span>I understand the recovery limitation and will save the one-time key.</span></label><button class="btn btn-primary" type="button" data-quiz-action="create-owner" disabled>${state.loading ? 'Creating key…' : 'Create a new owner key'}</button></div>${state.notice ? `<p class="library-message" role="status">${escapeHtml(state.notice)}</p>` : ''}</section>`;
  }

  function ownerKeyScreen() {
    const token = state.pendingOwner?.token || '';
    return `<section class="owner-setup library-panel"><div class="section-kicker">SAVE THIS KEY ONCE</div><h1>Your owner key is ready.</h1><p class="library-alert" role="alert">This key controls every quiz in this owner library. It will not be shown again by the server. Save a private copy now. Losing it means losing access; a new key cannot recover this library.</p><label class="form-label" for="one-time-owner-key">One-time owner key</label><input class="field owner-secret-field" id="one-time-owner-key" type="password" readonly value="${escapeHtml(token)}" autocomplete="off"><div class="owner-key-actions"><button class="btn btn-secondary" type="button" data-quiz-action="reveal-owner-key">Show key</button><button class="btn btn-secondary" type="button" data-quiz-action="copy-owner-key">Copy key</button><button class="btn btn-primary" type="button" data-quiz-action="continue-owner">I saved it — continue</button></div><p class="microcopy">The key stays in this tab only after you continue. It is never put in a URL.</p></section>`;
  }

  function libraryScreen() {
    const rows = state.tab === 'public' ? state.publicQuizzes : state.ownedQuizzes;
    const cards = rows.map(state.tab === 'public' ? publicCard : ownedCard).join('');
    const empty = state.loading
      ? '<div class="library-empty"><span class="loader" aria-hidden="true"></span><p>Opening the quiz library…</p></div>'
      : `<div class="library-empty"><div class="empty-orbit" aria-hidden="true">✦</div><h3>${state.tab === 'public' ? 'No public quizzes found' : 'Your library is ready for its first question'}</h3><p>${state.tab === 'public' ? 'Try a different search or category.' : 'Start with a draft. You can preview it before publishing.'}</p>${state.tab === 'mine' ? '<button class="btn btn-primary" type="button" data-quiz-action="new-quiz">Create your first quiz</button>' : ''}</div>`;
    return `<section class="library-view"><div class="library-heading"><div><div class="section-kicker">QUIZVERSE COLLECTION</div><h1>Find your next<br><span class="gradient-text">great question.</span></h1><p>Browse community quizzes or build a set for your crew.</p></div><button class="btn btn-primary new-quiz-button" type="button" data-quiz-action="new-quiz">Create a quiz</button></div><div class="library-tabs" role="tablist" aria-label="Quiz library"><button class="library-tab ${state.tab === 'public' ? 'active' : ''}" role="tab" aria-selected="${state.tab === 'public'}" type="button" data-quiz-action="tab-public">Explore public</button><button class="library-tab ${state.tab === 'mine' ? 'active' : ''}" role="tab" aria-selected="${state.tab === 'mine'}" type="button" data-quiz-action="tab-mine">My quizzes</button></div>${state.tab === 'public' ? `<form class="library-filters" id="quiz-search-form"><label><span>Search titles</span><input class="field" name="q" maxlength="100" value="${escapeHtml(state.filters.q)}" placeholder="Try ‘space’ or ‘history’"></label><label><span>Category</span><input class="field" name="category" maxlength="50" value="${escapeHtml(state.filters.category)}" placeholder="e.g. science"></label><label><span>Difficulty</span><select class="field" name="difficulty"><option value="">Any difficulty</option>${['easy', 'medium', 'hard'].map(value => `<option value="${value}" ${state.filters.difficulty === value ? 'selected' : ''}>${value[0].toUpperCase()}${value.slice(1)}</option>`).join('')}</select></label><button class="btn btn-secondary" type="submit">Search quizzes</button></form>` : ''}${state.notice ? `<p class="library-message" role="status">${escapeHtml(state.notice)}</p>` : ''}${state.tab === 'mine' && !state.ownerToken ? `<section class="library-empty"><h2>Open your private library</h2><p>Your owner key is separate from your arena reconnect key.</p><button class="btn btn-primary" type="button" data-quiz-action="owner-gate">Use or create an owner key</button></section>` : cards ? `<div class="library-grid">${cards}</div>` : empty}</section>`;
  }

  function renderQuestion(question, index, preview = false) {
    const format = question.format || 'multiple_choice';
    if (format !== 'multiple_choice') {
      if (preview) {
        return `<article class="preview-question"><div class="preview-question-meta"><span>QUESTION ${String(index + 1).padStart(2, '0')}</span><span>${escapeHtml(format.replaceAll('_', ' '))}</span></div><h2>${escapeHtml(question.prompt)}</h2><p class="preview-privacy">This format is saved for future experiences and is not playable in the current preview.</p></article>`;
      }
      return `<article class="question-editor" data-question-index="${index}" data-question-format="${escapeHtml(format)}"><div class="question-editor-head"><div><span class="question-number">QUESTION ${String(index + 1).padStart(2, '0')}</span><span class="microcopy"> · ${escapeHtml(format.replaceAll('_', ' '))}</span></div><div class="question-order-actions"><button class="icon-button" type="button" data-quiz-action="move-question" data-direction="up" data-index="${index}" aria-label="Move question ${index + 1} up" ${index === 0 ? 'disabled' : ''}>↑</button><button class="icon-button" type="button" data-quiz-action="move-question" data-direction="down" data-index="${index}" aria-label="Move question ${index + 1} down" ${index === state.draft.questions.length - 1 ? 'disabled' : ''}>↓</button><button class="icon-button danger-icon" type="button" data-quiz-action="remove-question" data-index="${index}" aria-label="Remove question ${index + 1}">×</button></div></div><p class="field-hint">Structured answer content is preserved for the owner; this format is not playable in current modes.</p><label class="form-label" for="question-prompt-${index}">Question prompt</label><textarea class="field editor-textarea" id="question-prompt-${index}" maxlength="500" data-question-field="prompt" required>${escapeHtml(question.prompt)}</textarea><pre class="field-hint">${escapeHtml(JSON.stringify(question.format_payload, null, 2))}</pre><label><span>Category (optional)</span><input class="field" maxlength="50" data-question-field="category" value="${escapeHtml(question.category)}"></label><label><span>Difficulty</span><select class="field" data-question-field="difficulty"><option value="">Use quiz difficulty</option>${['easy', 'medium', 'hard'].map(value => `<option value="${value}" ${question.difficulty === value ? 'selected' : ''}>${value[0].toUpperCase()}${value.slice(1)}</option>`).join('')}</select></label><label><span>Time limit (optional)</span><input class="field" type="number" min="5" max="120" data-question-field="time_limit_seconds" value="${escapeHtml(question.time_limit_seconds)}" placeholder="Default"></label><label class="form-label" for="question-explanation-${index}">Explanation (optional)</label><textarea class="field editor-textarea explanation-input" id="question-explanation-${index}" maxlength="1000" data-question-field="explanation">${escapeHtml(question.explanation)}</textarea></article>`;
    }
    if (preview) {
      return `<article class="preview-question"><div class="preview-question-meta"><span>QUESTION ${String(index + 1).padStart(2, '0')}</span><span>${escapeHtml(question.category || state.draft?.category || 'General')}</span></div><h2>${escapeHtml(question.prompt)}</h2><div class="preview-options">${question.options.map((option, optionIndex) => `<div class="preview-option"><span>${String.fromCharCode(65 + optionIndex)}</span>${escapeHtml(option)}</div>`).join('')}</div><p class="preview-time">${Number(question.time_limit_seconds || state.draft?.default_time_limit_seconds || 15)} seconds to answer</p></article>`;
    }
    const questionDifficulty = question.difficulty || '';
    return `<article class="question-editor" data-question-index="${index}"><div class="question-editor-head"><div><span class="question-number">QUESTION ${String(index + 1).padStart(2, '0')}</span><span class="microcopy"> · move with buttons</span></div><div class="question-order-actions"><button class="icon-button" type="button" data-quiz-action="move-question" data-direction="up" data-index="${index}" aria-label="Move question ${index + 1} up" ${index === 0 ? 'disabled' : ''}>↑</button><button class="icon-button" type="button" data-quiz-action="move-question" data-direction="down" data-index="${index}" aria-label="Move question ${index + 1} down" ${index === state.draft.questions.length - 1 ? 'disabled' : ''}>↓</button><button class="icon-button danger-icon" type="button" data-quiz-action="remove-question" data-index="${index}" aria-label="Remove question ${index + 1}">×</button></div></div><label class="form-label" for="question-prompt-${index}">Question prompt</label><textarea class="field editor-textarea" id="question-prompt-${index}" maxlength="500" data-question-field="prompt" required>${escapeHtml(question.prompt)}</textarea><div class="options-editor">${question.options.map((option, optionIndex) => `<label class="option-input"><span>${String.fromCharCode(65 + optionIndex)}</span><input class="field" maxlength="200" aria-label="Question ${index + 1} option ${String.fromCharCode(65 + optionIndex)}" data-option-index="${optionIndex}" value="${escapeHtml(option)}" required></label>`).join('')}</div><div class="question-settings"><label><span>Correct answer</span><select class="field" data-question-field="correct_option_index">${question.options.map((option, optionIndex) => `<option value="${optionIndex}" ${Number(question.correct_option_index) === optionIndex ? 'selected' : ''}>${String.fromCharCode(65 + optionIndex)}${option.trim() ? ` · ${escapeHtml(option.slice(0, 42))}` : ''}</option>`).join('')}</select></label><label><span>Category (optional)</span><input class="field" maxlength="50" data-question-field="category" value="${escapeHtml(question.category)}" placeholder="Use quiz category"></label><label><span>Difficulty</span><select class="field" data-question-field="difficulty"><option value="">Use quiz difficulty</option>${['easy', 'medium', 'hard'].map(value => `<option value="${value}" ${questionDifficulty === value ? 'selected' : ''}>${value[0].toUpperCase()}${value.slice(1)}</option>`).join('')}</select></label><label><span>Time limit (optional)</span><input class="field" type="number" min="5" max="120" data-question-field="time_limit_seconds" value="${escapeHtml(question.time_limit_seconds)}" placeholder="Default"></label></div><label class="form-label" for="question-explanation-${index}">Explanation (optional)</label><textarea class="field editor-textarea explanation-input" id="question-explanation-${index}" maxlength="1000" data-question-field="explanation" placeholder="Shown in the authoring view, not in player preview">${escapeHtml(question.explanation)}</textarea></article>`;
  }

  function editorScreen() {
    const draft = state.draft || newDraft();
    const generator = `<section class="ai-generator-panel" aria-labelledby="ai-generator-title"><div class="ai-generator-heading"><div><div class="section-kicker">AI ASSISTED</div><h2 id="ai-generator-title">Build a first draft</h2><p>Review each answer. Generated quizzes are never published automatically.</p></div><span class="ai-status" role="status">${state.aiAvailable ? 'READY' : state.aiStatusLoaded ? 'NOT CONFIGURED' : 'CHECKING'}</span></div>${!state.aiAvailable ? `<p class="field-hint">${state.aiStatusLoaded ? 'AI generation is not configured on this server. Ask the administrator to configure Gemini access.' : 'Checking AI availability…'}</p>` : `<form id="ai-generator-form"><label><span>Generate from</span><select class="field" name="method" id="ai-method"><option value="topic">A topic</option><option value="document">A document</option></select></label><label><span>Official category</span><select class="field" name="category" required ${state.taxonomyCategories.length ? '' : 'disabled'}><option value="">Choose an official category</option>${state.taxonomyCategories.map(category => `<option value="${escapeHtml(category.id)}">${escapeHtml(category.displayName)}</option>`).join('')}</select>${state.taxonomyCategoriesLoaded && !state.taxonomyCategories.length ? '<span class="field-hint">Categories could not be loaded. Refresh and try again.</span>' : ''}</label><div class="ai-topic-fields"><div class="metadata-grid"><label><span>Subject</span><input class="field" name="subject" maxlength="100" required placeholder="Computer Science"></label><label><span>Topic</span><input class="field" name="topic" maxlength="200" required placeholder="Operating systems"></label></div><label><span>Learning objectives (optional)</span><textarea class="field editor-textarea" name="objectives" maxlength="1000" placeholder="What should learners understand?"></textarea></label></div><div class="ai-document-fields" hidden><label><span>Document (PDF, DOCX, or TXT; up to 10 MB)</span><input class="field" type="file" name="document" accept=".pdf,.docx,.txt,application/pdf,text/plain,application/vnd.openxmlformats-officedocument.wordprocessingml.document"></label><p class="field-hint">Document content is sent to Gemini to generate questions. Google may use free-tier content to improve its products; do not upload sensitive material. Scanned PDFs are not supported.</p><label><span>Quiz topic</span><input class="field" name="document_topic" maxlength="200" placeholder="Topic from this document"></label></div><div class="ai-settings"><label><span>Questions</span><input class="field" type="number" name="question_count" min="1" max="20" value="5" required></label><label><span>Difficulty</span><select class="field" name="difficulty"><option value="easy">Easy</option><option value="medium" selected>Medium</option><option value="hard">Hard</option></select></label><button class="btn btn-secondary" type="submit" ${state.loading ? 'disabled' : ''}>${state.loading ? 'Generating…' : 'Generate questions'}</button></div></form>`}</section>`;
    return `<section class="creator-view"><div class="creator-toolbar"><button class="back-button" type="button" data-quiz-action="back-library">← Back to library</button><span class="quiz-status ${draft.status === 'published' ? 'published' : ''}">${draft.status === 'published' ? 'Published' : draft.id ? 'Draft' : 'Unsaved draft'}</span></div><div class="creator-heading"><div class="section-kicker">QUIZ CREATOR</div><h1>${draft.id ? 'Shape your quiz.' : 'Start with a question.'}</h1><p>Save a draft any time. Preview it like a player before publishing.</p></div>${generator}<form id="quiz-editor-form" novalidate><section class="editor-section"><h2>Quiz details</h2><div class="metadata-grid"><label><span>Title</span><input class="field" maxlength="100" required data-quiz-field="title" value="${escapeHtml(draft.title)}" placeholder="The great space race"></label><label><span>Category</span><input class="field" maxlength="50" required data-quiz-field="category" value="${escapeHtml(draft.category)}" placeholder="science"></label><label><span>Difficulty</span><select class="field" data-quiz-field="difficulty">${['easy', 'medium', 'hard'].map(value => `<option value="${value}" ${draft.difficulty === value ? 'selected' : ''}>${value[0].toUpperCase()}${value.slice(1)}</option>`).join('')}</select></label><label><span>Default answer time</span><input class="field" type="number" min="5" max="120" required data-quiz-field="default_time_limit_seconds" value="${escapeHtml(draft.default_time_limit_seconds)}"></label><label><span>Visibility</span><select class="field" data-quiz-field="visibility"><option value="private" ${draft.visibility === 'private' ? 'selected' : ''}>Private</option><option value="public" ${draft.visibility === 'public' ? 'selected' : ''}>Public</option></select></label><label class="description-field"><span>Description</span><textarea class="field editor-textarea" maxlength="500" data-quiz-field="description" placeholder="What makes this quiz fun?">${escapeHtml(draft.description)}</textarea></label></div><p class="field-hint">Public quizzes appear in Explore. Private quizzes stay in your library unless you create a share grant through the API.</p></section><section class="editor-section question-section"><div class="section-title-row"><div><h2>Questions <span class="question-count">${draft.questions.length}</span></h2><p>Four distinct choices per question. Reorder with the arrow buttons.</p></div><button class="btn btn-secondary" type="button" data-quiz-action="add-question" ${draft.questions.length >= 200 ? 'disabled' : ''}>＋ Add question</button></div>${draft.questions.length ? draft.questions.map((question, index) => renderQuestion(question, index)).join('') : '<div class="empty-question"><h3>Your first question starts here</h3><p>Add a prompt and four answer options. Drafts can be saved before they are complete.</p></div>'}</section>${state.notice ? `<p class="library-message" role="alert">${escapeHtml(state.notice)}</p>` : ''}<div class="editor-actions"><button class="btn btn-quiet" type="button" data-quiz-action="preview-editor">Preview</button><button class="btn btn-primary" type="button" data-quiz-action="save-quiz" ${state.loading ? 'disabled' : ''}>${state.loading ? 'Saving…' : 'Save draft'}</button>${draft.id ? `<button class="btn btn-secondary" type="button" data-quiz-action="${draft.status === 'published' ? 'unpublish' : 'publish'}" ${state.loading ? 'disabled' : ''}>${draft.status === 'published' ? 'Unpublish' : 'Publish quiz'}</button><button class="btn btn-danger" type="button" data-quiz-action="delete-quiz">Delete quiz</button>` : ''}</div></form></section>`;
  }

  function previewScreen() {
    const draft = state.draft;
    if (!draft) return libraryScreen();
    return `<section class="preview-view"><div class="creator-toolbar"><button class="back-button" type="button" data-quiz-action="${state.previewReturn === 'editor' ? 'return-editor' : 'back-library'}">← ${state.previewReturn === 'editor' ? 'Back to editing' : 'Back to library'}</button><span class="quiz-status">PLAYER PREVIEW</span></div><div class="preview-heading"><div class="section-kicker">${escapeHtml(draft.category || 'QUIZ PREVIEW')} · ${escapeHtml(draft.difficulty)}</div><h1>${escapeHtml(draft.title || 'Untitled quiz')}</h1><p>${escapeHtml(draft.description || 'No description yet.')}</p><p class="preview-privacy">This preview hides the answer key and explanations.</p></div>${draft.questions.length ? draft.questions.map((question, index) => renderQuestion(question, index, true)).join('') : '<div class="library-empty"><h3>No questions to preview yet</h3><p>Add questions in the creator, then preview again.</p></div>'}</section>`;
  }

  function render() {
    if (!state.root) return;
    if (state.page === 'owner-setup') state.root.innerHTML = ownerSetup();
    else if (state.page === 'owner-key') state.root.innerHTML = ownerKeyScreen();
    else if (state.page === 'editor') state.root.innerHTML = editorScreen();
    else if (state.page === 'preview') state.root.innerHTML = previewScreen();
    else state.root.innerHTML = libraryScreen();
  }

  async function duplicateQuiz(id) {
    if (!state.ownerToken) {
      state.pendingDuplicate = id;
      ownerGate();
      return;
    }
    try {
      const { response, data } = await request(`/api/quizzes/${encodeURIComponent(id)}/duplicate`, { method: 'POST' }, true);
      if (!response.ok || !data) {
        showError(response.status);
        return;
      }
      state.draft = {
        id: data.id,
        version: data.version,
        title: data.title,
        description: data.description || '',
        category: data.category,
        difficulty: data.difficulty,
        visibility: data.visibility,
        default_time_limit_seconds: data.default_time_limit_seconds,
        status: data.status,
        questions: data.questions.map(questionDraft),
      };
      state.page = 'editor';
      state.notice = 'Public quiz duplicated as a private draft with source attribution.';
      rememberDraft();
      render();
    } catch {
      state.notice = 'The quiz could not be duplicated. Try again when storage is available.';
      render();
    }
  }

  async function openPublicQuiz(id) {
    state.loading = true;
    render();
    try {
      // Deliberately omit owner credentials: this route returns the player-safe projection.
      const { response, data } = await request(`/api/quizzes/${encodeURIComponent(id)}`);
      state.loading = false;
      if (!response.ok || !data) {
        showError(response.status);
        return;
      }
      state.draft = {
        id: data.id,
        title: data.title,
        description: data.description || '',
        category: data.category,
        difficulty: data.difficulty,
        default_time_limit_seconds: data.default_time_limit_seconds,
        visibility: 'public',
        questions: data.questions.map(questionDraft),
      };
      state.previewReturn = 'library';
      state.page = 'preview';
      state.notice = '';
      render();
    } catch {
      state.loading = false;
      state.notice = 'This public quiz could not be opened right now.';
      render();
    }
  }

  function setupHandlers(root) {
    root.addEventListener('click', async event => {
      const button = event.target.closest('[data-quiz-action]');
      if (!button || !root.contains(button)) return;
      const action = button.dataset.quizAction;
      if (state.page === 'editor') readEditor();

      if (action === 'tab-public') {
        state.tab = 'public'; state.page = 'library'; state.notice = ''; await loadPublic();
      } else if (action === 'tab-mine') {
        state.tab = 'mine'; state.page = 'library'; state.notice = '';
        if (state.ownerToken) await loadOwned(); else render();
      } else if (action === 'owner-gate' || action === 'new-quiz') {
        if (state.ownerToken) beginNewQuiz(); else ownerGate();
      } else if (action === 'create-owner') {
        const acknowledged = root.querySelector('#owner-key-acknowledged')?.checked;
        if (!acknowledged) {
          state.notice = 'Confirm that you understand the recovery limitation before creating a key.';
          render();
        } else createOwner();
      } else if (action === 'continue-owner') {
        continueWithNewOwner();
        if (state.pendingDuplicate) {
          const id = state.pendingDuplicate; state.pendingDuplicate = null; await duplicateQuiz(id);
        }
      } else if (action === 'back-library') {
        state.page = 'library'; state.notice = '';
        if (state.draft) rememberDraft();
        render();
      } else if (action === 'edit-quiz') {
        await editQuiz(button.dataset.quizId);
      } else if (action === 'preview-owned') {
        await editQuiz(button.dataset.quizId);
        if (state.page === 'editor') { readEditor(); state.previewReturn = 'library'; state.page = 'preview'; render(); }
      } else if (action === 'open-public') {
        await openPublicQuiz(button.dataset.quizId);
      } else if (action === 'add-question') {
        if (state.draft.questions.length < 200) state.draft.questions.push(questionDraft());
        rememberDraft(); render();
      } else if (action === 'move-question' || action === 'remove-question') {
        const index = Number(button.dataset.index);
        if (action === 'remove-question') state.draft.questions.splice(index, 1);
        else {
          const direction = button.dataset.direction === 'up' ? -1 : 1;
          const next = index + direction;
          if (next >= 0 && next < state.draft.questions.length) {
            [state.draft.questions[index], state.draft.questions[next]] = [state.draft.questions[next], state.draft.questions[index]];
          }
        }
        rememberDraft(); render();
      } else if (action === 'preview-editor') {
        readEditor(); state.previewReturn = 'editor'; state.page = 'preview'; render();
      } else if (action === 'return-editor') {
        state.page = 'editor'; render();
      } else if (action === 'save-quiz') {
        await saveDraft();
      } else if (action === 'publish' || action === 'unpublish') {
        if (!await saveDraft()) return;
        const path = `/api/quizzes/${encodeURIComponent(state.draft.id)}/${action}`;
        try {
          const { response, data } = await request(path, { method: 'POST' }, true);
          if (!response.ok || !data) { showError(response.status); return; }
          state.draft.version = data.version;
          state.draft.status = data.status;
          state.notice = data.status === 'published' ? 'Quiz published.' : 'Quiz unpublished. It no longer appears in Explore.';
          rememberDraft();
          if (state.ownerToken) {
            const listing = await request('/api/quizzes/mine', {}, true);
            if (listing.response.ok) state.ownedQuizzes = listing.data.quizzes;
          }
          render();
        } catch { state.notice = 'The publication change could not be completed.'; render(); }
      } else if (action === 'duplicate') {
        await duplicateQuiz(button.dataset.quizId);
      } else if (action === 'delete-quiz') {
        if (!state.draft.id) return;
        if (!window.confirm(`Delete “${state.draft.title}”? This removes it from your library and revokes its share grants.`)) return;
        try {
          const { response } = await request(`/api/quizzes/${encodeURIComponent(state.draft.id)}`, { method: 'DELETE' }, true);
          if (!response.ok) { showError(response.status); return; }
          sessionStorage.removeItem(UNSAVED_KEY);
          state.draft = null; state.page = 'library'; state.tab = 'mine';
          await loadOwned();
          state.notice = 'Quiz deleted.';
          render();
        } catch { state.notice = 'The quiz could not be deleted. Try again.'; render(); }
      } else if (action === 'copy-owner-key') {
        const token = state.pendingOwner?.token;
        if (!token) return;
        try { await navigator.clipboard.writeText(token); state.notice = 'Owner key copied. Keep it somewhere private.'; }
        catch { state.notice = 'Clipboard access is unavailable. Select and copy the key field manually.'; }
        render();
      } else if (action === 'reveal-owner-key') {
        const field = root.querySelector('#one-time-owner-key');
        if (field) { field.type = field.type === 'password' ? 'text' : 'password'; button.textContent = field.type === 'text' ? 'Hide key' : 'Show key'; }
      }
    });

    root.addEventListener('submit', async event => {
      if (event.target.id === 'ai-generator-form') {
        event.preventDefault(); await generateQuestions(event.target); return;
      }
      if (event.target.id === 'owner-key-entry' || event.target.matches('[data-owner-form="restore"]')) {
        event.preventDefault(); await restoreOwnerKey(); return;
      }
      if (event.target.id === 'quiz-search-form') {
        event.preventDefault();
        const form = new FormData(event.target);
        state.filters = {
          q: String(form.get('q') || ''),
          category: String(form.get('category') || ''),
          difficulty: String(form.get('difficulty') || ''),
        };
        await loadPublic();
        return;
      }
      if (event.target.id === 'quiz-editor-form') {
        event.preventDefault();
        await saveDraft();
      }
    });

    root.addEventListener('input', event => {
      if (state.page === 'editor' && event.target.closest('#quiz-editor-form')) readEditor();
    });
    root.addEventListener('change', event => {
      if (event.target.id === 'ai-method') {
        const documentMode = event.target.value === 'document';
        const topicFields = root.querySelector('.ai-topic-fields');
        const documentFields = root.querySelector('.ai-document-fields');
        if (topicFields) topicFields.hidden = documentMode;
        if (documentFields) documentFields.hidden = !documentMode;
        root.querySelectorAll('.ai-topic-fields input').forEach(field => { field.required = !documentMode && field.name !== 'objectives'; });
      }
      if (event.target.id === 'owner-key-acknowledged') {
        const button = root.querySelector('[data-quiz-action="create-owner"]');
        if (button) button.disabled = !event.target.checked || state.loading;
      }
      if (state.page === 'editor' && event.target.closest('#quiz-editor-form')) readEditor();
    });
  }

  function flattenCategoryTree(nodes) {
    return (Array.isArray(nodes) ? nodes : []).flatMap(node => [
      { id: node.id, slug: node.slug, displayName: node.display_name },
      ...flattenCategoryTree(node.children),
    ]);
  }

  function loadTaxonomyCategories() {
    if (state.taxonomyCategoriesLoaded || state.taxonomyCategoriesLoading) return;
    state.taxonomyCategoriesLoading = true;
    request('/api/categories').then(({ response, data }) => {
      state.taxonomyCategoriesLoading = false;
      state.taxonomyCategoriesLoaded = true;
      if (response.ok && Array.isArray(data)) state.taxonomyCategories = flattenCategoryTree(data);
      render();
    }).catch(() => {
      state.taxonomyCategoriesLoading = false;
      state.taxonomyCategoriesLoaded = true;
      state.taxonomyCategories = [];
      render();
    });
  }

  function mount(root, notify) {
    // Main navigation enters the library landing page; the current editor draft
    // remains persisted in tab storage and can be reopened from the owner's list.
    state.page = 'library';
    if (state.root !== root) {
      state.root = root;
      state.notify = notify;
      setupHandlers(root);
    }
    render();
    loadTaxonomyCategories();
    if (!state.aiStatusLoaded) {
      request('/api/ai/status').then(({ response, data }) => {
        state.aiAvailable = response.ok && data?.enabled === true;
        state.aiStatusLoaded = true;
        render();
      }).catch(() => { state.aiAvailable = false; state.aiStatusLoaded = true; render(); });
    }
    if (state.page === 'library' && state.tab === 'public' && !state.publicQuizzes.length && !state.loading) loadPublic();
    if (state.page === 'library' && state.tab === 'mine' && state.ownerToken && !state.ownedQuizzes.length && !state.loading) loadOwned();
  }

  window.QuizLibrary = { mount };
})();
