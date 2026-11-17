(() => {
  'use strict';

  const app = document.querySelector('#app');
  const toast = document.querySelector('#toast');
  const connectionStatus = document.querySelector('#connection-status');
  const PREFERENCE_KEY = 'qv-player-preferences';
  const PLAYER_CREDENTIAL_KEY = 'qv-player-credential';
  const DEFAULT_PREFERENCES = { learning_preset: 'teen_challenge', difficulty: 'medium', language: 'en', topics: {} };
  function readPreferences() {
    try {
      const raw = JSON.parse(localStorage.getItem(PREFERENCE_KEY) || 'null');
      const presets = ['little_explorers', 'young_learners', 'junior_champions', 'teen_challenge', 'college_arena', 'expert_arena'];
      const difficulties = ['easy', 'medium', 'hard', 'mixed'];
      const weights = ['increase', 'normal', 'decrease', 'exclude'];
      if (!raw || typeof raw !== 'object' || !presets.includes(raw.learning_preset) || !difficulties.includes(raw.difficulty) || !/^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$/i.test(raw.language || 'en') || !raw.topics || typeof raw.topics !== 'object' || Array.isArray(raw.topics) || Object.keys(raw.topics).length > 49) return { ...DEFAULT_PREFERENCES, topics: {} };
      const topics = Object.fromEntries(Object.entries(raw.topics).filter(([key, value]) => typeof key === 'string' && key.length <= 128 && weights.includes(value)));
      return { learning_preset: raw.learning_preset, difficulty: raw.difficulty, language: raw.language || 'en', topics };
    } catch { return { ...DEFAULT_PREFERENCES, topics: {} }; }
  }
  const state = {
    socket: null,
    connected: false,
    room: null,
    playerId: null,
    token: sessionStorage.getItem('qv-token') || '',
    name: sessionStorage.getItem('qv-name') || '',
    screen: 'home',
    view: 'play',
    selectedMode: 'classic',
    preferences: readPreferences(),
    categories: [],
    publicQuizzes: [],
    ownedQuizzes: [],
    examCollections: [],
    soloReplayPending: false,
    reconnectDelay: 800,
    reconnectTimer: null,
    toastTimer: null,
    tick: null,
    profileCredential: sessionStorage.getItem(PLAYER_CREDENTIAL_KEY) || '',
    profile: null,
    profileStats: null,
    profileMatches: [],
    profileHistoryTotal: 0,
    profileLoading: false,
    profileNotice: '',
    profilePage: 0,
    oneTimeCredential: '',
    rankingsScope: 'global',
    rankingsMode: 'classic',
    rankingsData: null,
    rankingsLoading: false,
    rankingsError: '',
    rankingsPage: 0,
    rankingsRequestId: 0,
    myRatings: null,
    myRatingsLoading: false,
    ratingHistory: [],
    ratingHistoryTotal: 0,
    ratingHistoryPage: 0,
    ratingHistoryScope: '',
    ratingHistoryLoading: false,
    ratingHistoryError: '',
    tournamentsList: [],
    tournamentsTotal: 0,
    tournamentsLoading: false,
    tournamentsError: '',
    tournamentsFilterStatus: '',
    tournamentsFilterMode: '',
    selectedTournamentId: null,
    selectedTournamentDetail: null,
    selectedTournamentBracket: null,
    selectedTournamentAudit: [],
    tournamentLoadingDetail: false,
    showTournamentCreateModal: false,
    leaguesList: [],
    leaguesTotal: 0,
    leaguesLoading: false,
    leaguesError: '',
    leaguesFilterStatus: '',
    leaguesFilterMode: '',
    selectedLeagueId: null,
    selectedLeagueDetail: null,
    selectedSeasonId: null,
    selectedSeasonDetail: null,
    selectedSeasonAudit: [],
    leagueLoadingDetail: false,
    showLeagueCreateModal: false,
    showSeasonCreateModal: false,
    careerAdvancedStats: null,
    careerHonors: null,
    h2hData: null,
    h2hLoading: false,
    h2hModalOpen: false,
    h2hOpponentId: '',
    h2hOpponentName: '',
    h2hError: '',
    socialTab: 'overview',
    friendsList: [],
    incomingRequests: [],
    outgoingRequests: [],
    incomingInvitations: [],
    outgoingInvitations: [],
    incomingRematches: [],
    outgoingRematches: [],
    blockedList: [],
    playerSearchResults: [],
    searchQuery: '',
    socialLoading: false,
    socialNotice: '',
    selectedPublicProfile: null,
    publicProfileLoading: false,
    friendPresence: {},
    presenceWs: null,
    presenceHeartbeatTimer: null,
    presenceReconnectTimer: null,
    presenceReconnectDelay: 1000,
    conversations: [],
    activeConversationId: null,
    activeMessages: [],
    messagesLoading: false,
    typingFriends: {},
    typingTimer: null,
    editingMessageId: null,
    reportingMessageId: null,
    reportingReason: 'inappropriate',
    reportingDetails: '',
    messageSearchQuery: '',
    roomChatOpen: false,
    roomChatUnread: 0,
    notifCenterOpen: false,
    notifUnreadCount: 0,
    notifications: [],
    notifTab: 'all',
    notifUnreadOnly: false,
    notifLoading: false,
    notifPreferences: null,
    notifPreferencesOpen: false,
    socialOverview: null,
    intelligenceTab: 'overview',
    intelligenceOverview: null,
    intelligencePerformance: null,
    intelligenceTimeWindow: 'recent',
    intelligenceWeaknesses: null,
    intelligenceDifficulty: null,
    intelligenceLearningPath: null,
    intelligenceRecommendations: [],
    intelligenceReports: [],
    intelligenceSelectedReport: null,
    intelligenceQuestionAnalysis: null,
    intelligenceLoading: false,
    intelligenceError: '',
    communityTab: 'discover',
    communityOverview: null,
    communitySearchResults: null,
    communityCreators: [],
    communityMyQuizzes: [],
    communityMyProfile: null,
    communityFavorites: [],
    communityCollections: [],
    communityActiveCollection: null,
    communityActiveQuiz: null,
    communityActiveReviews: [],
    communityModerationReports: [],
    communityModerationAudit: [],
    communitySearchQuery: '',
    communityFilterCategory: '',
    communityFilterDifficulty: '',
    communitySortBy: 'recent',
    communityLoading: false,
    communityError: '',
    communityIsModerator: false,
    communityActiveReportTarget: null,
    communityAnalyticsModal: null,
    communityEditingProfile: false,
    communityAuthoringQuiz: false,
    communityCreatingCollection: false,
  };
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
  const initials = name => name.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join('').toUpperCase();
  const formatMode = mode => String(mode || '').split('_').map(s => s ? s[0].toUpperCase() + s.slice(1) : '').join(' ');
  const timeAgo = dateStr => {
    if (!dateStr) return '';
    const diff = Math.max(0, Date.now() - new Date(dateStr).getTime());
    const secs = Math.floor(diff / 1000);
    if (secs < 60) return 'Just now';
    const mins = Math.floor(secs / 60);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    return `${days}d ago`;
  };
  const send = (event, data = {}) => {
    if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
      notify('Reconnecting to the arena. Try again in a moment.');
      return false;
    }
    state.socket.send(JSON.stringify({ event, ...data }));
    return true;
  };
  function notify(message) {
    toast.textContent = message;
    toast.classList.add('show');
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => toast.classList.remove('show'), 3200);
  }
  function setConnection(connected) {
    state.connected = connected;
    connectionStatus.classList.toggle('online', connected);
    connectionStatus.classList.toggle('offline', !connected);
    connectionStatus.querySelector('span:last-child').textContent = connected ? 'ARENA LINK LIVE' : 'RECONNECTING';
  }
  function connect() {
    if (state.socket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(state.socket.readyState)) return;
    const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${scheme}//${location.host}/ws`);
    state.socket = socket;
    socket.addEventListener('open', () => {
      setConnection(true);
      state.reconnectDelay = 800;
      if (state.token) send('reconnect', { token: state.token });
      else render();
    });
    socket.addEventListener('message', ({ data }) => {
      let message;
      try { message = JSON.parse(data); } catch { return; }
      if (message.event === 'error') {
        notify(message.message || 'That action could not be completed.');
        if (['invalid_session', 'session_expired'].includes(message.code)) {
          sessionStorage.removeItem('qv-token');
          state.token = '';
          state.room = null;
          state.playerId = null;
          state.screen = 'home';
          render();
        }
        return;
      }
      if (message.event === 'joined') {
        state.token = message.token;
        state.playerId = message.player_id;
        state.room = message.state;
        state.clockOffsetMs = Number(state.room.server_now || Date.now()) - Date.now();
        sessionStorage.setItem('qv-token', state.token);
        sessionStorage.setItem('qv-name', state.name || state.room.players.find(player => player.id === state.playerId)?.name || '');
        render();
        return;
      }
      if (message.event === 'room_chat_message') {
        if (state.room) {
          state.room.chat_messages = state.room.chat_messages || [];
          if (!state.room.chat_messages.some(m => m.id === message.id)) {
            state.room.chat_messages.push(message);
          }
          if (!state.roomChatOpen) {
            state.roomChatUnread = (state.roomChatUnread || 0) + 1;
          }
          render();
          const chatBox = document.querySelector('#room-chat-messages-container');
          if (chatBox) chatBox.scrollTop = chatBox.scrollHeight;
        }
        return;
      }
      if (message.event === 'room_chat_reaction_updated') {
        if (state.room && state.room.chat_messages) {
          const m = state.room.chat_messages.find(item => item.id === message.message_id);
          if (m) {
            m.reactions = message.reactions;
            render();
          }
        }
        return;
      }
      if (message.state) {
        state.room = message.state;
        state.clockOffsetMs = Number(state.room.server_now || Date.now()) - Date.now();
        render();
        if (state.soloReplayPending && state.room.mode === 'solo_practice' && state.room.phase === 'waiting' && state.room.host_id === state.playerId) {
          state.soloReplayPending = false;
          send('start');
        }
      }
    });
    socket.addEventListener('close', () => {
      setConnection(false);
      if (state.tick) clearInterval(state.tick);
      const waiting = state.screen !== 'home';
      if (waiting) {
        notify('Arena link interrupted. Rejoining…');
        render();
        clearTimeout(state.reconnectTimer);
        state.reconnectTimer = setTimeout(connect, state.reconnectDelay);
        state.reconnectDelay = Math.min(8000, Math.round(state.reconnectDelay * 1.7));
      }
    });
    socket.addEventListener('error', () => socket.close());
  }
  function ranking() {
    return [...(state.room?.players || [])].sort((a, b) => b.score - a.score || b.correct - a.correct || a.name.localeCompare(b.name));
  }
  function leaderboard() {
    return `<aside class="leaderboard-card" aria-label="Live leaderboard"><div class="leaderboard-head"><h2 class="leaderboard-title">Live leaderboard</h2><span>UPDATING LIVE</span></div>${ranking().map((player, index) => `<div class="leader-row"><span class="rank ${index < 3 ? ['gold', 'silver', 'bronze'][index] : ''}">${index + 1}</span><span class="leader-name">${escapeHtml(player.name)}${player.id === state.playerId ? ' <span style="color:#a99aff">(you)</span>' : ''}</span><span class="leader-score">${player.score.toLocaleString()}<span class="sr-only"> points</span></span></div>`).join('')}</aside>`;
  }
  function playerRow(player) {
    return `<div class="player-row"><div class="avatar" aria-hidden="true">${escapeHtml(initials(player.name))}</div><div class="player-info"><div class="player-name">${escapeHtml(player.name)}${player.id === state.playerId ? ' <span style="color:#a99aff">(you)</span>' : ''}</div><div class="player-state"><span class="mini-dot ${player.connected ? '' : 'offline'}"></span>${player.connected ? 'In the arena' : 'Reconnecting'}</div></div>${player.is_host ? '<span class="host-badge">HOST</span>' : ''}</div>`;
  }
  function home() {
    return `<section class="home-layout"><div class="hero-copy"><div class="eyebrow">A little knowledge. A lot of rivalry.</div><h1 class="hero-title">Bring your<br><span class="gradient-text">brain trust.</span></h1><p class="hero-copy">Ten quick-fire questions. Your favorite people. One gloriously debatable winner. Your next great game night starts here.</p><div class="format-row"><span class="format-pill"><b>02—08</b> players</span><span class="format-pill"><b>10</b> rounds</span><span class="format-pill"><b>15s</b> to answer</span></div><div class="hero-art" aria-hidden="true"><div class="orbit"><span class="orbit-point one"></span><span class="orbit-point two"></span></div><div class="core-orb"></div><div class="art-label"><strong>QUICK THINKERS</strong>Fast questions, big reactions</div><div class="art-label bottom"><strong>YOUR CREW, YOUR ARENA</strong>Just share a six-character code</div></div></div><section class="play-card" aria-labelledby="play-title"><div class="play-card-head"><div><div class="section-kicker">STEP INTO THE VERSE</div><h2 id="play-title">Ready when you are.</h2><p class="subcopy">Pick a name. Create an arena or join your crew.</p></div><span class="card-spark" aria-hidden="true">✦</span></div><label class="form-label" for="player-name">Your nickname</label><input class="field" id="player-name" name="nickname" maxlength="18" autocomplete="nickname" placeholder="What should we call you?" value="${escapeHtml(state.name)}"><div class="error-text" id="name-error"></div><div class="action-grid"><button class="btn btn-primary" type="button" data-action="create">✦ &nbsp;Create Arena</button><button class="btn btn-secondary" type="button" data-action="join">Join with Code &nbsp;↗</button></div><div id="join-controls" hidden><label class="form-label" for="room-code">Six-character arena code</label><input class="field code-field" id="room-code" maxlength="6" autocomplete="off" placeholder="ABC234" aria-label="Six-character arena code"><button class="btn btn-primary btn-full" type="button" data-action="confirm-join">Join the arena&nbsp; →</button></div><p class="microcopy">No account. No downloads. Just a code and a little friendly rivalry.</p></section></section>`;
  }
  function installModeSelector() {
    const actions = app.querySelector('.action-grid');
    if (!actions) return;
    const picker = document.createElement('div');
    picker.className = 'mode-picker';
    const presetOptions = [['little_explorers','Little Explorers · ages 5–7'],['young_learners','Young Learners · ages 8–10'],['junior_champions','Junior Champions · ages 11–13'],['teen_challenge','Teen Challenge · ages 14–17'],['college_arena','College Arena · ages 18–23'],['expert_arena','Expert Arena · adults / advanced']];
    const diffOptions = ['easy','medium','hard','mixed'];
    picker.innerHTML = `<label class="form-label" for="game-mode">Choose your arena format</label><select class="field" id="game-mode" name="game-mode"><option value="classic">Classic · 15 seconds · Momentum Shift</option><option value="speed_challenge">Speed Challenge · 10 seconds · no Momentum Shift</option></select><p class="microcopy mode-description" id="mode-description">Classic: 1,000 points plus up to 500 speed points. Momentum Shift is active.</p><details class="match-settings"><summary>Learning preferences <span>(saved on this device)</span></summary><p class="microcopy">Content suitability only. This does not verify anyone’s age.</p><label>Learning preset<select class="field" id="player-preset">${presetOptions.map(([v,l])=>`<option value="${v}">${l}</option>`).join('')}</select></label><label>Preferred difficulty<select class="field" id="player-difficulty">${diffOptions.map(v=>`<option value="${v}">${v[0].toUpperCase()+v.slice(1)}</option>`).join('')}</select></label><div id="player-topic-settings"></div></details><details class="match-settings competitive-settings"><summary>Host match filters</summary><label>Question count<input class="field" id="match-count" type="number" min="1" max="20" value="10"></label><label>Difficulty<select class="field" id="match-difficulty">${diffOptions.map(v=>`<option value="${v}">${v[0].toUpperCase()+v.slice(1)}</option>`).join('')}</select></label><label>Learning suitability<select class="field" id="match-preset">${presetOptions.map(([v,l])=>`<option value="${v}">${l}</option>`).join('')}</select></label><label>Language<input class="field" id="match-language" maxlength="35" value="en"></label><label>Content scope<select class="field" id="match-scope"><option value="built_in">Built-in question bank</option><option value="public_library">Published public quizzes</option><option value="selected_quizzes">Choose published quizzes</option></select></label><label class="quiz-source-picker" hidden>Quizzes (select up to 20)<select class="field" id="match-quiz" multiple size="5" aria-label="Selected quizzes"></select></label><label>Exam collection<select class="field" id="match-exam"><option value="">Any / not applicable</option></select></label><label>Eligible categories<select class="field" id="match-categories" multiple size="5" aria-label="Eligible categories; leave empty for all"></select></label><label style="display:flex;align-items:center;gap:0.5rem;cursor:pointer;margin-top:0.5rem;"><input type="checkbox" id="match-private"> <span>Private / Invite-Only Arena</span></label><p class="microcopy">Leave categories empty for all. Filter summaries are shared with the room.</p></details>`;
    const drawTopics = () => {
      const host = picker.querySelector('#player-topic-settings');
      if (!state.categories.length) { host.innerHTML = '<p class="microcopy" role="status">Loading topic choices…</p>'; return; }
      host.innerHTML = `<fieldset><legend>Topic preferences</legend>${state.categories.map(c=>`<label class="topic-preference">${escapeHtml(c.display_name)}<select class="field" data-topic-id="${escapeHtml(c.id)}" aria-label="${escapeHtml(c.display_name)} preference">${[['increase','Increase'],['normal','Normal'],['decrease','Decrease'],['exclude','Exclude']].map(([v,l])=>`<option value="${v}" ${((state.preferences.topics[c.id]||'normal')===v)?'selected':''}>${l}</option>`).join('')}</select></label>`).join('')}</fieldset>`;
    };
    drawTopics();
    if (!state.categories.length) fetch('/api/categories').then(r=>r.ok?r.json():[]).then(tree=>{const flatten=nodes=>nodes.flatMap(n=>[{id:n.id,display_name:n.display_name},...flatten(n.children||[])]);state.categories=flatten(tree);const validIds=new Set(state.categories.map(c=>c.id));state.preferences.topics=Object.fromEntries(Object.entries(state.preferences.topics).filter(([id])=>validIds.has(id)));try{localStorage.setItem(PREFERENCE_KEY,JSON.stringify(state.preferences));}catch{}drawTopics();picker.querySelector('#match-categories').innerHTML=state.categories.map(c=>`<option value="${escapeHtml(c.id)}">${escapeHtml(c.display_name)}</option>`).join('');}).catch(()=>{picker.querySelector('#player-topic-settings').innerHTML='<p class="microcopy">Topic choices could not be loaded.</p>';});
    const fillQuizChoices = () => {
      const mode = select.value;
      const format = mode === 'bluff_master' ? 'open_ended' : mode === 'escape_room' ? 'puzzle' : 'multiple_choice';
      fetch(`/api/quizzes?limit=100&question_format=${format}`).then(r=>r.ok?r.json():{quizzes:[]}).then(data=>{
        state.publicQuizzes=data.quizzes||[];
        const selectQuizzes=picker.querySelector('#match-quiz');
        const publicOptions=state.publicQuizzes.map(q=>new Option(q.title,q.id));
        const ownedOptions=state.ownedQuizzes.filter(q=>q.status==='published' && q.questions.every(item=>item.format===format)).map(q=>new Option(`${q.title} · ${q.visibility}`,q.id));
        selectQuizzes.replaceChildren(...[...publicOptions,...ownedOptions.filter(owned=>!publicOptions.some(item=>item.value===owned.value))]);
      }).catch(()=>{});
    };
    const ownerCapability = sessionStorage.getItem('qv-owner-capability') || '';
    if (ownerCapability) fetch('/api/quizzes/mine',{headers:{Authorization:`Bearer ${ownerCapability}`}}).then(r=>r.ok?r.json():{quizzes:[]}).then(data=>{state.ownedQuizzes=(data.quizzes||[]).filter(q=>q.status==='published');fillQuizChoices();}).catch(()=>{});
    fetch('/api/exam-collections').then(r=>r.ok?r.json():[]).then(items=>{state.examCollections=items;const exam=picker.querySelector('#match-exam');exam.innerHTML='<option value="">Any / not applicable</option>'+items.map(x=>`<option value="${escapeHtml(x.id)}">${escapeHtml(x.display_name)}</option>`).join('');}).catch(()=>{});
    actions.before(picker);
    const select = picker.querySelector('#game-mode');
    const teamOption = document.createElement('option');
    teamOption.value = 'team_battle';
    teamOption.textContent = 'Team Battle · 4–8 players · even roster';
    select.append(teamOption);
    const eliminationOption = document.createElement('option');
    eliminationOption.value = 'elimination';
    eliminationOption.textContent = 'Elimination - survive each round';
    select.append(eliminationOption);
    for (const [value, label] of [['quiz_royale','Quiz Royale - 4-20 players - 3 lives'],['power_play','Power Play - abilities and energy']]) { const option = document.createElement('option'); option.value = value; option.textContent = label; select.append(option); }
    for (const [value, label] of [['bluff_master','Bluff Master - 3-8 players - fake and vote'],['escape_room','Escape Room Quiz - 2-8 players - team puzzle']]) { const option = document.createElement('option'); option.value = value; option.textContent = label; select.append(option); }
    const soloOption = document.createElement('option');
    soloOption.value = 'solo_practice';
    soloOption.textContent = 'Solo Practice - just you';
    select.append(soloOption);
    select.value = state.selectedMode;
    const updateSelection = () => {
      state.selectedMode = select.value;
      const rounds = picker.querySelector('#match-count');
      const fixedCount = ['bluff_master','escape_room'].includes(select.value);
      if (rounds && (fixedCount || !rounds.dataset.customized)) rounds.value = select.value === 'quiz_royale' ? '15' : '10';
      if (rounds) rounds.disabled = fixedCount;
      fillQuizChoices();
      picker.querySelector('#mode-description').textContent = select.value === 'quiz_royale'
        ? 'Quiz Royale: four to twenty players start with three lives. Wrong or unanswered rounds cost one life.'
        : select.value === 'power_play' ? 'Power Play: privately spend energy on 50:50, Double Points, or Shield.'
        : select.value === 'bluff_master' ? 'Bluff Master: write anonymous fake answers, then vote. Correct votes earn 1,000; fooling votes earn 500.'
        : select.value === 'escape_room' ? 'Escape Room: solve ten linked puzzles together with three shared hints before the 15-minute clock ends.'
        : select.value === 'speed_challenge'
        ? 'Speed Challenge: 1,000 points plus up to 500 speed points scaled to 10 seconds. Momentum Shift is off.'
        : select.value === 'elimination'
          ? 'Elimination: wrong or unanswered means out. Survive all 10 rounds or be the last player standing.'
          : select.value === 'solo_practice'
            ? 'Solo Practice: one real player, ten questions, personal scoring, and no leaderboard or Momentum Shift.'
          : select.value === 'team_battle'
          ? 'Team Battle: 4–8 real players, with an even roster. Teams alternate by join order; individual points combine for the team. Momentum Shift is off.'
          : 'Classic: 1,000 points plus up to 500 speed points. Momentum Shift is active.';
      const timePill = app.querySelector('.format-row .format-pill:last-child b');
      if (timePill) timePill.textContent = ({speed_challenge:'10s',quiz_royale:'12s',bluff_master:'30s',escape_room:'15m'})[select.value] || '15s';
      const createButton = app.querySelector('[data-action="create"]');
      if (createButton) createButton.textContent = select.value === 'solo_practice' ? 'Create Solo Practice' : '✦  Create Arena';
      const homeCopy = app.querySelector('.home-layout > .hero-copy > p');
      if (homeCopy) homeCopy.textContent = select.value === 'solo_practice'
        ? 'Ten questions, your own pace, and a score that is yours alone. Step into the arena whenever you are ready.'
        : select.value === 'bluff_master' ? 'Ten prompts. Anonymous bluffs. Try to fool your friends, then spot their fakes.'
        : select.value === 'escape_room' ? 'Five rooms, ten linked puzzles, and one shared team timer. Work together to escape.'
        : 'Ten quick-fire questions. Your favorite people. One gloriously debatable winner. Your next great game night starts here.';
      const joinArt = app.querySelector('.art-label.bottom strong');
      if (joinArt) joinArt.textContent = select.value === 'solo_practice' ? 'YOUR OWN PRACTICE ARENA' : 'YOUR CREW, YOUR ARENA';
      const homeFoot = app.querySelector('.play-card > .microcopy');
      if (homeFoot) homeFoot.textContent = select.value === 'solo_practice'
        ? 'No account. No downloads. No room code or opponent needed.'
        : 'No account. No downloads. Just a code and a little friendly rivalry.';
    };
    select.addEventListener('change', updateSelection);
    picker.querySelector('#match-count').addEventListener('input', event => { event.target.dataset.customized = 'true'; });
    picker.querySelector('#player-preset').value = state.preferences.learning_preset;
    picker.querySelector('#player-difficulty').value = state.preferences.difficulty;
    const playerLanguage = document.createElement('label');
    playerLanguage.innerHTML = 'Preferred language<input class="field" id="player-language" maxlength="35" value="en" aria-label="Preferred content language">';
    picker.querySelector('#player-topic-settings').before(playerLanguage);
    picker.querySelector('#player-language').value = state.preferences.language;
    // Competitive defaults are independent from this browser's private preferences.
    picker.querySelector('#match-preset').value = 'teen_challenge';
    picker.querySelector('#match-difficulty').value = 'medium';
    const savePreferences = () => {
      state.preferences.learning_preset = picker.querySelector('#player-preset').value;
      state.preferences.difficulty = picker.querySelector('#player-difficulty').value;
      state.preferences.language = picker.querySelector('#player-language').value.trim().toLowerCase() || 'en';
      try { localStorage.setItem(PREFERENCE_KEY, JSON.stringify(state.preferences)); } catch { notify('Preferences could not be saved on this device.'); }
    };
    picker.addEventListener('change', event => {
      if (event.target.matches('[data-topic-id]')) state.preferences.topics[event.target.dataset.topicId] = event.target.value;
      if (event.target.matches('#player-preset,#player-difficulty,#player-language,[data-topic-id]')) savePreferences();
      if (event.target.id === 'match-scope') picker.querySelector('.quiz-source-picker').hidden = event.target.value !== 'selected_quizzes';
    });
    updateSelection();
  }
  function updateModePresentation() {
    const room = state.room;
    if (!room) return;
    const speedMode = room.mode === 'speed_challenge';
    const teamMode = room.mode === 'team_battle';
    const royaleMode = room.mode === 'quiz_royale';
    const powerPlayMode = room.mode === 'power_play';
    const eliminationMode = room.mode === 'elimination' || royaleMode;
    const soloMode = room.mode === 'solo_practice';
    const timeRow = [...app.querySelectorAll('.side-stack .rule-line')]
      .find(row => row.querySelector('span')?.textContent === 'Answer time');
    if (timeRow) timeRow.querySelector('strong').textContent = `${room.question_seconds || 15} seconds`;
    const formatCard = app.querySelector('.side-stack .rules-card');
    if (formatCard && !formatCard.querySelector('.mode-line')) {
      const modeRow = document.createElement('div');
      modeRow.className = 'rule-line mode-line';
      modeRow.innerHTML = '<span>Mode</span><strong></strong>';
      formatCard.querySelector('h3')?.after(modeRow);
    }
    const modeRow = app.querySelector('.side-stack .mode-line strong');
    if (modeRow) modeRow.textContent = royaleMode ? 'Quiz Royale' : powerPlayMode ? 'Power Play' : speedMode ? 'Speed Challenge' : teamMode ? 'Team Battle' : eliminationMode ? 'Elimination' : soloMode ? 'Solo Practice' : 'Classic';
    const momentumCard = app.querySelector('.momentum-card');
    if (momentumCard) momentumCard.hidden = !room.momentum?.enabled;
    const momentumNote = app.querySelector('.momentum-note');
    if (momentumNote) momentumNote.hidden = !room.momentum?.enabled;
    if (teamMode) {
      const playerRule = [...app.querySelectorAll('.side-stack .rule-line')]
        .find(row => row.querySelector('span')?.textContent === 'Players');
      if (playerRule) playerRule.querySelector('strong').textContent = '4–8 · even';
      const roomNote = app.querySelector('.room-note');
      if (roomNote) roomNote.textContent = 'Team Battle requires an even roster of 4, 6, or 8 real players. Teams alternate by join order; reconnecting members keep their seat.';
      const startButton = app.querySelector('[data-action="start"]');
      if (startButton?.nextElementSibling) {
        startButton.nextElementSibling.textContent = room.can_start
          ? 'Teams alternate by join order. Individual points add to the team total.'
          : room.can_start_reason || 'Team Battle requires an even roster and four connected players.';
      }
      const board = app.querySelector('.leaderboard-card');
      if (board && room.teams) {
        let teamBoard = board.querySelector('.team-live-scoreboard');
        if (!teamBoard) {
          teamBoard = document.createElement('div');
          teamBoard.className = 'team-live-scoreboard';
          board.querySelector('.leaderboard-head')?.after(teamBoard);
        }
        teamBoard.innerHTML = room.teams.map(team => `<div class="team-live-row team-${escapeHtml(team.team_id)}"><span>${escapeHtml(team.name)}</span><strong>${Number(team.score).toLocaleString()} pts</strong><small>${team.correct} correct · ${Number(team.correct_response_time_ms).toLocaleString()} ms</small></div>`).join('');
      }
      const results = room.results;
      if (results) renderTeamResults(results, room);
      applyTeamLabels(room);
    }
    if (eliminationMode) {
      const playerRule = [...app.querySelectorAll('.side-stack .rule-line')]
        .find(row => row.querySelector('span')?.textContent === 'Players');
      if (playerRule) playerRule.querySelector('strong').textContent = royaleMode ? '4-20' : '2-8';
      const roomNote = app.querySelector('.room-note');
      if (roomNote) roomNote.textContent = royaleMode ? 'Wrong or unanswered answers cost one of three lives. Last remaining player wins.' : 'Wrong or unanswered answers eliminate a player at reveal. Disconnected players can reconnect until the question deadline.';
      const startNote = app.querySelector('[data-action="start"]')?.nextElementSibling;
      if (startNote) startNote.textContent = room.can_start ? 'Answer correctly each round to stay in the arena.' : (room.can_start_reason || 'At least two connected players are needed to start.');
      const count = app.querySelector('.remaining-count');
      if (count) count.textContent = `${room.remaining_players ?? room.players.filter(player => !player.eliminated).length} still in`;
      const quizTags = app.querySelector('.quiz-tags');
      if (quizTags && !count) {
        const badge = document.createElement('span');
        badge.className = 'remaining-count';
        badge.textContent = `${room.remaining_players ?? room.players.filter(player => !player.eliminated).length} still in`;
        quizTags.append(badge);
      }
      for (const row of app.querySelectorAll('.player-row, .leader-row')) {
        const playerName = row.querySelector('.player-name, .leader-name')?.textContent.replace(/\s*\(you\)\s*$/, '').trim();
        const player = room.players.find(candidate => candidate.name === playerName);
        if (!player || row.querySelector('.elimination-pill')) continue;
        const badge = document.createElement('span');
        badge.className = `elimination-pill ${player.eliminated ? 'is-out' : 'is-alive'}`;
        badge.textContent = player.eliminated ? `OUT - ${player.elimination_reason || 'eliminated'}` : royaleMode ? `♥ ${player.lives} lives` : 'IN';
        (row.querySelector('.player-info, .leader-name') || row).after(badge);
      }
      const current = room.players.find(player => player.id === state.playerId);
      if (current?.eliminated && room.phase !== 'waiting' && room.phase !== 'results') {
        const answerGrid = app.querySelector('.answer-grid');
        if (answerGrid) answerGrid.outerHTML = '<div class="eliminated-watching" role="status"><strong>You are out.</strong><span>Watch the finish unfold. Reconnect remains available until each question closes.</span></div>';
      }
      const revealBanner = app.querySelector('.reveal-banner');
      if (revealBanner && current?.eliminated) revealBanner.textContent = current.elimination_reason === 'timeout' ? 'Time ran out. You are eliminated; watch the remaining players.' : 'That answer was incorrect. You are eliminated; watch the remaining players.';
      if (room.phase === 'results' && room.results) renderEliminationResults(room.results, royaleMode, room.total_rounds);
    }
    if (soloMode) {
      const codeRow = app.querySelector('.room-code-row');
      if (codeRow) codeRow.hidden = true;
      const crew = app.querySelector('.crew-heading');
      if (crew) crew.hidden = true;
      const playerList = app.querySelector('.player-list');
      if (playerList) playerList.hidden = true;
      const roomHeading = app.querySelector('.room-card h2');
      if (roomHeading) roomHeading.textContent = 'Your practice arena is ready.';
      const roomKicker = app.querySelector('.room-card .section-kicker');
      if (roomKicker) roomKicker.textContent = 'SOLO PRACTICE · JUST YOU';
      const roomNote = app.querySelector('.room-note');
      if (roomNote) roomNote.textContent = 'Just you, ten questions, and your own score. Nothing to share and nobody to wait for.';
      const capacity = app.querySelector('.capacity');
      if (capacity) capacity.textContent = '1 / 1 player';
      const startButton = app.querySelector('[data-action="start"]');
      if (startButton) startButton.textContent = 'Start Practice';
      const startNote = startButton?.nextElementSibling;
      if (startNote) startNote.textContent = room.can_start ? 'Ready when you are. Your first question begins after a short countdown.' : (room.can_start_reason || 'Solo Practice needs one connected player.');
      const playersRule = [...app.querySelectorAll('.side-stack .rule-line')].find(row => row.querySelector('span')?.textContent === 'Players');
      if (playersRule) playersRule.querySelector('strong').textContent = '1 player';
      const leaderboardCard = app.querySelector('.leaderboard-card');
      if (leaderboardCard) leaderboardCard.hidden = true;
      const quizTags = app.querySelector('.quiz-tags');
      if (quizTags) {
        let scoreBadge = quizTags.querySelector('.solo-score');
        if (!scoreBadge) {
          scoreBadge = document.createElement('span');
          scoreBadge.className = 'solo-score';
          quizTags.append(scoreBadge);
        }
        const player = room.players.find(candidate => candidate.id === state.playerId);
        scoreBadge.textContent = `SCORE  ${Number(player?.score || 0).toLocaleString()}`;
      }
      const answerStatus = app.querySelector('.answer-status');
      if (answerStatus) answerStatus.textContent = room.answered ? 'Answer locked' : 'Your answer';
      if (room.phase === 'results' && room.results?.practice_summary) renderSoloResults(room.results.practice_summary);
    }
    if (speedMode) {
      app.querySelectorAll('.final-stat').forEach(stat => {
        stat.textContent = stat.textContent.split(' · ')[0];
      });
      const log = app.querySelector('.momentum-log');
      if (log) log.textContent = 'Momentum Shift is disabled in Speed Challenge.';
    }
  }
  function renderSoloResults(summary) {
    const heading = app.querySelector('.results-card h2');
    const copy = app.querySelector('.results-copy');
    if (heading) heading.textContent = 'Practice complete.';
    if (copy) copy.textContent = 'Your ten-round result, calculated by the arena.';
    const crown = app.querySelector('.winner-crown');
    if (crown) crown.hidden = true;
    const podium = app.querySelector('.podium');
    if (podium) podium.hidden = true;
    const ranking = app.querySelector('.final-list');
    if (ranking) ranking.hidden = true;
    const momentum = app.querySelector('.momentum-log');
    if (momentum) momentum.hidden = true;
    const actions = app.querySelector('.results-actions');
    if (actions) {
      const replay = actions.querySelector('[data-action="rematch"]');
      if (replay) replay.textContent = 'Practice again';
    }
    if (actions && !app.querySelector('.practice-result-grid')) {
      const accuracy = summary.accuracy_percent === null ? '—' : `${Number(summary.accuracy_percent).toFixed(1)}%`;
      const result = document.createElement('section');
      result.className = 'practice-result-grid';
      result.setAttribute('aria-label', 'Your practice results');
      result.innerHTML = `<article class="practice-result-score"><span>Final score</span><strong>${Number(summary.score).toLocaleString()}</strong></article><article><span>Correct</span><strong>${summary.correct_answers}</strong></article><article><span>Incorrect</span><strong>${summary.incorrect_answers}</strong></article><article><span>Unanswered</span><strong>${summary.unanswered_questions}</strong></article><article><span>Accuracy</span><strong>${accuracy}</strong><small>of attempted answers</small></article>`;
      actions.before(result);
    }
  }
  function renderEliminationResults(results, royaleMode = false, totalRounds = 10) {
    const rows = results.leaderboard || [];
    const winningId = (results.winning_player_ids || [])[0];
    const winner = rows.find(player => player.id === winningId);
    const heading = app.querySelector('.results-card h2');
    const copy = app.querySelector('.results-copy');
    if (heading) heading.textContent = results.is_draw
      ? (results.end_reason === 'all_eliminated' ? 'The arena took everyone out.' : 'The survivors share first place.')
      : `${winner?.name || 'The arena'} takes the crown.`;
    if (copy) copy.textContent = results.end_reason === 'one_survivor'
      ? 'Last player standing.'
      : results.end_reason === 'all_eliminated'
        ? 'Everyone left in the arena went out together. They share the final placement.'
        : `${totalRounds} rounds complete. ${royaleMode ? 'Survivors rank by lives, then points and correct answers.' : 'Survivors rank by points, correct answers, and correct response time.'}`;
    const podium = app.querySelector('.podium');
    if (podium) podium.hidden = true;
    const list = app.querySelector('.final-list');
    if (list) list.innerHTML = rows.map(player => {
      const suffix = player.placement === 1 ? 'st' : player.placement === 2 ? 'nd' : player.placement === 3 ? 'rd' : 'th';
      const place = `${player.shared_placement ? 'Shared ' : ''}${player.placement}${suffix}`;
      const status = player.eliminated ? `Out in round ${player.eliminated_round} (${player.elimination_reason})` : royaleMode ? `Survived with ${player.lives} lives` : 'Survived';
      return `<div class="final-row elimination-final-row"><span class="rank ${player.placement === 1 ? 'gold' : player.placement === 2 ? 'silver' : player.placement === 3 ? 'bronze' : ''}">${place}</span><span class="leader-name">${escapeHtml(player.name)}</span><span class="final-stat">${status} Â· ${player.correct} correct Â· ${Number(player.correct_response_time_ms).toLocaleString()} ms</span><strong class="leader-score">${Number(player.score).toLocaleString()}</strong></div>`;
    }).join('');
    const log = app.querySelector('.momentum-log');
    if (log) log.textContent = 'Momentum Shift is disabled in Elimination.';
  }
  function renderTeamResults(results, room) {
    const heading = app.querySelector('.results-card h2');
    const copy = app.querySelector('.results-copy');
    const teams = results.teams || [];
    const winners = teams.filter(team => team.is_winner);
    if (heading) heading.textContent = results.is_draw
      ? 'The teams share the win.'
      : `${winners[0]?.name || 'Team'} takes the crown.`;
    if (copy) copy.textContent = teams.map(team => `${team.name}: ${Number(team.score).toLocaleString()} points`).join(' · ');
    const podium = app.querySelector('.podium');
    if (podium) podium.hidden = true;
    const finalList = app.querySelector('.final-list');
    if (finalList) {
      app.querySelectorAll('.final-stat').forEach(stat => {
        const playerName = stat.parentElement.querySelector('.leader-name')?.textContent || '';
        const player = room.players.find(candidate => candidate.name === playerName);
        if (player) stat.textContent = `${player.team_name} · ${player.correct} correct · ${Number(player.correct_response_time_ms).toLocaleString()} ms`;
      });
      if (!app.querySelector('.team-final-results')) {
        const section = document.createElement('section');
        section.className = 'team-final-results';
        section.setAttribute('aria-label', 'Final team rankings');
        section.innerHTML = `<h3>Team standings</h3>${teams.map(team => {
          const placement = team.shared_placement ? 'Shared 1st place' : `${team.placement}${team.placement === 1 ? 'st' : 'nd'} place`;
          const members = room.players.filter(player => player.team_id === team.team_id);
          return `<article class="team-result-card team-${escapeHtml(team.team_id)}"><div class="team-result-heading"><h4>${escapeHtml(team.name)}</h4><span>${placement}${team.is_winner ? ' · Winner' : ''}</span></div><div class="team-result-total">${Number(team.score).toLocaleString()} points</div><p>${team.correct} aggregate correct · ${Number(team.correct_response_time_ms).toLocaleString()} ms correct response time</p><ul>${members.map(player => `<li><span>${escapeHtml(player.name)}</span><strong>${Number(player.score).toLocaleString()} pts · ${player.correct} correct</strong></li>`).join('')}</ul></article>`;
        }).join('')}`;
        finalList.before(section);
      }
    }
    const momentumLog = app.querySelector('.momentum-log');
    if (momentumLog) momentumLog.textContent = 'Momentum Shift is disabled in Team Battle.';
  }
  function applyTeamLabels(room) {
    if (room.mode !== 'team_battle') return;
    for (const row of app.querySelectorAll('.player-row, .leader-row')) {
      const nameNode = row.querySelector('.player-name, .leader-name');
      const playerName = nameNode?.textContent.replace(/\s*\(you\)\s*$/, '').trim();
      const player = room.players.find(candidate => candidate.name === playerName);
      if (!player) continue;
      const stateNode = row.querySelector('.player-state');
      if (stateNode && player.reconnect_expired) stateNode.lastChild.textContent = 'Reconnect window expired';
      if (row.querySelector('.team-pill')) continue;
      const badge = document.createElement('span');
      badge.className = `team-pill team-${player.team_id}`;
      badge.textContent = player.team_name;
      (row.querySelector('.player-info, .leader-name') || row).after(badge);
    }
  }
  function lobby() {
    const room = state.room;
    const host = room.host_id === state.playerId;
    const connected = room.players.filter(player => player.connected).length;
    const hostName = room.players.find(player => player.id === room.host_id)?.name || 'the host';
    const filters = room.match_filters || {};
    const categoryNames = (filters.categories || []).map(id => state.categories.find(c=>c.id===id)?.display_name || id);
    const examName = state.examCollections.find(x=>x.id===filters.exam_collection_id)?.display_name || (filters.exam_collection_id ? filters.exam_collection_id : 'Any');
    const filterSummary = `<section class="rules-card"><h3>Shared question filters</h3><div class="rule-line"><span>Content</span><strong>${filters.content_scope === 'public_library' ? 'Published public library' : filters.content_scope === 'selected_quizzes' ? 'Selected quizzes' : 'Built-in bank'}</strong></div><div class="rule-line"><span>Difficulty</span><strong>${escapeHtml(filters.difficulty || 'Medium')}</strong></div><div class="rule-line"><span>Suitability</span><strong>${escapeHtml((filters.learning_preset || 'teen_challenge').replaceAll('_',' '))}</strong></div><div class="rule-line"><span>Language</span><strong>${escapeHtml(filters.language || 'en')}</strong></div><div class="rule-line"><span>Exam collection</span><strong>${escapeHtml(examName)}</strong></div><div class="rule-line"><span>Questions</span><strong>${filters.question_count} rounds</strong></div><div class="rule-line"><span>Topics</span><strong>${escapeHtml(categoryNames.length ? categoryNames.join(', ') : 'All eligible')}</strong></div><p class="microcopy">Everyone receives the same validated question sequence.</p></section>`;
    if (room.mode === 'bluff_master' || room.mode === 'escape_room') {
      const bluff = room.mode === 'bluff_master';
      return `<section class="room-layout"><div class="room-card"><button class="back-button" type="button" data-action="leave">← &nbsp;Leave arena</button><div class="section-kicker">${bluff ? 'BLUFF MASTER' : 'ESCAPE ROOM QUIZ'}</div><h2>${bluff ? 'Think up a convincing bluff.' : 'Unlock the five-room challenge.'}</h2><div class="room-code-row"><div class="room-code">${escapeHtml(room.code)}</div><button class="btn btn-secondary" type="button" data-action="copy">Copy code</button></div><p class="room-note">${bluff ? 'Each player writes one fake answer, then everyone votes anonymously. Three players minimum.' : 'Work as one team through ten linked puzzles. You share three hints and a fifteen-minute clock.'}</p><div class="crew-heading"><h3>The crew</h3><span class="capacity">${room.players.length} / 8 players</span></div><div class="player-list" aria-live="polite">${room.players.map(playerRow).join('')}</div>${host ? `<button class="btn btn-primary btn-full" type="button" data-action="start" ${room.can_start ? '' : 'disabled'}>${bluff ? 'Start Bluff Master' : 'Start Escape Room'}</button><p class="microcopy">${room.can_start ? 'Content is validated before the countdown begins.' : escapeHtml(room.can_start_reason || 'Waiting for players.')}</p>` : `<div class="waiting-note">Waiting for ${escapeHtml(hostName)} to start…</div>`}</div><aside class="side-stack">${filterSummary}<section class="rules-card"><h3>${bluff ? 'Round rules' : 'Adventure rules'}</h3>${bluff ? '<p>30 seconds to submit a fake answer, then 15 seconds to vote. Correct vote: 1,000 points. Each opponent fooled: 500 points to the author. Results show answer authors.</p>' : '<p>Five rooms, two validated puzzles each. Correct team answers unlock the next puzzle. Three shared hints; finish all ten puzzles within 15 minutes.</p>'}</section></aside></section>`;
    }
    return `<section class="room-layout"><div class="room-card"><button class="back-button" type="button" data-action="leave">← &nbsp;Leave arena</button><div class="section-kicker">YOUR PRIVATE GAME ROOM</div><h2>Gather your people.</h2><div class="room-code-row"><div class="room-code" aria-label="Room code ${escapeHtml(room.code)}">${escapeHtml(room.code)}</div><button class="btn btn-secondary" type="button" data-action="copy">▣ &nbsp;Copy code</button></div><p class="room-note">Share this code with your crew. Once two players are here, the host can launch the quiz.</p><div class="crew-heading"><h3>The crew</h3><span class="capacity">${room.players.length} / ${room.max_players || 8} players</span></div><div class="player-list" aria-live="polite">${room.players.map(playerRow).join('')}</div>${host ? `<button class="btn btn-primary btn-full" type="button" data-action="start" ${room.can_start ? '' : 'disabled'}>Launch the quiz &nbsp;✦</button><p class="microcopy">${room.can_start ? 'Everyone ready? Let the brain battle begin.' : 'Waiting for at least one more connected player.'}</p>` : `<div class="waiting-note"><span class="pulse-dot"></span>Waiting for ${escapeHtml(hostName)} to launch…</div>`}</div><aside class="side-stack">${filterSummary}<section class="rules-card"><h3>Tonight's format</h3><div class="rule-line"><span>Players</span><strong>2–8</strong></div><div class="rule-line"><span>Questions</span><strong>${filters.question_count || 10} rounds</strong></div><div class="rule-line"><span>Answer time</span><strong>15 seconds</strong></div></section><section class="rules-card"><h3>How scoring works</h3><div class="rule-line"><span>Correct answer</span><strong>1,000 pts</strong></div><div class="rule-line"><span>Speed bonus</span><strong>Up to 500</strong></div><p class="microcopy">The server records answer time. Faster correct answers earn a bigger bonus.</p></section><section class="rules-card momentum-card"><h3>Momentum Shift</h3><p class="momentum-copy">At the start of a round, players trailing the leader by 1–1,500 points qualify. A correct answer in the first 5 seconds earns a published <strong>+250 points</strong>. Nobody loses points.</p></section></aside></section>`;
  }
  function game() {
    const room = state.room;
    if (room.mode === 'bluff_master') return bluffGame(room);
    if (room.mode === 'escape_room') return escapeGame(room);
    const question = room.question;
    const feedback = room.answer_feedback;
    const countdown = room.phase === 'countdown';
    const labels = ['A', 'B', 'C', 'D'];
    const momentum = room.momentum;
    const power = room.power_play || {};
    return `<section class="game-layout"><div class="quiz-card"><div class="quiz-head"><div class="quiz-tags"><span class="round-tag">ROUND ${String(Math.min(room.round, 10)).padStart(2, '0')} / 10</span>${question ? `<span class="category-tag">${escapeHtml(question.category)}</span>` : ''}</div><div class="timer-ring" id="timer-ring" role="timer" aria-label="Time remaining"><span class="timer-number" id="timer-number">${countdown ? '3' : room.question_seconds}</span></div></div>${countdown ? `<h1 class="question-text">Get ready.<br>The arena opens in a moment.</h1><div class="waiting-note">Find your focus. First question incoming.</div>` : `<h1 class="question-text">${escapeHtml(question?.prompt || 'Loading the next question…')}</h1>${room.mode === 'power_play' ? `<section class="power-play-controls"><div class="energy-line"><strong>⚡ ${power.energy ?? 3} / 5 energy</strong><span>${power.shield_active ? 'Shield active' : power.double_active ? 'Double Points armed' : 'One ability per question'}</span></div><div class="ability-buttons">${[['fifty_fifty','50:50'],['double_points','Double Points'],['shield','Shield']].map(([id,label]) => `<button class="btn btn-secondary ability-button" type="button" data-action="ability" data-ability="${id}" ${(room.phase !== 'question' || room.answered || (power.energy ?? 0) < 2 || !(power.available || []).includes(id) || power.used_this_round) ? 'disabled' : ''}>${label}${(power.used || []).includes(id) ? ' · Used' : ''}</button>`).join('')}</div><p class="microcopy">Incorrect or unanswered results lose 250 points; Shield blocks the next such penalty.</p></section>` : ''}<div class="answer-grid">${((question?.visible_choices || [0,1,2,3]).map(index => [index, question.options[index]])).map(([index, option]) => { const reveal = room.phase === 'reveal'; const correct = reveal && feedback?.correct === index; const selected = feedback?.selected === index; const classes = ['answer-button', correct ? 'correct' : '', selected ? 'selected' : '', reveal && selected && !correct ? 'incorrect' : ''].filter(Boolean).join(' '); return `<button class="${classes}" type="button" data-action="answer" data-choice="${index}" ${room.phase !== 'question' || room.answered ? 'disabled' : ''}><span class="answer-letter">${labels[index]}</span><span>${escapeHtml(option)}</span>${correct ? '<span aria-label="Correct answer">✓</span>' : ''}</button>`; }).join('')}</div>${feedback ? `<div class="reveal-banner ${feedback.was_correct ? '' : 'wrong'}" role="status"><span>${feedback.was_correct ? '✦' : '↻'}</span><span>${feedback.was_correct ? `Brilliant. That one is yours. +${feedback.speed_bonus.toLocaleString()} speed${feedback.momentum_bonus ? ` · +${feedback.momentum_bonus} Momentum Shift` : ''}.` : 'The right answer is highlighted. Next round soon.'}</span></div>` : ''}${feedback && room.mode === 'power_play' ? `<p class="power-feedback" role="status">${feedback.doubled ? 'Double Points doubled your correct score.' : feedback.shield_consumed ? 'Shield consumed. The 250 point penalty was blocked.' : feedback.power_penalty ? '250 points were deducted.' : ''}</p>` : ''}<div class="quiz-foot"><span>${room.answered ? 'Answer locked in. Eyes on the reveal.' : 'Choose your answer before time runs out.'}</span><span class="answer-status"><span class="mini-dot"></span>${room.players.filter(player => player.answered).length} / ${room.players.length} answered</span></div><p class="momentum-note">${momentum?.eligible ? 'Momentum Shift active: you qualify this round. Correct in 5 seconds for +250.' : 'Momentum Shift: trailing by 1–1,500 points can earn +250 for a correct answer in the first 5 seconds.'}</p>`}</div>${leaderboard()}</section>`;
  }
  function phaseClock(room) {
    const phase = room.phase;
    const count = phase === 'countdown' ? 3 : phase === 'submission' ? 30 : phase === 'voting' ? 15 : phase === 'reveal' ? 5 : 900;
    return `<div class="timer-ring" id="timer-ring" role="timer" aria-label="Time remaining"><span class="timer-number" id="timer-number">${count}</span></div>`;
  }
  function bluffGame(room) {
    const bluff = room.bluff || {};
    const question = room.question;
    let actionArea = '';
    if (room.phase === 'countdown') actionArea = '<div class="waiting-note">The first prompt is coming up.</div>';
    else if (room.phase === 'submission') actionArea = bluff.submitted
      ? `<div class="waiting-note" role="status">Your answer was received. Waiting for other players (${bluff.submitted_count}/${bluff.player_count}).</div>`
      : `<label class="form-label" for="bluff-answer">Your fake answer</label><input class="field" id="bluff-answer" maxlength="120" autocomplete="off" placeholder="Write a believable answer"><button class="btn btn-primary btn-full" type="button" data-action="bluff-submit">Submit fake answer</button><p class="microcopy">Your answer stays hidden from other players during voting.</p>`;
    else if (room.phase === 'voting') actionArea = `<p class="phase-instruction">Choose the answer you think is correct. Other fake-answer authors stay anonymous.</p><div class="answer-grid">${(bluff.choices||[]).map((choice,index)=>`<button class="answer-button ${choice.is_yours?'own-bluff':''}" type="button" data-action="bluff-vote" data-choice-id="${escapeHtml(choice.id)}" ${bluff.voted||choice.is_yours?'disabled':''}><span class="answer-letter">${String.fromCharCode(65+index)}</span><span>${escapeHtml(choice.text)}${choice.is_yours?' · Your answer (cannot vote)':''}</span></button>`).join('')}</div>${bluff.voted?'<div class="waiting-note" role="status">Vote received. Waiting for the reveal.</div>':''}`;
    else if (room.phase === 'reveal') actionArea = `<div class="reveal-banner">Correct answer: ${escapeHtml(bluff.correct_answer)}</div><div class="bluff-results">${(bluff.results||[]).map(item=>`<article class="bluff-result ${item.correct?'correct':''}"><strong>${escapeHtml(item.text)}</strong><span>${item.correct?'Correct answer':`Fake answer by ${escapeHtml(item.author)}`}</span><small>${item.fooled_players.length?`Fooled: ${item.fooled_players.map(escapeHtml).join(', ')}`:'No players fooled'}</small></article>`).join('')}</div>`;
    return `<section class="game-layout"><div class="quiz-card"><div class="quiz-head"><div class="quiz-tags"><span class="round-tag">ROUND ${String(room.round).padStart(2,'0')} / 10</span><span class="category-tag">${room.phase==='submission'?'WRITE':room.phase==='voting'?'VOTE':room.phase==='reveal'?'REVEAL':'READY'}</span></div>${phaseClock(room)}</div><h1 class="question-text">${escapeHtml(question?.prompt || 'Get ready for a prompt.')}</h1>${actionArea}</div>${leaderboard()}</section>`;
  }
  function escapeGame(room) {
    const escape = room.escape || {};
    const puzzle = escape.current_puzzle;
    const countdown = room.phase === 'countdown';
    const progress = (escape.rooms||[]).map(item=>`<div class="escape-room-node ${item.state}" aria-label="Room ${item.room}: ${item.state}"><span>${item.room}</span><small>${item.state==='solved'?'Solved':item.state==='current'?'Current':'Locked'}</small></div>`).join('');
    const actionArea = countdown ? '<div class="waiting-note">Your team challenge starts in a moment.</div>' : puzzle ? `<div class="escape-progress"><strong>Room ${puzzle.room} · Puzzle ${puzzle.index+1} of 10</strong><span>${escape.solved_puzzles} puzzles solved</span></div><div class="escape-room-map">${progress}</div>${puzzle.hint?`<div class="hint-box" role="status"><strong>Team hint</strong><p>${escapeHtml(puzzle.hint)}</p></div>`:''}<label class="form-label" for="escape-answer">Team answer</label><input class="field" id="escape-answer" maxlength="200" autocomplete="off" placeholder="Enter your answer"><div class="escape-actions"><button class="btn btn-primary" type="button" data-action="escape-answer">Submit answer</button><button class="btn btn-secondary" type="button" data-action="escape-hint" ${escape.hints_remaining<=0||escape.hint_used?'disabled':''}>Use shared hint (${escape.hints_remaining} left)</button></div><p class="microcopy">One correct answer advances the shared team state. Wrong answers do not use hints.</p>` : '<div class="waiting-note">The team adventure is complete.</div>';
    return `<section class="game-layout escape-game-layout"><div class="quiz-card"><div class="quiz-head"><div class="quiz-tags"><span class="round-tag">ESCAPE ROOM</span><span class="category-tag">${escape.solved_puzzles||0} / 10 SOLVED</span></div>${phaseClock(room)}</div>${puzzle?`<h1 class="question-text">${escapeHtml(puzzle.prompt)}</h1>`:'<h1 class="question-text">Work together to unlock every room.</h1>'}${actionArea}</div><aside class="rules-card escape-team-card"><h3>Team adventurers</h3><div class="player-list">${room.players.map(playerRow).join('')}</div><div class="rule-line"><span>Rooms cleared</span><strong>${escape.completed_rooms||0} / 5</strong></div><div class="rule-line"><span>Shared hints</span><strong>${escape.hints_remaining ?? 3}</strong></div><p class="microcopy">Everyone sees the same puzzle, hint, timer, and progress.</p></aside></section>`;
  }
  function results() {
    const players = ranking();
    if (state.room.mode === 'escape_room') {
      const result = state.room.results || {};
      const victory = result.outcome === 'victory';
      return `<section class="results-card escape-results"><div class="winner-crown" aria-hidden="true">${victory?'✦':'⌛'}</div><div class="section-kicker">ESCAPE ROOM RESULTS</div><h2>${victory?'The team escaped!':'Time ran out.'}</h2><p class="results-copy">${result.team_outcome==='victory'?'Your team unlocked all five rooms.':'The team reached the end of the shared timer.'}</p><div class="escape-result-grid"><article><span>Rooms completed</span><strong>${result.completed_rooms} / 5</strong></article><article><span>Puzzles solved</span><strong>${result.solved_puzzles} / 10</strong></article><article><span>Hints remaining</span><strong>${result.remaining_hints}</strong></article><article><span>Elapsed time</span><strong>${Math.floor(result.elapsed_seconds/60)}:${String(result.elapsed_seconds%60).padStart(2,'0')}</strong></article></div>${state.playerId===state.room.host_id?'<div class="results-actions"><button class="btn btn-primary" type="button" data-action="rematch">Play again</button><button class="btn btn-secondary" type="button" data-action="lobby">Return to lobby</button></div>':'<div class="waiting-note">The host can start another adventure.</div>'}</section>`;
    }
    const winner = players[0];
    const podium = players.slice(0, 3);
    const podiumOrder = podium.length === 3 ? [podium[1], podium[0], podium[2]] : podium;
    const momentumAwards = state.room.results?.momentum_adjustments || [];
    return `<section class="results-card"><div class="winner-crown" aria-hidden="true">♛</div><div class="section-kicker" style="justify-content:center">TEN ROUNDS. ONE ARENA.</div><h2>${winner ? `${escapeHtml(winner.name)} takes the crown.` : 'The arena is yours.'}</h2><p class="results-copy">${winner ? `${winner.score.toLocaleString()} points · ${winner.correct} correct answers` : 'Thanks for playing together.'}</p><div class="podium" aria-label="Top players">${podiumOrder.map(player => { const place = players.indexOf(player) + 1; return `<div class="podium-place ${place === 1 ? 'first' : ''}"><div class="podium-medal">${['♛', '✧', '✦'][place - 1]}</div><div class="podium-name">${escapeHtml(player.name)}</div><div class="podium-score">${player.score.toLocaleString()}</div></div>`; }).join('')}</div><div class="final-list" aria-label="Final rankings">${players.map((player, index) => `<div class="final-row"><span class="rank ${index < 3 ? ['gold', 'silver', 'bronze'][index] : ''}">${index + 1}</span><span class="leader-name">${escapeHtml(player.name)}</span><span class="final-stat">${player.correct} correct · +${player.momentum_points} Momentum</span><strong class="leader-score">${player.score.toLocaleString()}</strong></div>`).join('')}</div>${momentumAwards.length ? `<p class="momentum-log">Momentum Shift awards: ${momentumAwards.map(adjustment => `${escapeHtml(adjustment.name)} +${adjustment.points} (round ${adjustment.round})`).join(' · ')}</p>` : '<p class="momentum-log">No Momentum Shift awards this match.</p>'}${state.playerId === state.room.host_id ? `<div class="results-actions"><button class="btn btn-primary" type="button" data-action="rematch">Run it back &nbsp;↻</button><button class="btn btn-secondary" type="button" data-action="lobby">Return to lobby</button></div>` : `<div class="waiting-note">The host can start a rematch whenever the crew is ready.</div>`}</section>`;
  }
  function profileScreen() {
    app.classList.remove('library-shell');
    if (state.profileLoading && !state.profile) {
      app.innerHTML = '<section class="profile-card"><div class="loading-state"><span class="loader"></span><p>Loading player profile…</p></div></section>';
      return;
    }
    if (!state.profileCredential || !state.profile) {
      app.innerHTML = `<section class="profile-card"><div class="section-kicker">OPTIONAL PLAYER IDENTITY</div><h1>Your competitive profile.</h1><p class="subcopy">Create or sign in to a pseudonymous profile to keep personal match history. Casual play remains available without a profile.</p><p class="profile-warning">Your credential is a private bearer key. It is stored only for this browser tab. Save a copy in a password manager; if it is lost, this anonymous profile cannot be recovered.</p>${state.profileNotice ? `<p class="library-message" role="alert">${escapeHtml(state.profileNotice)}</p>` : ''}<div class="profile-columns"><form class="profile-form"><h2>Create profile</h2><label class="form-label" for="profile-create-name">Display name</label><input class="field" id="profile-create-name" maxlength="24" autocomplete="nickname" required placeholder="Your player name"><button class="btn btn-primary" type="button" data-action="profile-create" ${state.profileLoading ? 'disabled' : ''}>${state.profileLoading ? 'Creating…' : 'Create profile'}</button></form><form class="profile-form"><h2>Sign in</h2><label class="form-label" for="profile-credential">Player credential</label><input class="field" id="profile-credential" type="password" autocomplete="current-password" maxlength="128" required placeholder="Paste your saved credential"><button class="btn btn-secondary" type="button" data-action="profile-signin" ${state.profileLoading ? 'disabled' : ''}>Sign in</button></form></div></section>`;
      return;
    }
    const stats = state.profileStats || { history_available: false, completed_matches: 0, wins: 0, losses: 0, draws: 0, mode_breakdown: [] };
    const myRatings = state.myRatings?.global_rating;
    const advStats = state.careerAdvancedStats || {
      total_matches: stats.completed_matches || 0,
      wins: stats.wins || 0,
      losses: stats.losses || 0,
      draws: stats.draws || 0,
      win_percentage: stats.completed_matches ? Math.round((stats.wins / stats.completed_matches) * 1000) / 10 : 0.0,
      current_streak: { type: 'none', count: 0 },
      longest_win_streak: 0,
      recent_form: { last_5: [], last_10: [] },
      total_correct: 0,
      total_answered: 0,
      accuracy_pct: 0.0,
      peak_rating: myRatings?.rating || 1000,
      current_rating: myRatings?.rating || 1000,
      is_provisional: myRatings?.is_provisional ?? true,
      rated_match_count: myRatings?.rated_match_count || 0,
      mode_performance: [],
      forfeit_monitoring: { forfeited_count: 0, forfeit_rate_pct: 0.0, forfeit_warning: false },
    };

    const ratingCard = myRatings ? `
      <h2>Competitive Elo Rating</h2>
      <div class="user-rating-banner">
        <div class="user-rating-value">${myRatings.rating}</div>
        <div class="user-rating-meta">
          <strong>Global Rating &nbsp;${myRatings.is_provisional ? '<span class="badge-status badge-provisional">PROVISIONAL</span>' : '<span class="badge-status badge-established">ESTABLISHED</span>'} <span class="badge-status" style="margin-left:.5rem;background:#35274d;color:#d8b4fe;border:1px solid #7e22ce;">PEAK ${advStats.peak_rating}</span></strong>
          <span>${myRatings.rated_match_count} rated matches · ${myRatings.wins} Wins · ${myRatings.draws} Draws · ${myRatings.losses} Losses</span>
        </div>
        ${state.myRatings?.recent_changes?.length ? `
        <div class="recent-changes-pills" title="Recent rating movements">
          ${state.myRatings.recent_changes.slice(0, 5).map(c => `<span class="delta-pill ${c.rating_delta > 0 ? 'delta-pos' : c.rating_delta < 0 ? 'delta-neg' : 'delta-zero'}">${c.rating_delta > 0 ? '+' : ''}${c.rating_delta}</span>`).join('')}
        </div>` : ''}
      </div>` : (state.profileMatches.length ? '<p class="profile-empty">Casual matches completed. Complete an eligible competitive match to establish your Elo rating.</p>' : '<p class="profile-empty">No rated competitive matches completed yet.</p>');

    const streakType = advStats.current_streak?.type;
    const streakCount = advStats.current_streak?.count || 0;
    const streakDisplay = streakCount === 0 || streakType === 'none' ? '—' : `${streakType === 'win' ? '+' : ''}${streakCount} ${streakType.toUpperCase()}`;
    const streakColor = streakType === 'win' ? '#74c69d' : streakType === 'loss' ? '#ff8fa3' : '#a3e7fc';

    const forfeitMon = advStats.forfeit_monitoring || { forfeit_warning: false, forfeit_rate_pct: 0.0 };
    const fairPlayDisplay = forfeitMon.forfeit_warning ? `⚠️ ${forfeitMon.forfeit_rate_pct}%` : '✓ Clean';
    const fairPlayColor = forfeitMon.forfeit_warning ? '#ff8fa3' : '#74c69d';
    const fairPlaySub = forfeitMon.forfeit_warning ? 'High forfeit rate' : `${forfeitMon.forfeit_rate_pct}% forfeit rate`;

    const careerOverviewHtml = `
      <h2>Career Overview</h2>
      <div class="career-overview-grid">
        <div class="career-stat-card">
          <span class="career-stat-val">${advStats.win_percentage}%</span>
          <span class="career-stat-label">Win Rate</span>
          <span class="career-stat-sub">${advStats.wins}W · ${advStats.losses}L · ${advStats.draws}D</span>
        </div>
        <div class="career-stat-card">
          <span class="career-stat-val" style="color:${streakColor}">${streakDisplay}</span>
          <span class="career-stat-label">Current Streak</span>
          <span class="career-stat-sub">Recent consecutive</span>
        </div>
        <div class="career-stat-card">
          <span class="career-stat-val" style="color:#ffd700">${advStats.longest_win_streak}</span>
          <span class="career-stat-label">Best Streak</span>
          <span class="career-stat-sub">Consecutive wins</span>
        </div>
        <div class="career-stat-card">
          <span class="career-stat-val">${advStats.accuracy_pct}%</span>
          <span class="career-stat-label">Accuracy</span>
          <span class="career-stat-sub">${advStats.total_correct} / ${advStats.total_answered} correct</span>
        </div>
        <div class="career-stat-card">
          <span class="career-stat-val" style="color:${fairPlayColor}">${fairPlayDisplay}</span>
          <span class="career-stat-label">Fair Play</span>
          <span class="career-stat-sub">${fairPlaySub}</span>
        </div>
      </div>
    `;

    const renderFormPills = (outcomes) => (outcomes || []).map(out => {
      const cls = out === 'win' ? 'form-pill-win' : out === 'loss' ? 'form-pill-loss' : 'form-pill-draw';
      const letter = out === 'win' ? 'W' : out === 'loss' ? 'L' : 'D';
      return `<span class="form-pill ${cls}" title="${out.toUpperCase()}">${letter}</span>`;
    }).join('');

    const form5Html = renderFormPills(advStats.recent_form?.last_5);
    const form10Html = renderFormPills(advStats.recent_form?.last_10);
    const recentFormHtml = `
      <h2>Recent Form</h2>
      <div class="form-guide-container" style="margin-bottom:1.25rem">
        <div style="font-size:.85rem;color:#b5b5cc;margin-bottom:.4rem">Last 5 Matches:</div>
        <div class="form-guide-track">${form5Html || '<span class="microcopy">No eligible matches yet</span>'}</div>
        ${form10Html && advStats.recent_form?.last_10?.length > 5 ? `
        <div style="font-size:.85rem;color:#b5b5cc;margin-top:.8rem;margin-bottom:.4rem">Last 10 Matches:</div>
        <div class="form-guide-track">${form10Html}</div>` : ''}
      </div>
    `;

    const trophies = state.careerHonors?.trophies || [];
    const trophiesHtml = trophies.length ? `
      <div class="trophy-shelf">
        ${trophies.map(t => `
          <article class="trophy-card tier-${t.tier}">
            <span class="trophy-badge">${t.tier === 'gold' ? '🥇' : t.tier === 'silver' ? '🥈' : '🥉'} ${escapeHtml(t.title)}</span>
            <h3 class="trophy-title">${escapeHtml(t.event_name)}</h3>
            <span class="trophy-meta">${escapeHtml(formatMode(t.mode))} · ${t.completed_at ? new Date(t.completed_at).toLocaleDateString() : 'Completed'}</span>
          </article>
        `).join('')}
      </div>
    ` : '<p class="profile-empty">No tournament or league trophies yet. Reach a tournament final or league podium to earn honors!</p>';

    const modePerfRows = (advStats.mode_performance || []).map(m => `
      <tr>
        <td style="font-weight:600;color:#fff">${escapeHtml(formatMode(m.mode))}</td>
        <td>${m.matches}</td>
        <td>${m.wins}W - ${m.losses}L - ${m.draws}D</td>
        <td style="font-weight:700;color:#a3e7fc">${m.win_rate_pct}%</td>
        <td>${m.avg_score}</td>
        <td>${m.best_score}</td>
        <td>${m.accuracy_pct}%</td>
      </tr>
    `).join('');

    const modeAnalyticsHtml = (advStats.mode_performance && advStats.mode_performance.length) ? `
      <h2>Game Mode Analytics</h2>
      <div class="standings-table-container" style="margin-bottom:1.5rem">
        <table class="standings-table">
          <thead>
            <tr>
              <th>Mode</th>
              <th>Matches</th>
              <th>Record</th>
              <th>Win %</th>
              <th>Avg Score</th>
              <th>Best Score</th>
              <th>Accuracy</th>
            </tr>
          </thead>
          <tbody>
            ${modePerfRows}
          </tbody>
        </table>
      </div>
    ` : '';

    const h2hSearchHtml = `
      <h2>Head-to-Head Comparison</h2>
      <p class="subcopy" style="margin-bottom:.75rem">Compare your competitive record and pairwise stats against any opponent.</p>
      <div class="h2h-lookup-bar" style="display:flex;gap:.6rem;flex-wrap:wrap;align-items:center;margin-bottom:1.5rem">
        <input class="field" id="h2h-opponent-input" placeholder="Paste Opponent Player Profile ID (32 hex characters)" style="max-width:380px;font-size:.85rem;padding:.5rem .75rem" maxlength="64">
        <button class="btn btn-secondary" type="button" data-action="profile-h2h-compare">Compare Head-to-Head</button>
      </div>
    `;

    const matches = state.profileMatches.map(match => {
      const isRematchEligible = match.mode !== 'solo_practice' && !match.tournament_id && !match.league_id;
      const opponents = (match.opponents || []).map(op => `
        <button class="btn btn-secondary" type="button" data-action="open-h2h" data-opponent-id="${escapeHtml(op.player_id)}" data-opponent-name="${escapeHtml(op.display_name)}" style="padding:.2rem .5rem;font-size:.74rem;line-height:1;margin-left:.25rem">
          Vs ${escapeHtml(op.display_name)}
        </button>
        ${isRematchEligible && op.player_id ? `
        <button class="btn btn-primary" type="button" data-action="challenge-rematch" data-match-id="${escapeHtml(match.id)}" data-opponent-id="${escapeHtml(op.player_id)}" data-opponent-name="${escapeHtml(op.display_name)}" style="padding:.2rem .5rem;font-size:.74rem;line-height:1;margin-left:.25rem">
          ⚔️ Rematch
        </button>` : ''}
      `).join('');
      return `
        <article class="profile-match history-item">
          <div>
            <strong>${escapeHtml(match.mode.replaceAll('_', ' '))}</strong>
            <span>${escapeHtml(new Date(match.completed_at).toLocaleString())}</span>
            ${opponents ? `<div style="margin-top:.35rem;display:flex;align-items:center;flex-wrap:wrap;gap:.25rem"><span style="font-size:.75rem;color:#9f8aff">Opponents:</span> ${opponents}</div>` : ''}
          </div>
          <div>
            <strong>${escapeHtml(match.outcome.replaceAll('_', ' '))}</strong>
            <span>${match.placement ? `Place ${match.placement}` : escapeHtml(match.result_kind)}</span>
          </div>
          <div>
            <strong>${match.final_score ?? (match.team?.score ?? '—')}</strong>
            <span>${match.correct_count ?? (match.team?.correct_count ?? 'Shared result')}</span>
          </div>
        </article>
      `;
    }).join('');

    const ratingHistoryRows = state.ratingHistory.map(entry => `<article class="rating-history-row"><div><strong>${escapeHtml(entry.mode.replaceAll('_', ' '))} · ${escapeHtml(entry.scope === 'global' ? 'Global' : entry.scope.replace('mode:', 'Mode: ').replaceAll('_', ' '))}</strong><span>${escapeHtml(new Date(entry.created_at).toLocaleString())}</span></div><div><strong class="${entry.rating_delta > 0 ? 'delta-pos' : entry.rating_delta < 0 ? 'delta-neg' : 'delta-zero'}">${entry.rating_delta > 0 ? '+' : ''}${entry.rating_delta}</strong><span>${entry.prior_rating} → ${entry.new_rating}</span></div></article>`).join('');
    const ratingHistoryPages = Math.max(1, Math.ceil(state.ratingHistoryTotal / 20));
    const ratingHistoryHtml = state.ratingHistoryLoading
      ? '<p class="profile-empty" role="status">Loading rating history…</p>'
      : state.ratingHistoryError
        ? `<p class="library-message" role="alert">${escapeHtml(state.ratingHistoryError)} <button class="btn btn-quiet" type="button" data-action="rating-history-retry">Retry</button></p>`
        : ratingHistoryRows
          ? `${ratingHistoryRows}<div class="rankings-pagination"><button class="btn btn-secondary" type="button" data-action="rating-history-prev" ${state.ratingHistoryPage === 0 ? 'disabled' : ''}>Previous</button><span class="microcopy">Page ${state.ratingHistoryPage + 1} of ${ratingHistoryPages}</span><button class="btn btn-secondary" type="button" data-action="rating-history-next" ${state.ratingHistoryPage + 1 >= ratingHistoryPages ? 'disabled' : ''}>Next</button></div>`
          : '<p class="profile-empty">No rating changes yet. Eligible signed-in matches will appear here.</p>';
    const ratingScopes = [['', 'All scopes'], ['global', 'Global'], ...RANKING_MODES.filter(mode => mode.id !== 'global').map(mode => [`mode:${mode.id}`, mode.name])];
    const ratingScopeOptions = ratingScopes.map(([value, label]) => `<option value="${value}" ${state.ratingHistoryScope === value ? 'selected' : ''}>${escapeHtml(label)}</option>`).join('');

    let h2hModalHtml = '';
    if (state.h2hModalOpen) {
      let modalBody = '';
      if (state.h2hLoading) {
        modalBody = '<div class="loading-state"><span class="loader"></span><p>Loading head-to-head records…</p></div>';
      } else if (state.h2hError) {
        modalBody = `<p class="library-message" role="alert">${escapeHtml(state.h2hError)}</p>`;
      } else if (state.h2hData) {
        const d = state.h2hData;
        const p1 = d.player_one;
        const p2 = d.player_two;
        const encounters = (d.recent_encounters || []).map(enc => `
          <div class="h2h-match-row">
            <div>
              <strong>${escapeHtml(formatMode(enc.mode))}</strong>
              <span style="font-size:.76rem;color:#8e90b0;margin-left:.5rem">${new Date(enc.completed_at).toLocaleDateString()}</span>
            </div>
            <div style="font-weight:700;font-family:ui-monospace,monospace">
              <span style="color:${enc.player_one_score > enc.player_two_score ? '#74c69d' : '#fff'}">${enc.player_one_score}</span>
              <span style="color:#8e90b0"> - </span>
              <span style="color:${enc.player_two_score > enc.player_one_score ? '#74c69d' : '#fff'}">${enc.player_two_score}</span>
            </div>
            <div>
              <span class="badge-tourn ${enc.winner_id === p1.id ? 'badge-completed' : enc.winner_id === p2.id ? 'badge-cancelled' : 'badge-draft'}">
                ${enc.winner_id === p1.id ? 'Win' : enc.winner_id === p2.id ? 'Loss' : 'Draw'}
              </span>
            </div>
          </div>
        `).join('');

        modalBody = `
          <div class="h2h-vs-header">
            <div class="h2h-player-box">
              <strong>${escapeHtml(p1.display_name)}</strong>
              <span>Elo ${p1.rating}</span>
            </div>
            <div class="h2h-vs-divider">VS</div>
            <div class="h2h-player-box">
              <strong>${escapeHtml(p2.display_name)}</strong>
              <span>Elo ${p2.rating}</span>
            </div>
          </div>
          <div class="h2h-metrics-grid">
            <article>
              <strong>${d.player_one_wins} - ${d.draws} - ${d.player_two_wins}</strong>
              <span>Record (W - D - L)</span>
            </article>
            <article>
              <strong>${d.player_one_win_pct}% vs ${d.player_two_win_pct}%</strong>
              <span>Win Rate</span>
            </article>
            <article>
              <strong>${d.player_one_avg_score} vs ${d.player_two_avg_score}</strong>
              <span>Average Score</span>
            </article>
            <article>
              <strong>${d.score_difference > 0 ? '+' : ''}${d.score_difference}</strong>
              <span>Score Differential</span>
            </article>
            <article>
              <strong>${d.elo_difference > 0 ? '+' : ''}${d.elo_difference}</strong>
              <span>Rating Differential</span>
            </article>
            <article>
              <strong>${d.total_encounters}</strong>
              <span>Shared Matches</span>
            </article>
          </div>
          <h3 style="font:700 1.1rem 'Space Grotesk',sans-serif;margin:1.2rem 0 .6rem;color:#fff">Shared Encounters</h3>
          <div class="h2h-matches-list">
            ${encounters || '<p class="profile-empty">No shared matches found between these two players yet.</p>'}
          </div>
        `;
      }
      h2hModalHtml = `
        <div class="h2h-modal-backdrop" data-action="h2h-close">
          <div class="h2h-modal" onclick="event.stopPropagation()">
            <div class="h2h-header-bar">
              <h2>Head-to-Head Comparison</h2>
              <button class="btn btn-quiet" type="button" data-action="h2h-close" style="padding:.25rem .6rem">✕ Close</button>
            </div>
            ${modalBody}
          </div>
        </div>
      `;
    }

    const incomingRem = state.incomingRematches || [];
    const outgoingRem = state.outgoingRematches || [];
    let rematchesHtml = '';
    if (incomingRem.length || outgoingRem.length) {
      rematchesHtml = `
        <div class="rematch-section">
          <h3>Active Rematch Challenges</h3>
          ${incomingRem.length ? `
            <div style="margin-bottom:1rem;">
              <strong style="font-size:0.85rem;color:#a3e7fc;">Incoming Challenges:</strong>
              <div class="rematch-grid" style="margin-top:0.5rem;">
                ${incomingRem.map(c => `
                  <div class="rematch-card">
                    <div class="rematch-card-header">
                      <strong>${escapeHtml(c.challenger_name)}</strong>
                      <span class="social-rating-pill">${escapeHtml(c.mode.replaceAll('_', ' '))}</span>
                    </div>
                    <div class="rematch-card-meta">Expires in ${c.seconds_remaining}s</div>
                    <div class="rematch-card-actions">
                      <button class="btn btn-primary" type="button" data-action="accept-rematch" data-challenge-id="${c.challenge_id}">Accept &amp; Play</button>
                      <button class="btn btn-secondary" type="button" data-action="decline-rematch" data-challenge-id="${c.challenge_id}">Decline</button>
                    </div>
                  </div>
                `).join('')}
              </div>
            </div>` : ''}
          ${outgoingRem.length ? `
            <div>
              <strong style="font-size:0.85rem;color:#8587a6;">Outgoing Challenges:</strong>
              <div class="rematch-grid" style="margin-top:0.5rem;">
                ${outgoingRem.map(c => `
                  <div class="rematch-card">
                    <div class="rematch-card-header">
                      <strong>Vs ${escapeHtml(c.opponent_name)}</strong>
                      <span class="social-rating-pill">${escapeHtml(c.mode.replaceAll('_', ' '))}</span>
                    </div>
                    <div class="rematch-card-meta">Waiting for opponent · Expires in ${c.seconds_remaining}s</div>
                    <div class="rematch-card-actions">
                      <button class="btn btn-secondary" type="button" data-action="cancel-rematch" data-challenge-id="${c.challenge_id}">Cancel</button>
                    </div>
                  </div>
                `).join('')}
              </div>
            </div>` : ''}
        </div>
      `;
    }

    app.innerHTML = `
      ${h2hModalHtml}
      <section class="profile-card">
        <div class="section-kicker">PLAYER PROFILE &amp; CAREER HUB</div>
        <h1>${escapeHtml(state.profile.display_name)}</h1>
        <p class="subcopy">Pseudonymous identity · no email or personal details</p>
        ${state.profileNotice ? `<p class="library-message" role="status">${escapeHtml(state.profileNotice)}</p>` : ''}
        <form class="profile-name-form">
          <label class="form-label" for="profile-name-update">Display name</label>
          <input class="field" id="profile-name-update" maxlength="24" required value="${escapeHtml(state.profile.display_name)}">
          <button class="btn btn-secondary" type="button" data-action="profile-update">Save name</button>
        </form>
        ${state.oneTimeCredential ? '<div class="profile-warning" role="status">A new credential is ready. Copy it now; it will not be shown again. If lost, it cannot be recovered.</div><button class="btn btn-secondary" type="button" data-action="profile-copy-credential">Copy one-time credential</button>' : ''}
        <div class="profile-actions">
          <button class="btn btn-secondary" type="button" data-action="profile-rotate">Rotate credential</button>
          <button class="btn btn-quiet" type="button" data-action="profile-logout">Sign out on this tab</button>
          <button class="btn btn-danger" type="button" data-action="profile-delete">Delete profile</button>
        </div>
        ${ratingCard}
        ${careerOverviewHtml}
        ${recentFormHtml}
        <h2>Career Trophy Cabinet</h2>
        ${trophiesHtml}
        ${modeAnalyticsHtml}
        ${h2hSearchHtml}
        <h2>Rating history</h2>
        <label class="form-label" for="rating-history-scope">Filter rating scope</label>
        <select class="field rating-history-scope" id="rating-history-scope">${ratingScopeOptions}</select>
        ${ratingHistoryHtml}
        ${rematchesHtml}
        <h2>Competitive record</h2>
        ${stats.history_available ? `<div class="profile-stats"><article><strong>${stats.completed_matches}</strong><span>Matches</span></article><article><strong>${stats.wins}</strong><span>Wins</span></article><article><strong>${stats.draws}</strong><span>Draws</span></article><article><strong>${stats.losses}</strong><span>Losses</span></article></div>` : '<p class="profile-empty">No persisted match history yet. This is different from a zero win or loss record.</p>'}
        <h2>Match history</h2>
        ${matches || '<p class="profile-empty">No profile-linked completed matches yet. Join or create a match while signed in to add one.</p>'}
        ${state.profileHistoryTotal > state.profileMatches.length ? `<button class="btn btn-secondary" type="button" data-action="profile-more">Load more</button>` : ''}
        <p class="microcopy">Profile deletion revokes credentials and anonymizes this profile in retained match records. Match integrity facts may remain without identifying details.</p>
      </section>
    `;
  }
  const RANKING_MODES = [
    { id: 'global', name: 'Global' },
    { id: 'classic', name: 'Classic' },
    { id: 'speed_challenge', name: 'Speed Challenge' },
    { id: 'elimination', name: 'Elimination' },
    { id: 'quiz_royale', name: 'Quiz Royale' },
    { id: 'power_play', name: 'Power Play' },
    { id: 'bluff_master', name: 'Bluff Master' },
  ];
  const TOURNAMENT_SUPPORTED_MODES = [
    { id: 'classic', name: 'Classic' },
    { id: 'speed_challenge', name: 'Speed Challenge' },
    { id: 'elimination', name: 'Elimination' },
    { id: 'power_play', name: 'Power Play' },
  ];
  const LEAGUE_SUPPORTED_MODES = [
    { id: 'classic', name: 'Classic' },
    { id: 'speed_challenge', name: 'Speed Challenge' },
    { id: 'elimination', name: 'Elimination' },
    { id: 'power_play', name: 'Power Play' },
  ];

  async function refreshTournaments() {
    state.tournamentsLoading = true;
    state.tournamentsError = '';
    render();
    try {
      let url = '/api/tournaments?limit=50';
      if (state.tournamentsFilterStatus) url += `&status=${encodeURIComponent(state.tournamentsFilterStatus)}`;
      if (state.tournamentsFilterMode) url += `&mode=${encodeURIComponent(state.tournamentsFilterMode)}`;
      const res = await fetch(url, { cache: 'no-store' });
      if (!res.ok) throw new Error('Tournaments are temporarily unavailable.');
      const data = await res.json();
      state.tournamentsList = data.tournaments || [];
      state.tournamentsTotal = data.total || 0;

      if (state.selectedTournamentId) {
        await refreshTournamentDetail(state.selectedTournamentId);
      }
    } catch (err) {
      state.tournamentsError = err.message || 'Could not load tournaments.';
    } finally {
      state.tournamentsLoading = false;
      render();
    }
  }

  async function refreshTournamentDetail(tournamentId) {
    state.tournamentLoadingDetail = true;
    try {
      const [detailRes, bracketRes, auditRes] = await Promise.all([
        fetch(`/api/tournaments/${tournamentId}`, { cache: 'no-store' }),
        fetch(`/api/tournaments/${tournamentId}/bracket`, { cache: 'no-store' }),
        fetch(`/api/tournaments/${tournamentId}/audit`, { cache: 'no-store' }),
      ]);
      if (detailRes.ok) state.selectedTournamentDetail = await detailRes.json();
      if (bracketRes.ok) state.selectedTournamentBracket = await bracketRes.json();
      if (auditRes.ok) state.selectedTournamentAudit = await auditRes.json();
    } catch (err) {
      notify(err.message || 'Could not load tournament details.');
    } finally {
      state.tournamentLoadingDetail = false;
    }
  }

  function tournamentsScreen() {
    app.classList.remove('library-shell');
    if (state.selectedTournamentId) {
      tournamentDetailScreen();
      return;
    }
    tournamentListScreen();
  }

  function tournamentBadge(status) {
    const labels = {
      draft: 'Draft',
      registration_open: 'Open Registration',
      registration_closed: 'Registration Closed',
      scheduled: 'Scheduled',
      in_progress: 'In Progress',
      completed: 'Completed',
      cancelled: 'Cancelled',
    };
    const cssClass = {
      draft: 'badge-draft',
      registration_open: 'badge-open',
      registration_closed: 'badge-draft',
      scheduled: 'badge-in-progress',
      in_progress: 'badge-in-progress',
      completed: 'badge-completed',
      cancelled: 'badge-cancelled',
    }[status] || 'badge-draft';
    return `<span class="badge-tourn ${cssClass}">${escapeHtml(labels[status] || status)}</span>`;
  }

  function tournamentListScreen() {
    let content = '';
    if (state.tournamentsLoading && !state.tournamentsList.length) {
      content = '<div class="loading-state"><span class="loader"></span><p>Loading tournaments…</p></div>';
    } else if (state.tournamentsError) {
      content = `<p class="library-message" role="alert">${escapeHtml(state.tournamentsError)}</p><button class="btn btn-secondary" type="button" data-action="tournaments-retry">Retry</button>`;
    } else if (!state.tournamentsList.length) {
      content = '<p class="profile-empty">No tournaments found matching the filters. Create the first tournament!</p>';
    } else {
      const cards = state.tournamentsList.map(t => {
        const canRegister = t.status === 'registration_open' && state.profileCredential;
        return `
          <article class="tournament-card">
            <div>
              <div class="tournament-card-top">
                <h3 class="tournament-card-title">${escapeHtml(t.name)}</h3>
                ${tournamentBadge(t.status)}
              </div>
              ${t.description ? `<p class="subcopy" style="margin:.4rem 0 .6rem;font-size:.85rem;">${escapeHtml(t.description)}</p>` : ''}
              <div class="tournament-card-meta">
                <span>Mode: <strong>${escapeHtml(t.mode.replaceAll('_', ' '))}</strong></span>
                <span>Format: <strong>Single Elim</strong></span>
                <span>Capacity: <strong>${t.registered_count} / ${t.capacity}</strong></span>
                <span>Host: <strong>${escapeHtml(t.owner_display_name)}</strong></span>
              </div>
            </div>
            <div class="tournament-card-actions">
              <button class="btn btn-secondary" type="button" data-action="view-tournament" data-id="${t.id}">View Bracket</button>
              ${canRegister ? `<button class="btn btn-primary" type="button" data-action="register-tournament" data-id="${t.id}">Register</button>` : ''}
            </div>
          </article>
        `;
      }).join('');
      content = `<div class="tournaments-grid">${cards}</div>`;
    }

    const modal = state.showTournamentCreateModal ? `
      <div class="create-modal-card">
        <h2>Create Tournament</h2>
        <form class="profile-form">
          <label class="form-label" for="tourn-name">Tournament Name</label>
          <input class="field" id="tourn-name" maxlength="80" required placeholder="e.g. Saturday Speed Knockout">
          <label class="form-label" for="tourn-mode">Game Mode</label>
          <select class="field" id="tourn-mode">
            ${TOURNAMENT_SUPPORTED_MODES.map(m => `<option value="${m.id}">${m.name}</option>`).join('')}
          </select>
          <label class="form-label" for="tourn-capacity">Player Capacity</label>
          <select class="field" id="tourn-capacity">
            <option value="4">4 Players (2 Rounds)</option>
            <option value="8" selected>8 Players (3 Rounds)</option>
            <option value="16">16 Players (4 Rounds)</option>
            <option value="32">32 Players (5 Rounds)</option>
          </select>
          <label class="form-label" for="tourn-desc">Description (Optional)</label>
          <textarea class="field" id="tourn-desc" maxlength="500" rows="3" placeholder="Tournament rules or notes"></textarea>
          <div style="display:flex;gap:.75rem;margin-top:1rem;">
            <button class="btn btn-primary" type="button" data-action="tournament-submit-create">Create Tournament</button>
            <button class="btn btn-quiet" type="button" data-action="close-tournament-create">Cancel</button>
          </div>
        </form>
      </div>
    ` : '';

    app.innerHTML = `
      <section class="tournaments-card">
        <div class="section-kicker">STAGE 8.3 · COMPETITIVE ARENA</div>
        <div class="tournaments-header">
          <div>
            <h1>Tournament Arena</h1>
            <p class="subcopy">Two-player single-elimination knockout tournaments with deterministic seeding and Elo ratings.</p>
          </div>
          <div>
            ${state.profileCredential
              ? `<button class="btn btn-primary" type="button" data-action="open-tournament-create">Create Tournament</button>`
              : `<button class="btn btn-secondary" type="button" data-action="go-to-profile">Sign in to Compete</button>`}
          </div>
        </div>
        ${modal}
        <div class="tournaments-filter-bar">
          <label for="filter-status" style="font-size:.8rem;color:#a5a6c0;">Status:</label>
          <select class="field" id="filter-status">
            <option value="" ${state.tournamentsFilterStatus === '' ? 'selected' : ''}>All Statuses</option>
            <option value="registration_open" ${state.tournamentsFilterStatus === 'registration_open' ? 'selected' : ''}>Open Registration</option>
            <option value="in_progress" ${state.tournamentsFilterStatus === 'in_progress' ? 'selected' : ''}>In Progress</option>
            <option value="completed" ${state.tournamentsFilterStatus === 'completed' ? 'selected' : ''}>Completed</option>
            <option value="draft" ${state.tournamentsFilterStatus === 'draft' ? 'selected' : ''}>Draft</option>
          </select>
          <label for="filter-mode" style="font-size:.8rem;color:#a5a6c0;">Mode:</label>
          <select class="field" id="filter-mode">
            <option value="" ${state.tournamentsFilterMode === '' ? 'selected' : ''}>All Modes</option>
            ${TOURNAMENT_SUPPORTED_MODES.map(m => `<option value="${m.id}" ${state.tournamentsFilterMode === m.id ? 'selected' : ''}>${m.name}</option>`).join('')}
          </select>
          <span style="font-size:.8rem;color:#8587a8;margin-left:auto;">${state.tournamentsTotal} Tournaments</span>
        </div>
        ${content}
      </section>
    `;
  }

  function tournamentDetailScreen() {
    const t = state.selectedTournamentDetail;
    const bracket = state.selectedTournamentBracket;
    const audit = state.selectedTournamentAudit || [];

    if (!t) {
      app.innerHTML = '<section class="tournaments-card"><div class="loading-state"><span class="loader"></span><p>Loading tournament details…</p></div></section>';
      return;
    }

    const myId = state.profile?.id;
    const isOwner = myId && myId === t.owner_player_id;
    const participants = bracket?.participants || [];
    const isRegistered = myId && participants.some(p => p.player_id === myId && p.status !== 'withdrawn');

    let ownerActions = '';
    if (isOwner) {
      if (t.status === 'draft') {
        ownerActions += `<button class="btn btn-primary" type="button" data-action="open-tournament-reg" data-id="${t.id}">Open Registration</button>`;
        ownerActions += `<button class="btn btn-danger" type="button" data-action="cancel-tournament" data-id="${t.id}">Cancel</button>`;
      } else if (t.status === 'registration_open') {
        ownerActions += `<button class="btn btn-primary" type="button" data-action="start-tournament" data-id="${t.id}">Start Tournament</button>`;
        ownerActions += `<button class="btn btn-secondary" type="button" data-action="close-tournament-reg" data-id="${t.id}">Close Registration</button>`;
        ownerActions += `<button class="btn btn-danger" type="button" data-action="cancel-tournament" data-id="${t.id}">Cancel</button>`;
      } else if (t.status === 'registration_closed') {
        ownerActions += `<button class="btn btn-primary" type="button" data-action="start-tournament" data-id="${t.id}">Start Tournament</button>`;
        ownerActions += `<button class="btn btn-danger" type="button" data-action="cancel-tournament" data-id="${t.id}">Cancel</button>`;
      } else if (t.status === 'in_progress') {
        ownerActions += `<button class="btn btn-danger" type="button" data-action="cancel-tournament" data-id="${t.id}">Cancel</button>`;
      }
    }

    let playerActions = '';
    if (t.status === 'registration_open' && state.profileCredential) {
      if (isRegistered) {
        playerActions += `<button class="btn btn-danger" type="button" data-action="withdraw-tournament" data-id="${t.id}">Withdraw</button>`;
      } else {
        playerActions += `<button class="btn btn-primary" type="button" data-action="register-tournament" data-id="${t.id}">Register to Play</button>`;
      }
    }

    // Bracket rounds HTML
    let bracketHtml = '';
    if (bracket?.rounds?.length) {
      const roundsCols = bracket.rounds.map(r => {
        const matchesHtml = r.matches.map(m => {
          const isP1 = myId && m.player_one?.id === myId;
          const isP2 = myId && m.player_two?.id === myId;
          const amParticipant = isP1 || isP2;
          const isP1Winner = m.winner && m.player_one && m.winner.id === m.player_one.id;
          const isP2Winner = m.winner && m.player_two && m.winner.id === m.player_two.id;

          let matchAction = '';
          if (['scheduled', 'in_progress'].includes(m.status)) {
            if (amParticipant) {
              matchAction = `
                <div class="match-footer-action">
                  <button class="btn btn-primary" style="padding:.3rem .6rem;font-size:.78rem;" type="button" data-action="launch-tournament-match" data-tourn-id="${t.id}" data-match-id="${m.id}">Play Match</button>
                  <button class="btn btn-quiet" style="padding:.3rem .5rem;font-size:.72rem;" type="button" data-action="forfeit-tournament-match" data-tourn-id="${t.id}" data-match-id="${m.id}">Forfeit</button>
                  <button class="btn btn-quiet" style="padding:.3rem .5rem;font-size:.72rem;" type="button" data-action="dispute-tournament-match" data-tourn-id="${t.id}" data-match-id="${m.id}">Dispute</button>
                </div>
              `;
            } else {
              matchAction = `<div class="match-footer-action" style="font-size:.75rem;color:#a5a6c0;">${m.status === 'in_progress' ? 'Match In Progress' : 'Ready'}</div>`;
            }
          } else if (m.status === 'completed') {
            matchAction = `<div class="match-footer-action" style="font-size:.75rem;color:#53c69f;">Completed · Winner: ${escapeHtml(m.winner?.display_name || 'Winner')}</div>`;
          } else if (m.status === 'bye') {
            matchAction = `<div class="match-footer-action" style="font-size:.75rem;color:#d2cffa;">Bye Advancement</div>`;
          } else if (m.status === 'forfeited') {
            matchAction = `<div class="match-footer-action" style="font-size:.75rem;color:#ff9ab0;">Forfeited (${escapeHtml(m.result_reason || '')})</div>`;
          } else if (m.status === 'disputed') {
            matchAction = `<div class="match-footer-action" style="font-size:.75rem;color:#ffe2a0;">Disputed (${escapeHtml(m.result_reason || '')})</div>`;
          }

          return `
            <div class="bracket-match-node ${m.status === 'in_progress' ? 'match-in-progress' : m.status === 'completed' ? 'match-completed' : ''}">
              <div class="match-header-bar">
                <span>Match ${m.match_number}</span>
                <span>${escapeHtml(m.status)}</span>
              </div>
              <div class="match-slot ${isP1Winner ? 'winner' : ''}">
                <div class="match-slot-name">
                  <span class="seed-chip">1</span>
                  <span>${escapeHtml(m.player_one?.display_name || (m.status === 'pending' ? 'TBD' : 'None'))}</span>
                </div>
                ${isP1Winner ? '<span>🏆</span>' : ''}
              </div>
              <div class="match-slot ${isP2Winner ? 'winner' : ''}">
                <div class="match-slot-name">
                  <span class="seed-chip">2</span>
                  <span>${escapeHtml(m.player_two?.display_name || (m.status === 'pending' ? 'TBD' : m.status === 'bye' ? 'BYE' : 'None'))}</span>
                </div>
                ${isP2Winner ? '<span>🏆</span>' : ''}
              </div>
              ${matchAction}
            </div>
          `;
        }).join('');

        return `
          <div class="bracket-round-column">
            <div class="bracket-round-header">Round ${r.round_number} (${escapeHtml(r.status)})</div>
            <div class="bracket-round-matches">${matchesHtml}</div>
          </div>
        `;
      }).join('');

      bracketHtml = `
        <h2>Knockout Bracket</h2>
        <div class="bracket-scroll-container">
          <div class="bracket-rounds-track">${roundsCols}</div>
        </div>
      `;
    } else {
      bracketHtml = `<p class="profile-empty" style="margin:2rem 0;">Bracket will be generated once registration closes and the tournament begins.</p>`;
    }

    // Participants list
    const partsRows = participants.map(p => `
      <tr class="${p.player_id === myId ? 'self-row' : ''}">
        <td><strong>#${p.seed ?? '—'}</strong></td>
        <td>${escapeHtml(p.display_name)}</td>
        <td><span class="badge-status ${p.status === 'champion' ? 'badge-established' : p.status === 'runner_up' ? 'badge-provisional' : ''}">${escapeHtml(p.status)}</span></td>
        <td>${p.final_placement ? `#${p.final_placement}` : '—'}</td>
      </tr>
    `).join('');

    // Audit trail
    const auditRows = audit.map(ev => `
      <div class="audit-row">
        <span>${new Date(ev.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
        <div><strong class="audit-action">${escapeHtml(ev.action)}</strong>: ${escapeHtml(ev.reason || '')}</div>
        <span>${escapeHtml(ev.actor_player_id ? 'Player' : 'System')}</span>
      </div>
    `).join('');

    app.innerHTML = `
      <section class="tournaments-card">
        <button class="btn btn-quiet" type="button" data-action="back-to-tournaments" style="margin-bottom:1rem;">← Back to All Tournaments</button>
        <div class="tournament-detail-banner">
          <div>
            <div style="display:flex;align-items:center;gap:.75rem;">
              <h1 style="margin:0;">${escapeHtml(t.name)}</h1>
              ${tournamentBadge(t.status)}
            </div>
            ${t.description ? `<p style="margin:.4rem 0;color:#c0c1da;">${escapeHtml(t.description)}</p>` : ''}
            <div class="tournament-detail-meta">
              <span>Mode: <strong>${escapeHtml(t.mode.replaceAll('_', ' '))}</strong></span>
              <span>Format: <strong>Single Elimination</strong></span>
              <span>Capacity: <strong>${t.registered_count} / ${t.capacity} Players</strong></span>
              <span>Host: <strong>${escapeHtml(t.owner_display_name)}</strong></span>
              <span>Rules: <strong>${escapeHtml(t.rules_version)}</strong></span>
            </div>
          </div>
          <div class="tournament-detail-actions">
            ${ownerActions}
            ${playerActions}
          </div>
        </div>
        ${bracketHtml}
        <h2>Registered Participants (${participants.length})</h2>
        <table class="rankings-table" style="margin-bottom:2rem;">
          <thead>
            <tr>
              <th style="width:4rem;">Seed</th>
              <th>Player</th>
              <th>Status</th>
              <th>Placement</th>
            </tr>
          </thead>
          <tbody>
            ${partsRows || '<tr><td colspan="4" style="text-align:center;">No players registered yet.</td></tr>'}
          </tbody>
        </table>
        <h2>Audit Trail</h2>
        <div class="audit-list">
          ${auditRows || '<p class="subcopy">No audit events recorded yet.</p>'}
        </div>
      </section>
    `;
  }

  async function refreshLeagues() {
    state.leaguesLoading = true;
    state.leaguesError = '';
    render();
    try {
      let url = '/api/leagues?limit=50';
      if (state.leaguesFilterStatus) url += `&status=${encodeURIComponent(state.leaguesFilterStatus)}`;
      if (state.leaguesFilterMode) url += `&mode=${encodeURIComponent(state.leaguesFilterMode)}`;
      const res = await fetch(url, { cache: 'no-store' });
      if (!res.ok) throw new Error('Leagues are temporarily unavailable.');
      const data = await res.json();
      state.leaguesList = data.leagues || [];
      state.leaguesTotal = data.total || 0;

      if (state.selectedLeagueId) {
        await refreshLeagueDetail(state.selectedLeagueId);
        if (state.selectedSeasonId) {
          await refreshSeasonDetail(state.selectedLeagueId, state.selectedSeasonId);
        }
      }
    } catch (err) {
      state.leaguesError = err.message || 'Could not load leagues.';
    } finally {
      state.leaguesLoading = false;
      render();
    }
  }

  async function refreshLeagueDetail(leagueId) {
    state.leagueLoadingDetail = true;
    try {
      const res = await fetch(`/api/leagues/${leagueId}`, { cache: 'no-store' });
      if (res.ok) state.selectedLeagueDetail = await res.json();
    } catch (err) {
      notify(err.message || 'Could not load league details.');
    } finally {
      state.leagueLoadingDetail = false;
    }
  }

  async function refreshSeasonDetail(leagueId, seasonId) {
    state.leagueLoadingDetail = true;
    try {
      const [seasonRes, auditRes] = await Promise.all([
        fetch(`/api/leagues/${leagueId}/seasons/${seasonId}`, { cache: 'no-store' }),
        fetch(`/api/leagues/${leagueId}/seasons/${seasonId}/audit`, { cache: 'no-store' }),
      ]);
      if (seasonRes.ok) state.selectedSeasonDetail = await seasonRes.json();
      if (auditRes.ok) state.selectedSeasonAudit = await auditRes.json();
    } catch (err) {
      notify(err.message || 'Could not load season details.');
    } finally {
      state.leagueLoadingDetail = false;
    }
  }

  function leaguesScreen() {
    app.classList.remove('library-shell');
    if (state.selectedSeasonId) {
      seasonDetailScreen();
      return;
    }
    if (state.selectedLeagueId) {
      leagueDetailScreen();
      return;
    }
    leagueListScreen();
  }

  function leagueBadge(status) {
    const labels = {
      active: 'Active',
      archived: 'Archived',
      draft: 'Draft',
      registration_open: 'Open Registration',
      registration_closed: 'Registration Closed',
      scheduled: 'Scheduled',
      in_progress: 'In Progress',
      completed: 'Completed',
      cancelled: 'Cancelled',
    };
    const cssClass = {
      active: 'badge-open',
      archived: 'badge-draft',
      draft: 'badge-draft',
      registration_open: 'badge-open',
      registration_closed: 'badge-draft',
      scheduled: 'badge-in-progress',
      in_progress: 'badge-in-progress',
      completed: 'badge-completed',
      cancelled: 'badge-cancelled',
    }[status] || 'badge-draft';
    return `<span class="badge-tourn ${cssClass}">${escapeHtml(labels[status] || status)}</span>`;
  }

  function leagueListScreen() {
    let content = '';
    if (state.leaguesLoading && !state.leaguesList.length) {
      content = '<div class="loading-state"><span class="loader"></span><p>Loading leagues…</p></div>';
    } else if (state.leaguesError) {
      content = `<p class="library-message" role="alert">${escapeHtml(state.leaguesError)}</p><button class="btn btn-secondary" type="button" data-action="leagues-retry">Retry</button>`;
    } else if (!state.leaguesList.length) {
      content = '<p class="profile-empty">No leagues found matching the filters. Create the first competitive league!</p>';
    } else {
      const cards = state.leaguesList.map(l => `
        <article class="league-card">
          <div>
            <div class="league-card-top">
              <h3 class="league-card-title">${escapeHtml(l.name)}</h3>
              ${leagueBadge(l.status)}
            </div>
            ${l.description ? `<p class="subcopy" style="margin:.4rem 0 .6rem;font-size:.85rem;">${escapeHtml(l.description)}</p>` : ''}
            <div class="tournament-card-meta">
              <span>Mode: <strong>${escapeHtml(l.mode.replaceAll('_', ' '))}</strong></span>
              <span>Format: <strong>Round-Robin</strong></span>
              <span>Seasons: <strong>${l.seasons_count}</strong></span>
              <span>Host: <strong>${escapeHtml(l.owner_display_name)}</strong></span>
            </div>
          </div>
          <div class="tournament-card-actions">
            <button class="btn btn-primary" type="button" data-action="view-league" data-id="${l.id}">View League</button>
          </div>
        </article>
      `).join('');
      content = `<div class="leagues-grid">${cards}</div>`;
    }

    const modal = state.showLeagueCreateModal ? `
      <div class="create-modal-card">
        <h2>Create Competitive League</h2>
        <form class="profile-form">
          <label class="form-label" for="league-name">League Name</label>
          <input class="field" id="league-name" maxlength="80" required placeholder="e.g. Premier Classic League">
          <label class="form-label" for="league-mode">Game Mode</label>
          <select class="field" id="league-mode">
            ${LEAGUE_SUPPORTED_MODES.map(m => `<option value="${m.id}">${m.name}</option>`).join('')}
          </select>
          <label class="form-label" for="league-desc">Description (Optional)</label>
          <textarea class="field" id="league-desc" maxlength="500" rows="3" placeholder="League rules, schedule overview, or notes"></textarea>
          <div style="display:flex;gap:.75rem;margin-top:1rem;">
            <button class="btn btn-primary" type="button" data-action="league-submit-create">Create League</button>
            <button class="btn btn-quiet" type="button" data-action="close-league-create">Cancel</button>
          </div>
        </form>
      </div>
    ` : '';

    app.innerHTML = `
      <section class="leagues-card">
        <div class="section-kicker">STAGE 8.4 · SEASONAL LEAGUES</div>
        <div class="leagues-header">
          <div>
            <h1>Competitive Leagues</h1>
            <p class="subcopy">Seasonal round-robin leagues with deterministic scheduling, Elo updates, and comprehensive standings.</p>
          </div>
          <div>
            ${state.profileCredential
              ? `<button class="btn btn-primary" type="button" data-action="open-league-create">Create League</button>`
              : `<button class="btn btn-secondary" type="button" data-action="go-to-profile">Sign in to Compete</button>`}
          </div>
        </div>
        ${modal}
        <div class="leagues-filter-bar">
          <label for="filter-league-status" style="font-size:.8rem;color:#a5a6c0;">Status:</label>
          <select class="field" id="filter-league-status">
            <option value="" ${state.leaguesFilterStatus === '' ? 'selected' : ''}>All Statuses</option>
            <option value="active" ${state.leaguesFilterStatus === 'active' ? 'selected' : ''}>Active</option>
            <option value="archived" ${state.leaguesFilterStatus === 'archived' ? 'selected' : ''}>Archived</option>
          </select>
          <label for="filter-league-mode" style="font-size:.8rem;color:#a5a6c0;">Mode:</label>
          <select class="field" id="filter-league-mode">
            <option value="" ${state.leaguesFilterMode === '' ? 'selected' : ''}>All Modes</option>
            ${LEAGUE_SUPPORTED_MODES.map(m => `<option value="${m.id}" ${state.leaguesFilterMode === m.id ? 'selected' : ''}>${m.name}</option>`).join('')}
          </select>
          <span style="font-size:.8rem;color:#8587a8;margin-left:auto;">${state.leaguesTotal} Leagues</span>
        </div>
        ${content}
      </section>
    `;
  }

  function leagueDetailScreen() {
    const l = state.selectedLeagueDetail;
    if (!l) {
      app.innerHTML = '<section class="leagues-card"><div class="loading-state"><span class="loader"></span><p>Loading league details…</p></div></section>';
      return;
    }

    const myId = state.profile?.id;
    const isOwner = myId && myId === l.owner_player_id;

    const seasonsCards = (l.seasons || []).map(s => `
      <article class="league-card">
        <div>
          <div class="league-card-top">
            <h3 class="league-card-title">Season ${s.season_number}: ${escapeHtml(s.name)}</h3>
            ${leagueBadge(s.status)}
          </div>
          <div class="tournament-card-meta" style="margin-top:.75rem;">
            <span>Capacity: <strong>${s.capacity} Players</strong></span>
            <span>Status: <strong>${escapeHtml(s.status.replaceAll('_', ' '))}</strong></span>
          </div>
        </div>
        <div class="tournament-card-actions">
          <button class="btn btn-primary" type="button" data-action="view-season" data-league-id="${l.id}" data-season-id="${s.id}">View Season &amp; Standings</button>
        </div>
      </article>
    `).join('');

    const modal = state.showSeasonCreateModal ? `
      <div class="create-modal-card">
        <h2>Create Season</h2>
        <form class="profile-form">
          <label class="form-label" for="season-name">Season Name</label>
          <input class="field" id="season-name" maxlength="80" required placeholder="e.g. Inaugural Season">
          <label class="form-label" for="season-capacity">Player Limit (Max 32)</label>
          <select class="field" id="season-capacity">
            <option value="4">4 Players (3 Rounds)</option>
            <option value="8">8 Players (7 Rounds)</option>
            <option value="16" selected>16 Players (15 Rounds)</option>
            <option value="32">32 Players (31 Rounds)</option>
          </select>
          <div style="display:flex;gap:.75rem;margin-top:1rem;">
            <button class="btn btn-primary" type="button" data-action="season-submit-create">Create Season</button>
            <button class="btn btn-quiet" type="button" data-action="close-season-create">Cancel</button>
          </div>
        </form>
      </div>
    ` : '';

    app.innerHTML = `
      <section class="leagues-card">
        <button class="btn btn-quiet" type="button" data-action="back-to-leagues" style="margin-bottom:1rem;">← Back to All Leagues</button>
        <div class="league-detail-banner">
          <div>
            <div style="display:flex;align-items:center;gap:.75rem;">
              <h1 style="margin:0;">${escapeHtml(l.name)}</h1>
              ${leagueBadge(l.status)}
            </div>
            ${l.description ? `<p style="margin:.4rem 0;color:#c0c1da;">${escapeHtml(l.description)}</p>` : ''}
            <div class="tournament-detail-meta">
              <span>Mode: <strong>${escapeHtml(l.mode.replaceAll('_', ' '))}</strong></span>
              <span>Host: <strong>${escapeHtml(l.owner_display_name)}</strong></span>
              <span>Total Seasons: <strong>${l.seasons_count}</strong></span>
              <span>Rules: <strong>${escapeHtml(l.rules_version)}</strong></span>
            </div>
          </div>
          <div>
            ${isOwner ? `<button class="btn btn-primary" type="button" data-action="open-season-create">New Season</button>` : ''}
          </div>
        </div>
        ${modal}
        <h2>League Seasons (${l.seasons?.length || 0})</h2>
        <div class="leagues-grid" style="margin-top:1rem;">
          ${seasonsCards || '<p class="profile-empty">No seasons have been created yet. The league host can launch Season 1!</p>'}
        </div>
      </section>
    `;
  }

  function seasonDetailScreen() {
    const s = state.selectedSeasonDetail;
    const audit = state.selectedSeasonAudit || [];
    if (!s) {
      app.innerHTML = '<section class="leagues-card"><div class="loading-state"><span class="loader"></span><p>Loading season details…</p></div></section>';
      return;
    }

    const myId = state.profile?.id;
    const isOwner = myId && myId === s.owner_player_id;
    const participants = s.participants || [];
    const isRegistered = myId && participants.some(p => p.player_id === myId && p.status !== 'withdrawn');

    let ownerActions = '';
    if (isOwner) {
      if (s.status === 'draft') {
        ownerActions += `<button class="btn btn-primary" type="button" data-action="open-season-reg" data-league-id="${s.league_id}" data-season-id="${s.id}">Open Registration</button>`;
        ownerActions += `<button class="btn btn-danger" type="button" data-action="cancel-season" data-league-id="${s.league_id}" data-season-id="${s.id}">Cancel</button>`;
      } else if (s.status === 'registration_open') {
        ownerActions += `<button class="btn btn-primary" type="button" data-action="start-season" data-league-id="${s.league_id}" data-season-id="${s.id}">Start Season</button>`;
        ownerActions += `<button class="btn btn-secondary" type="button" data-action="close-season-reg" data-league-id="${s.league_id}" data-season-id="${s.id}">Close Registration</button>`;
        ownerActions += `<button class="btn btn-danger" type="button" data-action="cancel-season" data-league-id="${s.league_id}" data-season-id="${s.id}">Cancel</button>`;
      } else if (s.status === 'registration_closed') {
        ownerActions += `<button class="btn btn-primary" type="button" data-action="start-season" data-league-id="${s.league_id}" data-season-id="${s.id}">Start Season</button>`;
        ownerActions += `<button class="btn btn-danger" type="button" data-action="cancel-season" data-league-id="${s.league_id}" data-season-id="${s.id}">Cancel</button>`;
      } else if (s.status === 'in_progress') {
        ownerActions += `<button class="btn btn-danger" type="button" data-action="cancel-season" data-league-id="${s.league_id}" data-season-id="${s.id}">Cancel</button>`;
      }
    }

    let playerActions = '';
    if (s.status === 'registration_open' && state.profileCredential) {
      if (isRegistered) {
        playerActions += `<button class="btn btn-danger" type="button" data-action="withdraw-season" data-league-id="${s.league_id}" data-season-id="${s.id}">Withdraw</button>`;
      } else {
        playerActions += `<button class="btn btn-primary" type="button" data-action="register-season" data-league-id="${s.league_id}" data-season-id="${s.id}">Register to Play</button>`;
      }
    }

    // Standings table
    const standings = s.standings || [];
    const standingsRows = standings.map(row => {
      const isSelf = myId && row.player_id === myId;
      const isChampion = s.status === 'completed' && row.rank === 1;
      return `
        <tr class="${isSelf ? 'self-row' : ''} ${isChampion ? 'champion-row' : ''}">
          <td><strong>#${row.rank}</strong></td>
          <td><strong>${escapeHtml(row.display_name)}</strong>${isSelf ? ' <small class="brand-light">(You)</small>' : ''}${isChampion ? ' 🏆' : ''}</td>
          <td>${row.matches_played}</td>
          <td>${row.wins}</td>
          <td>${row.draws}</td>
          <td>${row.losses}</td>
          <td>${row.byes}</td>
          <td><strong>${row.points}</strong></td>
          <td>${row.score_for}</td>
          <td>${row.score_against}</td>
          <td>${row.score_diff > 0 ? '+' : ''}${row.score_diff}</td>
        </tr>
      `;
    }).join('');

    // Rounds & Fixtures schedule
    let scheduleHtml = '';
    if (s.rounds?.length) {
      scheduleHtml = s.rounds.map(r => {
        const fixturesHtml = (r.fixtures || []).map(f => {
          const isP1 = myId && f.player_one_id === myId;
          const isP2 = myId && f.player_two_id === myId;
          const amParticipant = isP1 || isP2;
          const isP1Winner = f.winner_player_id && f.winner_player_id === f.player_one_id;
          const isP2Winner = f.winner_player_id && f.winner_player_id === f.player_two_id;

          let fixtureAction = '';
          if (['scheduled', 'in_progress'].includes(f.status)) {
            if (amParticipant) {
              fixtureAction = `
                <div class="fixture-actions-bar">
                  <button class="btn btn-primary" style="padding:.25rem .55rem;font-size:.78rem;" type="button" data-action="launch-season-fixture" data-league-id="${s.league_id}" data-season-id="${s.id}" data-fixture-id="${f.id}">Play Match</button>
                  <button class="btn btn-quiet" style="padding:.25rem .45rem;font-size:.72rem;" type="button" data-action="forfeit-season-fixture" data-league-id="${s.league_id}" data-season-id="${s.id}" data-fixture-id="${f.id}">Forfeit</button>
                  <button class="btn btn-quiet" style="padding:.25rem .45rem;font-size:.72rem;" type="button" data-action="dispute-season-fixture" data-league-id="${s.league_id}" data-season-id="${s.id}" data-fixture-id="${f.id}">Dispute</button>
                </div>
              `;
            } else {
              fixtureAction = `<div class="fixture-actions-bar" style="font-size:.75rem;color:#a5a6c0;">${f.status === 'in_progress' ? 'In Progress' : 'Scheduled'}</div>`;
            }
          } else if (f.status === 'bye') {
            fixtureAction = `<div class="fixture-actions-bar" style="font-size:.75rem;color:#d2cffa;">Scheduled Bye</div>`;
          } else if (f.status === 'completed') {
            fixtureAction = `<div class="fixture-actions-bar" style="font-size:.75rem;color:#53c69f;">${f.is_draw ? 'Match Draw (1 pt each)' : `Winner: ${escapeHtml(f.winner_name || 'Winner')}`}</div>`;
          } else if (f.status === 'forfeited') {
            fixtureAction = `<div class="fixture-actions-bar" style="font-size:.75rem;color:#ff9ab0;">Forfeited (${escapeHtml(f.result_reason || '')})</div>`;
          } else if (f.status === 'disputed') {
            fixtureAction = `<div class="fixture-actions-bar" style="font-size:.75rem;color:#ffe2a0;">Disputed (${escapeHtml(f.result_reason || '')})</div>`;
          }

          return `
            <div class="fixture-card">
              <div class="fixture-header">
                <span>Match ${f.match_number}</span>
                <span>${escapeHtml(f.status.toUpperCase())}</span>
              </div>
              <div class="fixture-player-row ${isP1Winner ? 'winner' : f.is_draw ? 'draw' : ''}">
                <span>${escapeHtml(f.player_one_name || 'Player 1')}</span>
                <span class="fixture-score">${f.player_one_score !== null ? f.player_one_score : '—'}</span>
              </div>
              <div class="fixture-player-row ${isP2Winner ? 'winner' : f.is_draw ? 'draw' : ''}">
                <span>${escapeHtml(f.player_two_name || (f.status === 'bye' ? 'BYE' : 'Player 2'))}</span>
                <span class="fixture-score">${f.player_two_score !== null ? f.player_two_score : '—'}</span>
              </div>
              ${fixtureAction}
            </div>
          `;
        }).join('');

        return `
          <div class="round-section">
            <h3 class="round-section-title">Round ${r.round_number} (${escapeHtml(r.status.replaceAll('_', ' '))})</h3>
            <div class="fixtures-grid">${fixturesHtml}</div>
          </div>
        `;
      }).join('');
    } else {
      scheduleHtml = '<p class="profile-empty" style="margin:2rem 0;">Round-robin schedule will be generated once registration closes and the season begins.</p>';
    }

    // Audit trail
    const auditRows = audit.map(ev => `
      <div class="audit-row">
        <span>${new Date(ev.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
        <div><strong class="audit-action">${escapeHtml(ev.action)}</strong>: ${escapeHtml(ev.reason || '')}</div>
        <span>${escapeHtml(ev.actor_player_id ? 'Player' : 'System')}</span>
      </div>
    `).join('');

    app.innerHTML = `
      <section class="leagues-card">
        <button class="btn btn-quiet" type="button" data-action="back-to-league" data-league-id="${s.league_id}" style="margin-bottom:1rem;">← Back to ${escapeHtml(s.league_name)}</button>
        <div class="league-detail-banner">
          <div>
            <div style="display:flex;align-items:center;gap:.75rem;">
              <h1 style="margin:0;">Season ${s.season_number}: ${escapeHtml(s.name)}</h1>
              ${leagueBadge(s.status)}
            </div>
            <div class="tournament-detail-meta" style="margin-top:.4rem;">
              <span>League: <strong>${escapeHtml(s.league_name)}</strong></span>
              <span>Mode: <strong>${escapeHtml(s.league_mode.replaceAll('_', ' '))}</strong></span>
              <span>Capacity: <strong>${s.registered_count} / ${s.capacity} Players</strong></span>
              <span>Rules: <strong>${escapeHtml(s.rules_version)}</strong></span>
            </div>
          </div>
          <div class="tournament-detail-actions">
            ${ownerActions}
            ${playerActions}
          </div>
        </div>

        <h2>Season Standings</h2>
        <p class="subcopy" style="font-size:.82rem;margin-bottom:.6rem;">Points system: Win = 3 pts, Draw = 1 pt, Loss = 0 pts, Bye = 0 pts. Tie-breakers: Points &gt; Head-to-Head &gt; Score Difference &gt; Wins &gt; Player ID.</p>
        <div class="standings-table-container">
          <table class="standings-table">
            <thead>
              <tr>
                <th style="width:3.5rem;">Rank</th>
                <th>Player</th>
                <th>MP</th>
                <th>W</th>
                <th>D</th>
                <th>L</th>
                <th>Bye</th>
                <th>PTS</th>
                <th>SF</th>
                <th>SA</th>
                <th>SD</th>
              </tr>
            </thead>
            <tbody>
              ${standingsRows || '<tr><td colspan="11" style="text-align:center;">No participants yet.</td></tr>'}
            </tbody>
          </table>
        </div>

        <h2>Round-Robin Schedule</h2>
        <div class="fixtures-rounds-track">${scheduleHtml}</div>

        <h2>Audit Trail</h2>
        <div class="audit-list">
          ${auditRows || '<p class="subcopy">No audit events recorded yet.</p>'}
        </div>
      </section>
    `;
  }

  function rankingsScreen() {
    app.classList.remove('library-shell');
    if (state.rankingsLoading && !state.rankingsData) {
      app.innerHTML = '<section class="rankings-card"><div class="loading-state"><span class="loader"></span><p>Loading competitive rankings…</p></div></section>';
      return;
    }
    const activeScope = state.rankingsScope;
    const activeMode = state.rankingsMode;
    const scopesNav = RANKING_MODES.map(m => {
      const isGlobal = m.id === 'global';
      const active = isGlobal ? activeScope === 'global' : (activeScope === 'mode' && activeMode === m.id);
      return `<button class="btn btn-scope ${active ? 'active' : ''}" type="button" data-action="rankings-scope" data-scope="${m.id}">${escapeHtml(m.name)}</button>`;
    }).join('');

    const myRatingObj = state.myRatings ? (
      activeScope === 'global' ? state.myRatings.global_rating : state.myRatings.mode_ratings?.[activeMode]
    ) : null;

    let userBanner = '';
    if (state.profile) {
      if (myRatingObj) {
        userBanner = `<div class="user-rating-banner"><div class="user-rating-value">${myRatingObj.rating}</div><div class="user-rating-meta"><strong>Your ${activeScope === 'global' ? 'Global' : escapeHtml(activeMode.replaceAll('_', ' '))} Rating &nbsp;${myRatingObj.is_provisional ? '<span class="badge-status badge-provisional">PROVISIONAL</span>' : '<span class="badge-status badge-established">ESTABLISHED</span>'}</strong><span>${myRatingObj.rated_match_count} rated matches · ${myRatingObj.wins} Wins · ${myRatingObj.draws} Draws · ${myRatingObj.losses} Losses</span></div>${state.myRatings.recent_changes?.length ? `<div class="recent-changes-pills" title="Recent rating movements">${state.myRatings.recent_changes.slice(0, 5).map(c => `<span class="delta-pill ${c.rating_delta > 0 ? 'delta-pos' : c.rating_delta < 0 ? 'delta-neg' : 'delta-zero'}">${c.rating_delta > 0 ? '+' : ''}${c.rating_delta}</span>`).join('')}</div>` : ''}</div>`;
      } else {
        userBanner = `<div class="user-rating-banner"><div class="user-rating-value">—</div><div class="user-rating-meta"><strong>Your ${activeScope === 'global' ? 'Global' : escapeHtml(activeMode.replaceAll('_', ' '))} Rating</strong><span>No rated matches in this category yet. Complete an eligible competitive match to establish your ranking.</span></div></div>`;
      }
    }

    const data = state.rankingsData || { total: 0, established_total: 0, provisional_total: 0, rankings: [], has_more: false };
    const rankings = data.rankings || [];
    const establishedRows = rankings.filter(r => !r.is_provisional);
    const provisionalRows = rankings.filter(r => r.is_provisional);

    function renderTable(rows) {
      if (!rows.length) return '';
      return `<table class="rankings-table" aria-label="Competitive rankings table"><thead><tr><th scope="col" class="rank-col">Rank</th><th scope="col">Player</th><th scope="col" class="rating-col">Rating</th><th scope="col">Record (W-D-L)</th><th scope="col">Matches</th><th scope="col">Status</th></tr></thead><tbody>${rows.map(entry => {
        const isSelf = state.profile && entry.player_id === state.profile.id;
        return `<tr class="${isSelf ? 'self-row' : ''}"><td class="rank-col">#${entry.rank}</td><td><strong>${escapeHtml(entry.display_name)}</strong>${isSelf ? ' <small class="brand-light">(You)</small>' : ''}</td><td class="rating-col">${entry.rating}</td><td>${entry.wins}W - ${entry.draws}D - ${entry.losses}L</td><td>${entry.rated_match_count}</td><td>${entry.is_provisional ? '<span class="badge-status badge-provisional">PROVISIONAL</span>' : '<span class="badge-status badge-established">ESTABLISHED</span>'}</td></tr>`;
      }).join('')}</tbody></table>`;
    }

    let tablesHtml = '';
    if (rankings.length === 0) {
      tablesHtml = '<p class="profile-empty">No ranked players in this category yet. Join or host an eligible multiplayer match while signed in to claim the top spot!</p>';
    } else {
      if (establishedRows.length > 0) {
        tablesHtml += `<h2 class="section-subtitle">Established Leaderboard (${data.established_total})</h2>${renderTable(establishedRows)}`;
      }
      if (provisionalRows.length > 0) {
        tablesHtml += `<h2 class="section-subtitle">Provisional Placement (&lt; 10 matches) (${data.provisional_total})</h2><p class="microcopy">Provisional players are actively completing placement matches to calibrate their competitive rating.</p>${renderTable(provisionalRows)}`;
      }
    }

    const totalPages = Math.max(1, Math.ceil(data.total / 20));
    const currentPage = state.rankingsPage + 1;
    const paginationHtml = data.total > 20 ? `<div class="rankings-pagination"><button class="btn btn-secondary" type="button" data-action="rankings-prev" ${state.rankingsPage === 0 ? 'disabled' : ''}>Previous</button><span class="microcopy">Page ${currentPage} of ${totalPages} (${data.total} total)</span><button class="btn btn-secondary" type="button" data-action="rankings-next" ${!data.has_more ? 'disabled' : ''}>Next</button></div>` : '';

    app.innerHTML = `<section class="rankings-card"><div class="section-kicker">COMPETITIVE RANKINGS</div><h1>Arena Leaderboard</h1><p class="subcopy">Authoritative persistent Elo rankings calculated from verified multiplayer matches.</p>${state.rankingsError ? `<p class="library-message" role="alert">${escapeHtml(state.rankingsError)} <button class="btn btn-quiet" type="button" data-action="rankings-retry">Retry</button></p>` : ''}<div class="rankings-scopes">${scopesNav}</div>${state.rankingsLoading && state.rankingsData ? '<p class="profile-empty" role="status">Updating rankings…</p>' : ''}${userBanner}${tablesHtml}${paginationHtml}<p class="microcopy" style="margin-top:2rem">Elo calculations are zero-sum across each multiplayer match. Ratings require complete individual matches with at least two authenticated player identities. Team Battle, Solo Practice, Escape Room, and casual matches are excluded from individual rated leaderboards.</p></section>`;
  }
  async function refreshRankings() {
    const requestId = ++state.rankingsRequestId;
    state.rankingsLoading = true;
    state.rankingsError = '';
    render();
    try {
      const scopeParam = state.rankingsScope;
      const url = scopeParam === 'mode'
        ? `/api/rankings?scope=mode&mode=${encodeURIComponent(state.rankingsMode)}&limit=20&offset=${state.rankingsPage * 20}`
        : `/api/rankings?scope=global&limit=20&offset=${state.rankingsPage * 20}`;
      const fetches = [fetch(url, { cache: 'no-store' })];
      if (state.profileCredential) {
        fetches.push(playerApi('/api/player-profiles/me/ratings'));
      }
      const [rankingsRes, myRatingsRes] = await Promise.all(fetches);
      if (requestId !== state.rankingsRequestId) return;
      if (!rankingsRes.ok) {
        throw new Error('Rankings are temporarily unavailable. Try again shortly.');
      }
      state.rankingsData = await rankingsRes.json();
      if (myRatingsRes && myRatingsRes.response.ok) {
        state.myRatings = myRatingsRes.data;
      }
    } catch (err) {
      if (requestId === state.rankingsRequestId) state.rankingsError = err.message || 'Could not load rankings.';
    } finally {
      if (requestId === state.rankingsRequestId) {
        state.rankingsLoading = false;
        render();
      }
    }
  }
  async function playerApi(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (state.profileCredential) headers.set('Authorization', `Bearer ${state.profileCredential}`);
    if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    return fetch(path, { ...options, headers, cache: 'no-store' }).then(async response => {
      let data = null;
      try { data = await response.json(); } catch { /* Empty responses are valid for deletion. */ }
      return { response, data };
    });
  }
  async function refreshProfile() {
    if (!state.profileCredential) { state.profile = null; render(); return; }
    state.profileLoading = true; state.profileNotice = ''; render();
    try {
      const [profileResult, statsResult, matchesResult, ratingsResult, advStatsResult, honorsResult] = await Promise.all([
        playerApi('/api/player-profiles/me'), playerApi('/api/player-profiles/me/competitive-stats'),
        playerApi(`/api/player-profiles/me/matches?limit=20&offset=${state.profilePage * 20}`),
        playerApi('/api/player-profiles/me/ratings'),
        playerApi('/api/players/me/statistics/advanced'),
        playerApi('/api/players/me/honors'),
      ]);
      if (profileResult.response.status === 401) throw new Error('Your credential was rejected. Sign in with a valid saved credential.');
      if (!profileResult.response.ok || !statsResult.response.ok || !matchesResult.response.ok) throw new Error('Player profile storage is temporarily unavailable. Try again.');
      state.profile = profileResult.data; state.profileStats = statsResult.data;
      void initPresence();
      void refreshRematches();
      if (ratingsResult && ratingsResult.response.ok) {
        state.myRatings = ratingsResult.data;
      }
      if (advStatsResult && advStatsResult.response.ok) {
        state.careerAdvancedStats = advStatsResult.data;
      }
      if (honorsResult && honorsResult.response.ok) {
        state.careerHonors = honorsResult.data;
      }
      await refreshRatingHistory(false);
      state.profileMatches = state.profilePage ? [...state.profileMatches, ...(matchesResult.data.matches || [])] : (matchesResult.data.matches || []);
      state.profileHistoryTotal = matchesResult.data.total || 0;
    } catch (error) {
      if (error.message.startsWith('Your credential')) { sessionStorage.removeItem(PLAYER_CREDENTIAL_KEY); state.profileCredential = ''; state.profile = null; }
      state.profileNotice = error.message || 'Could not load this profile.';
    } finally { state.profileLoading = false; render(); }
  }
  async function refreshRatingHistory(renderBeforeFetch = true) {
    if (!state.profileCredential) return;
    state.ratingHistoryLoading = true;
    state.ratingHistoryError = '';
    if (renderBeforeFetch) render();
    const params = new URLSearchParams({ limit: '20', offset: String(state.ratingHistoryPage * 20) });
    if (state.ratingHistoryScope) params.set('scope', state.ratingHistoryScope);
    try {
      const { response, data } = await playerApi(`/api/player-profiles/me/ratings/history?${params}`);
      if (!response.ok) throw new Error(data?.error?.message || 'Rating history is temporarily unavailable.');
      state.ratingHistory = data.history || [];
      state.ratingHistoryTotal = data.total || 0;
    } catch (error) {
      state.ratingHistoryError = error.message || 'Could not load rating history.';
    } finally {
      state.ratingHistoryLoading = false;
      if (renderBeforeFetch) render();
    }
  }
  async function getLiveIdentityTicket() {
    if (!state.profileCredential) return null;
    const { response, data } = await playerApi('/api/player-profiles/me/live-ticket', { method: 'POST' });
    if (!response.ok || typeof data?.live_ticket !== 'string') {
      notify(response.status === 401 ? 'Your player credential expired or was revoked. Sign in again or continue casually.' : 'Could not link your player profile to this arena. Try again or continue casually.');
      return false;
    }
    return data.live_ticket;
  }
  function updatePresenceUI() {
    Object.entries(state.friendPresence).forEach(([playerId, info]) => {
      const tag = document.querySelector(`[data-presence-tag="${playerId}"]`);
      const dot = document.querySelector(`[data-presence-dot="${playerId}"]`);
      const lbl = document.querySelector(`[data-presence-label="${playerId}"]`);
      if (tag && dot && lbl) {
        const s = info?.status || 'offline';
        const cls = (s === 'online' || s === 'in_lobby' || s === 'in_match') ? s : 'offline';
        const text = s === 'online' ? 'Online' : s === 'in_lobby' ? 'In Lobby' : s === 'in_match' ? 'In Match' : 'Offline';
        tag.className = `social-presence-tag ${cls}`;
        dot.className = `social-presence-dot ${cls}`;
        lbl.textContent = text;
      }
    });
  }

  async function initPresence() {
    if (!state.profileCredential) return;
    if (state.presenceWs && (state.presenceWs.readyState === WebSocket.OPEN || state.presenceWs.readyState === WebSocket.CONNECTING)) {
      return;
    }
    try {
      const { response, data } = await playerApi('/api/presence/ticket', { method: 'POST' });
      if (!response.ok || !data?.ticket) return;
      const ticket = data.ticket;
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/ws/presence`;
      const ws = new WebSocket(wsUrl);
      state.presenceWs = ws;

      ws.onopen = () => {
        state.presenceReconnectDelay = 1000;
        ws.send(JSON.stringify({ event: 'presence_auth', ticket }));
        void fetchNotificationUnreadCount();
        if (state.presenceHeartbeatTimer) clearInterval(state.presenceHeartbeatTimer);
        state.presenceHeartbeatTimer = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ event: 'ping' }));
          }
        }, 25000);
      };

      ws.onmessage = async (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.event === 'presence_ready') {
            (msg.snapshot || []).forEach(item => {
              state.friendPresence[item.player_id] = { status: item.status, updated_at: item.updated_at };
            });
            updatePresenceUI();
          } else if (msg.event === 'presence_update') {
            state.friendPresence[msg.player_id] = { status: msg.status, updated_at: msg.updated_at };
            updatePresenceUI();
          } else if (msg.event === 'room_invitation_received') {
            notify(`${msg.inviter_nickname || msg.inviter_handle || 'A friend'} invited you to arena ${msg.room_code}!`);
            void refreshSocial(false);
          } else if (msg.event === 'rematch_challenge_received') {
            notify(`${msg.challenger_nickname || msg.challenger_handle || 'An opponent'} challenged you to a rematch in ${msg.game_mode}!`);
            void refreshRematches();
          } else if (msg.event === 'rematch_challenge_accepted') {
            notify(`Rematch challenge accepted! Joining arena ${msg.room_code}…`);
            void refreshRematches();
            const identityTicket = await getLiveIdentityTicket();
            if (identityTicket) {
              send('join', {
                name: state.name || state.profile?.display_name || 'Player',
                code: msg.room_code,
                identity_ticket: identityTicket,
              });
              state.view = 'play';
              state.screen = 'lobby';
              setActiveNavigation();
            }
          } else if (msg.event === 'direct_message_received') {
            const incomingMsg = msg.message || msg;
            const convId = incomingMsg.conversation_id || msg.conversation_id;
            incomingMsg.is_mine = incomingMsg.sender_id === state.profile?.player_id;
            if (state.activeConversationId === convId) {
              if (!state.activeMessages.some(m => m.id === incomingMsg.id)) {
                state.activeMessages.push(incomingMsg);
              }
              void playerApi(`/api/messages/conversations/${convId}/read`, { method: 'POST' });
              setTimeout(scrollThreadToBottom, 50);
            } else {
              notify(`New message from ${incomingMsg.sender_name || 'Friend'}: ${(incomingMsg.content || '').slice(0, 35)}`);
              const conv = (state.conversations || []).find(c => c.id === convId);
              if (conv) {
                conv.unread_count = (conv.unread_count || 0) + 1;
                conv.last_message_preview = (incomingMsg.content || '').slice(0, 60);
                conv.last_message_at = incomingMsg.created_at;
              } else {
                void refreshSocial(false);
              }
            }
            if (state.view === 'social' && state.socialTab === 'messages') render();
          } else if (msg.event === 'direct_message_delivered') {
            const msgId = msg.id || msg.message_id;
            const convId = msg.conversation_id;
            const delivered_at = msg.delivered_at;
            if (state.activeConversationId === convId) {
              const m = state.activeMessages.find(item => item.id === msgId);
              if (m) {
                m.delivered_at = delivered_at;
                m.status = m.read_at ? 'read' : 'delivered';
                if (state.view === 'social' && state.socialTab === 'messages') render();
              }
            }
          } else if (msg.event === 'direct_message_read') {
            const convId = msg.conversation_id;
            const { marked_message_ids, read_at } = msg;
            if (state.activeConversationId === convId) {
              const ids = new Set(marked_message_ids || []);
              state.activeMessages.forEach(m => {
                if (ids.has(m.id) || !marked_message_ids || marked_message_ids.length === 0) {
                  m.is_read = true;
                  m.read_at = read_at;
                  m.status = 'read';
                }
              });
              if (state.view === 'social' && state.socialTab === 'messages') render();
            }
          } else if (msg.event === 'direct_message_edited') {
            const editedMsg = msg.message || msg;
            const convId = editedMsg.conversation_id || msg.conversation_id;
            if (state.activeConversationId === convId) {
              const idx = state.activeMessages.findIndex(m => m.id === editedMsg.id);
              if (idx !== -1) {
                state.activeMessages[idx] = { ...state.activeMessages[idx], ...editedMsg, is_edited: true };
                if (state.view === 'social' && state.socialTab === 'messages') render();
              }
            }
            const conv = (state.conversations || []).find(c => c.id === convId);
            if (conv) {
              conv.last_message_preview = editedMsg.content ? editedMsg.content.slice(0, 60) : conv.last_message_preview;
            }
          } else if (msg.event === 'direct_message_deleted') {
            const msgId = msg.id || msg.message_id;
            const convId = msg.conversation_id;
            const deleted_at = msg.deleted_at;
            if (state.activeConversationId === convId) {
              const m = state.activeMessages.find(item => item.id === msgId);
              if (m) {
                m.is_deleted = true;
                m.deleted_at = deleted_at;
                m.content = '[Message deleted]';
                if (state.view === 'social' && state.socialTab === 'messages') render();
              }
            }
            const conv = (state.conversations || []).find(c => c.id === convId);
            if (conv) {
              conv.last_message_preview = '[Message deleted]';
            }
          } else if (msg.event === 'direct_message_reaction_updated') {
            const msgId = msg.id || msg.message_id;
            const convId = msg.conversation_id;
            const reactions = msg.reactions;
            if (state.activeConversationId === convId) {
              const m = state.activeMessages.find(item => item.id === msgId);
              if (m) {
                m.reactions = reactions;
                if (state.view === 'social' && state.socialTab === 'messages') render();
              }
            }
          } else if (msg.event === 'typing_started') {
            if (msg.conversation_id) {
              state.typingFriends[msg.conversation_id] = true;
              if (state.view === 'social' && state.socialTab === 'messages' && state.activeConversationId === msg.conversation_id) {
                render();
              }
              setTimeout(() => {
                if (state.typingFriends[msg.conversation_id]) {
                  delete state.typingFriends[msg.conversation_id];
                  if (state.view === 'social' && state.socialTab === 'messages') render();
                }
              }, 4000);
            }
          } else if (msg.event === 'typing_stopped') {
            if (msg.conversation_id) {
              delete state.typingFriends[msg.conversation_id];
              if (state.view === 'social' && state.socialTab === 'messages') render();
            }
          } else if (msg.event === 'notification_received') {
            const notif = msg.notification || msg;
            state.notifUnreadCount = (state.notifUnreadCount || 0) + 1;
            updateNotifBadgeUI();
            if (state.notifCenterOpen) {
              state.notifications = [notif, ...(state.notifications || [])];
              renderNotificationCenter();
            }
            playChimeSound();
            notify(`🔔 ${notif.title}: ${notif.body}`);
          } else if (msg.event === 'notification_unread_count') {
            state.notifUnreadCount = msg.unread_count;
            updateNotifBadgeUI();
          } else if (msg.event === 'notification_read') {
            const id = msg.id;
            const item = (state.notifications || []).find(n => n.id === id);
            if (item) {
              item.is_read = true;
              if (state.notifCenterOpen) renderNotificationCenter();
            }
          } else if (msg.event === 'notification_deleted') {
            const id = msg.id;
            state.notifications = (state.notifications || []).filter(n => n.id !== id);
            if (state.notifCenterOpen) renderNotificationCenter();
          } else if (msg.event === 'notification_preferences_updated') {
            state.notifPreferences = msg.preferences;
          }
        } catch {}
      };

      ws.onclose = () => {
        if (state.presenceHeartbeatTimer) {
          clearInterval(state.presenceHeartbeatTimer);
          state.presenceHeartbeatTimer = null;
        }
        state.presenceWs = null;
        if (state.profileCredential) {
          if (state.presenceReconnectTimer) clearTimeout(state.presenceReconnectTimer);
          const delay = Math.min(state.presenceReconnectDelay, 15000);
          state.presenceReconnectDelay = delay * 2;
          state.presenceReconnectTimer = setTimeout(initPresence, delay);
        }
      };
    } catch {}
  }

  function closePresence() {
    if (state.presenceHeartbeatTimer) {
      clearInterval(state.presenceHeartbeatTimer);
      state.presenceHeartbeatTimer = null;
    }
    if (state.presenceReconnectTimer) {
      clearTimeout(state.presenceReconnectTimer);
      state.presenceReconnectTimer = null;
    }
    if (state.presenceWs) {
      try { state.presenceWs.close(); } catch {}
      state.presenceWs = null;
    }
    state.friendPresence = {};
  }

  async function refreshActiveMessages() {
    if (!state.activeConversationId) return;
    try {
      const res = await playerApi(`/api/messages/conversations/${state.activeConversationId}/messages?limit=50`);
      if (res.response.ok) {
        state.activeMessages = res.data.messages || [];
        void playerApi(`/api/messages/conversations/${state.activeConversationId}/read`, { method: 'POST' });
        const conv = (state.conversations || []).find(c => c.id === state.activeConversationId);
        if (conv) conv.unread_count = 0;
      }
    } catch {}
  }

  function scrollThreadToBottom() {
    const el = document.querySelector('#thread-messages-scroll');
    if (el) el.scrollTop = el.scrollHeight;
  }

  async function openConversationWithFriend(friendId, friendName) {
    state.socialTab = 'messages';
    state.socialLoading = true;
    render();
    try {
      let conv = (state.conversations || []).find(c => c.friend && c.friend.player_id === friendId);
      if (!conv) {
        const createRes = await playerApi('/api/messages/conversations', {
          method: 'POST',
          body: JSON.stringify({ friend_id: friendId })
        });
        if (createRes.response.ok) {
          conv = createRes.data;
          state.conversations.unshift(conv);
        } else {
          throw new Error(createRes.data?.error?.message || 'Could not start conversation.');
        }
      }
      state.activeConversationId = conv.id;
      state.editingMessageId = null;
      await refreshActiveMessages();
    } catch (err) {
      notify(err.message);
    } finally {
      state.socialLoading = false;
      render();
      setTimeout(scrollThreadToBottom, 50);
    }
  }

  async function refreshSocial(renderBeforeFetch = true) {
    if (!state.profileCredential) { render(); return; }
    state.socialLoading = true;
    state.socialNotice = '';
    if (renderBeforeFetch) render();
    try {
      const [friendsRes, incomingRes, outgoingRes, blockedRes, incInvRes, outInvRes, convRes, overviewRes] = await Promise.all([
        playerApi('/api/friends'),
        playerApi('/api/friends/requests/incoming'),
        playerApi('/api/friends/requests/outgoing'),
        playerApi('/api/blocks'),
        playerApi('/api/social/invitations/incoming'),
        playerApi('/api/social/invitations/outgoing'),
        playerApi('/api/messages/conversations'),
        playerApi('/api/notifications/social-overview'),
      ]);
      if (overviewRes && overviewRes.response.ok) state.socialOverview = overviewRes.data;
      void fetchNotificationUnreadCount();
      if (friendsRes.response.ok) {
        state.friendsList = friendsRes.data.friends || [];
        state.friendsList.forEach(f => {
          if (f.presence?.status && !state.friendPresence[f.player_id]) {
            state.friendPresence[f.player_id] = { status: f.presence.status, updated_at: new Date().toISOString() };
          }
        });
      }
      if (incomingRes.response.ok) state.incomingRequests = incomingRes.data.incoming_requests || [];
      if (outgoingRes.response.ok) state.outgoingRequests = outgoingRes.data.outgoing_requests || [];
      if (blockedRes.response.ok) state.blockedList = blockedRes.data.blocked || blockedRes.data.blocked_players || [];
      if (incInvRes && incInvRes.response.ok) state.incomingInvitations = incInvRes.data.incoming_invitations || [];
      if (outInvRes && outInvRes.response.ok) state.outgoingInvitations = outInvRes.data.outgoing_invitations || [];
      if (convRes && convRes.response.ok) state.conversations = convRes.data.conversations || [];
      if (state.activeConversationId) {
        await refreshActiveMessages();
      }
    } catch (err) {
      state.socialNotice = err.message || 'Could not load social data.';
    } finally {
      state.socialLoading = false;
      render();
    }
  }

  async function refreshRematches() {
    if (!state.profileCredential) return;
    try {
      const [inRes, outRes] = await Promise.all([
        playerApi('/api/social/rematches/incoming'),
        playerApi('/api/social/rematches/outgoing')
      ]);
      if (inRes.response.ok) state.incomingRematches = inRes.data.incoming_challenges || [];
      if (outRes.response.ok) state.outgoingRematches = outRes.data.outgoing_challenges || [];
      if (state.view === 'profile') render();
    } catch {}
  }

  function renderFriendsTab() {
    if (state.friendsList.length === 0) {
      return `
        <div class="social-empty-state">
          <h3>No Friends Yet</h3>
          <p>Your friends list is currently empty. Search for fellow quiz competitors to add them!</p>
          <button class="btn btn-primary" type="button" data-action="social-tab" data-tab="search">Find Players</button>
        </div>
      `;
    }
    const cards = state.friendsList.map(f => {
      const pInfo = state.friendPresence[f.player_id];
      const pStatus = pInfo?.status || f.presence?.status || 'offline';
      let dotClass = 'offline';
      let label = 'Offline';
      if (pStatus === 'online') {
        dotClass = 'online';
        label = 'Online';
      } else if (pStatus === 'in_lobby') {
        dotClass = 'in_lobby';
        label = 'In Lobby';
      } else if (pStatus === 'in_match') {
        dotClass = 'in_match';
        label = 'In Match';
      }
      return `
      <div class="social-card">
        <div class="social-card-user">
          <div class="social-avatar">${escapeHtml(initials(f.display_name || 'Player'))}</div>
          <div class="social-card-meta">
            <strong>${escapeHtml(f.display_name)}</strong>
            <div class="social-rating-pill">⚡ ${f.rating ?? f.global_rating} Elo</div>
            <div class="social-presence-tag ${dotClass}" data-presence-tag="${f.player_id}">
              <span class="social-presence-dot ${dotClass}" data-presence-dot="${f.player_id}" aria-hidden="true"></span>
              <span class="social-presence-label" data-presence-label="${f.player_id}">${label}</span>
            </div>
          </div>
        </div>
        <div class="social-card-actions">
          <button class="btn btn-primary" type="button" data-action="open-friend-chat" data-friend-id="${f.player_id}" data-friend-name="${escapeHtml(f.display_name)}">💬 Message</button>
          ${state.room && state.room.phase === 'waiting' ? `<button class="btn btn-secondary" type="button" data-action="invite-friend" data-friend-id="${f.player_id}" data-friend-name="${escapeHtml(f.display_name)}">Invite to Arena</button>` : ''}
          <button class="btn btn-secondary" type="button" data-action="view-public-profile" data-player-id="${f.player_id}">View Profile</button>
          <button class="btn btn-secondary" type="button" data-action="remove-friend" data-friend-id="${f.player_id}">Remove</button>
          <button class="btn btn-secondary" type="button" data-action="block-player" data-player-id="${f.player_id}">Block</button>
        </div>
      </div>
    `;}).join('');
    return `<div class="social-grid">${cards}</div>`;
  }

  function renderSearchTab() {
    const resultsHtml = state.playerSearchResults.length === 0
      ? (state.searchQuery ? `<div class="social-empty-state"><p>No players found matching "${escapeHtml(state.searchQuery)}".</p></div>` : '<div class="social-empty-state"><p>Enter a player display name or 32-character ID to search.</p></div>')
      : `<div class="social-grid">${state.playerSearchResults.map(p => {
          let actionBtn = '';
          if (p.relationship_state === 'friends' || p.relationship_state === 'friend') {
            actionBtn = '<span class="pill pill-success">✓ Friends</span>';
          } else if (p.relationship_state === 'outgoing_request' || p.relationship_state === 'request_sent') {
            actionBtn = '<span class="pill pill-warning">Request Pending</span>';
          } else if (p.relationship_state === 'incoming_request' || p.relationship_state === 'request_received') {
            actionBtn = `<button class="btn btn-primary" type="button" data-action="social-tab" data-tab="requests">Respond</button>`;
          } else if (p.relationship_state === 'blocked' || p.relationship_state === 'blocked_by') {
            actionBtn = '<span class="pill pill-danger">Blocked</span>';
          } else {
            actionBtn = `<button class="btn btn-primary" type="button" data-action="send-friend-request" data-recipient-id="${p.player_id}">Add Friend</button>`;
          }
          return `
            <div class="social-card">
              <div class="social-card-user">
                <div class="social-avatar">${escapeHtml(initials(p.display_name || 'Player'))}</div>
                <div class="social-card-meta">
                  <strong>${escapeHtml(p.display_name)}</strong>
                  <div class="social-rating-pill">⚡ ${p.rating} Elo ${p.is_provisional ? '(Prov)' : ''}</div>
                  <span>Win Rate: ${p.win_percentage}% · ${p.total_matches} matches</span>
                </div>
              </div>
              <div class="social-card-actions">
                ${actionBtn}
                <button class="btn btn-secondary" type="button" data-action="view-public-profile" data-player-id="${p.player_id}">View Profile</button>
              </div>
            </div>
          `;
        }).join('')}</div>`;

    return `
      <div class="social-search-bar">
        <input id="player-search-input" type="search" placeholder="Search by display name or player ID (min 2 chars)..." value="${escapeHtml(state.searchQuery)}">
        <button class="btn btn-primary" type="button" data-action="exec-player-search">Search</button>
      </div>
      ${resultsHtml}
    `;
  }

  function renderRequestsTab() {
    const incomingCards = state.incomingRequests.length === 0
      ? '<div class="social-empty-state"><p>No incoming friend requests.</p></div>'
      : `<div class="social-grid">${state.incomingRequests.map(r => {
          const sName = r.sender_display_name || r.sender_name || 'Player';
          return `
          <div class="social-card">
            <div class="social-card-user">
              <div class="social-avatar">${escapeHtml(initials(sName))}</div>
              <div class="social-card-meta">
                <strong>${escapeHtml(sName)}</strong>
                <div class="social-rating-pill">⚡ ${r.sender_rating} Elo</div>
                <span>Received ${new Date(r.created_at).toLocaleDateString()}</span>
              </div>
            </div>
            <div class="social-card-actions">
              <button class="btn btn-primary" type="button" data-action="accept-request" data-request-id="${r.request_id}">Accept</button>
              <button class="btn btn-secondary" type="button" data-action="reject-request" data-request-id="${r.request_id}">Reject</button>
              <button class="btn btn-secondary" type="button" data-action="view-public-profile" data-player-id="${r.sender_id}">View Profile</button>
            </div>
          </div>
        `;}).join('')}</div>`;

    const outgoingCards = state.outgoingRequests.length === 0
      ? '<div class="social-empty-state"><p>No pending outgoing friend requests.</p></div>'
      : `<div class="social-grid">${state.outgoingRequests.map(r => {
          const rName = r.recipient_display_name || r.recipient_name || 'Player';
          return `
          <div class="social-card">
            <div class="social-card-user">
              <div class="social-avatar">${escapeHtml(initials(rName))}</div>
              <div class="social-card-meta">
                <strong>${escapeHtml(rName)}</strong>
                <div class="social-rating-pill">⚡ ${r.recipient_rating} Elo</div>
                <span>Sent ${new Date(r.created_at).toLocaleDateString()}</span>
              </div>
            </div>
            <div class="social-card-actions">
              <button class="btn btn-secondary" type="button" data-action="cancel-request" data-request-id="${r.request_id}">Cancel Request</button>
              <button class="btn btn-secondary" type="button" data-action="view-public-profile" data-player-id="${r.recipient_id}">View Profile</button>
            </div>
          </div>
        `;}).join('')}</div>`;

    return `
      <h3 class="social-req-group-title">Incoming Requests (${state.incomingRequests.length})</h3>
      ${incomingCards}
      <h3 class="social-req-group-title" style="margin-top:2rem">Outgoing Requests (${state.outgoingRequests.length})</h3>
      ${outgoingCards}
    `;
  }

  function renderBlockedTab() {
    if (state.blockedList.length === 0) {
      return '<div class="social-empty-state"><p>No blocked players.</p></div>';
    }
    return `<div class="social-grid">${state.blockedList.map(b => `
      <div class="social-card">
        <div class="social-card-user">
          <div class="social-avatar">${escapeHtml(initials(b.display_name || 'Player'))}</div>
          <div class="social-card-meta">
            <strong>${escapeHtml(b.display_name)}</strong>
            <span>Blocked on ${new Date(b.created_at).toLocaleDateString()}</span>
          </div>
        </div>
        <div class="social-card-actions">
          <button class="btn btn-secondary" type="button" data-action="unblock-player" data-player-id="${b.player_id}">Unblock</button>
        </div>
      </div>
    `).join('')}</div>`;
  }

  function renderPublicProfileModal() {
    const p = state.selectedPublicProfile;
    let relBadge = '';
    let relAction = '';
    if (p.relationship_state === 'friends' || p.relationship_state === 'friend') {
      relBadge = '<span class="pill pill-success">Friend</span>';
      relAction = `<button class="btn btn-secondary" type="button" data-action="remove-friend" data-friend-id="${p.player_id}">Remove Friend</button>`;
    } else if (p.relationship_state === 'outgoing_request' || p.relationship_state === 'request_sent') {
      relBadge = '<span class="pill pill-warning">Request Pending</span>';
    } else if (p.relationship_state === 'incoming_request' || p.relationship_state === 'request_received') {
      relBadge = '<span class="pill pill-warning">Request Received</span>';
      relAction = `<button class="btn btn-primary" type="button" data-action="social-tab" data-tab="requests">Respond in Requests</button>`;
    } else if (p.relationship_state === 'blocked' || p.relationship_state === 'blocked_by') {
      relBadge = '<span class="pill pill-danger">Blocked</span>';
      relAction = p.relationship_state === 'blocked' ? `<button class="btn btn-secondary" type="button" data-action="unblock-player" data-player-id="${p.player_id}">Unblock</button>` : '';
    } else {
      relBadge = '<span class="pill pill-neutral">Not Connected</span>';
      relAction = `<button class="btn btn-primary" type="button" data-action="send-friend-request" data-recipient-id="${p.player_id}">Add Friend</button>`;
    }

    const trophies = p.honors_summary?.trophies || [];
    const trophiesHtml = trophies.length > 0
      ? `<div class="trophies-grid" style="margin-top:.5rem">${trophies.map(t => `
          <div class="trophy-card tier-${t.tier}">
            <div class="trophy-badge">${escapeHtml(t.badge_label || t.tier)}</div>
            <p class="trophy-title">${escapeHtml(t.title)}</p>
            <div class="trophy-meta">${escapeHtml(t.subtitle)}</div>
          </div>
        `).join('')}</div>`
      : '<p style="color:#8e90ab;font-size:.85rem;margin:.5rem 0 0">No competitive trophies earned yet.</p>';

    return `
      <div class="social-modal-backdrop" data-action="close-public-profile">
        <div class="social-modal" onclick="event.stopPropagation()">
          <div class="social-modal-header">
            <div style="display:flex;align-items:center;gap:.75rem">
              <div class="social-avatar">${escapeHtml(initials(p.display_name || 'Player'))}</div>
              <div>
                <h2>${escapeHtml(p.display_name)}</h2>
                <div style="display:flex;align-items:center;gap:.5rem;margin-top:.25rem">
                  <span style="font-size:.8rem;color:#8e90ab">ID: ${escapeHtml(p.player_id)}</span>
                  ${relBadge}
                </div>
              </div>
            </div>
            <button class="btn btn-secondary" type="button" data-action="close-public-profile">✕</button>
          </div>
          <div class="social-modal-body">
            <div class="social-stats-row">
              <div class="social-stats-cell">
                <strong>${p.rating}</strong>
                <span>Global Elo ${p.is_provisional ? '(Prov)' : ''}</span>
              </div>
              <div class="social-stats-cell">
                <strong>${p.win_percentage}%</strong>
                <span>Win Rate</span>
              </div>
              <div class="social-stats-cell">
                <strong>${p.total_matches}</strong>
                <span>Matches (${p.wins}W / ${p.losses}L / ${p.draws}D)</span>
              </div>
            </div>
            <div>
              <strong style="display:block;color:#fff;margin-bottom:.4rem;font-size:.95rem">Trophies &amp; Honors</strong>
              ${trophiesHtml}
            </div>
          </div>
          <div class="social-modal-actions">
            ${relAction}
            ${p.relationship_state !== 'blocked' && p.relationship_state !== 'blocked_by' ? `<button class="btn btn-secondary" type="button" data-action="block-player" data-player-id="${p.player_id}">Block</button>` : ''}
            <button class="btn btn-secondary" type="button" data-action="close-public-profile">Close</button>
          </div>
        </div>
      </div>
    `;
  }

  function renderInvitationsTab() {
    const incoming = state.incomingInvitations || [];
    const outgoing = state.outgoingInvitations || [];

    const incomingHtml = incoming.length === 0
      ? '<div class="social-empty-state"><p>No incoming room invitations.</p></div>'
      : `<div class="social-grid">${incoming.map(inv => `
          <div class="social-card invitation-card">
            <div class="social-card-user">
              <div class="social-avatar">${escapeHtml(initials(inv.inviter_name || 'Player'))}</div>
              <div class="social-card-meta invitation-meta">
                <strong>${escapeHtml(inv.inviter_name)}</strong>
                <div class="social-rating-pill">Arena: ${escapeHtml(inv.room_code)}</div>
                <div class="invitation-countdown">Expires in ${inv.seconds_remaining}s</div>
              </div>
            </div>
            <div class="social-card-actions">
              <button class="btn btn-primary" type="button" data-action="accept-invitation" data-invitation-id="${inv.invitation_id}">Accept &amp; Join</button>
              <button class="btn btn-secondary" type="button" data-action="decline-invitation" data-invitation-id="${inv.invitation_id}">Decline</button>
            </div>
          </div>
        `).join('')}</div>`;

    const outgoingHtml = outgoing.length === 0
      ? '<div class="social-empty-state"><p>No outgoing room invitations.</p></div>'
      : `<div class="social-grid">${outgoing.map(inv => `
          <div class="social-card invitation-card">
            <div class="social-card-user">
              <div class="social-avatar">${escapeHtml(initials(inv.invitee_name || 'Player'))}</div>
              <div class="social-card-meta invitation-meta">
                <strong>${escapeHtml(inv.invitee_name)}</strong>
                <div class="social-rating-pill">Arena: ${escapeHtml(inv.room_code)}</div>
                <div class="invitation-countdown">Expires in ${inv.seconds_remaining}s</div>
              </div>
            </div>
            <div class="social-card-actions">
              <button class="btn btn-secondary" type="button" data-action="cancel-invitation" data-invitation-id="${inv.invitation_id}">Cancel</button>
            </div>
          </div>
        `).join('')}</div>`;

    return `
      <div>
        <h3 class="social-req-group-title">Incoming Invitations</h3>
        ${incomingHtml}
        <h3 class="social-req-group-title" style="margin-top:2rem;">Outgoing Invitations</h3>
        ${outgoingHtml}
      </div>
    `;
  }

  function renderMessagesTab() {
    const filterQuery = (state.messageSearchQuery || '').toLowerCase();
    const filteredConvs = (state.conversations || []).filter(c => {
      const name = (c.friend?.display_name || '').toLowerCase();
      return !filterQuery || name.includes(filterQuery);
    });

    const activeConv = (state.conversations || []).find(c => c.id === state.activeConversationId);

    // Left sidebar: conversations
    const convItems = filteredConvs.length === 0
      ? '<div class="messaging-empty-list"><p>No conversations found.</p></div>'
      : filteredConvs.map(c => {
          const friend = c.friend || {};
          const pInfo = state.friendPresence[friend.player_id];
          const pStatus = pInfo?.status || friend.presence?.status || 'offline';
          const isCurrent = c.id === state.activeConversationId;
          const snippet = c.last_message_preview
            ? escapeHtml(c.last_message_preview)
            : '<em>No messages yet</em>';
          const timeStr = c.last_message_at
            ? new Date(c.last_message_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
            : '';
          const unreadBadge = c.unread_count > 0 ? `<span class="social-badge">${c.unread_count}</span>` : '';

          return `
            <div class="conv-item ${isCurrent ? 'active' : ''}" data-action="select-conversation" data-conversation-id="${c.id}">
              <div class="conv-avatar-wrap">
                <div class="social-avatar">${escapeHtml(initials(friend.display_name || 'Player'))}</div>
                <span class="presence-dot ${pStatus}"></span>
              </div>
              <div class="conv-info">
                <div class="conv-header">
                  <strong class="conv-name">${escapeHtml(friend.display_name)}</strong>
                  <span class="conv-time">${timeStr}</span>
                </div>
                <div class="conv-preview">
                  <span class="conv-snippet">${snippet}</span>
                  ${unreadBadge}
                </div>
              </div>
            </div>
          `;
        }).join('');

    // Right side: Active Thread
    let threadHtml = '';
    if (!activeConv) {
      threadHtml = `
        <div class="thread-placeholder">
          <div class="thread-placeholder-inner">
            <span class="placeholder-icon">💬</span>
            <h3>Select a Conversation</h3>
            <p>Choose a friend from the list or start a new chat from the Friends tab.</p>
            <button class="btn btn-secondary" type="button" data-action="social-tab" data-tab="friends">Go to Friends</button>
          </div>
        </div>
      `;
    } else {
      const friend = activeConv.friend || {};
      const pInfo = state.friendPresence[friend.player_id];
      const pStatus = pInfo?.status || friend.presence?.status || 'offline';
      let statusLabel = 'Offline';
      if (pStatus === 'online') statusLabel = 'Online';
      else if (pStatus === 'in_lobby') statusLabel = 'In Lobby';
      else if (pStatus === 'in_match') statusLabel = 'In Match';

      const isTyping = !!state.typingFriends[activeConv.id];

      const messagesHtml = state.messagesLoading
        ? '<div class="loading-state"><span class="loader"></span><p>Loading messages…</p></div>'
        : (state.activeMessages || []).length === 0
          ? '<div class="thread-empty"><p>No messages yet. Say hello!</p></div>'
          : (state.activeMessages || []).map(m => {
              const isMine = m.is_mine || m.sender_id === state.profile?.player_id;
              const isDeleted = m.is_deleted || !!m.deleted_at;
              const isEdited = m.is_edited || !!m.edited_at;

              // Read / delivery receipt checkmark
              let receiptHtml = '';
              if (isMine && !isDeleted) {
                if (m.is_read || m.read_at || m.status === 'read') {
                  receiptHtml = '<span class="receipt-check read" title="Read">✓✓</span>';
                } else if (m.delivered_at || m.status === 'delivered') {
                  receiptHtml = '<span class="receipt-check delivered" title="Delivered">✓✓</span>';
                } else {
                  receiptHtml = '<span class="receipt-check sent" title="Sent">✓</span>';
                }
              }

              // Edit / delete within 5 minutes
              const canEdit = isMine && !isDeleted && (Date.now() - new Date(m.created_at).getTime() < 5 * 60 * 1000);
              const canDelete = isMine && !isDeleted;
              const canReport = !isMine && !isDeleted;

              // Reaction pills
              const rxSummary = m.reactions || {};
              const rxPills = Object.entries(rxSummary).filter(([_, val]) => {
                if (Array.isArray(val)) return val.length > 0;
                if (typeof val === 'object') return (val.count || 0) > 0;
                return val > 0;
              }).map(([emoji, val]) => {
                let count = 0;
                let reactedByMe = false;
                if (Array.isArray(val)) {
                  count = val.length;
                  reactedByMe = val.includes(state.profile?.player_id);
                } else if (typeof val === 'object') {
                  count = val.count || 0;
                  reactedByMe = !!val.reacted_by_me;
                } else {
                  count = val;
                }
                return `
                  <button class="rx-pill ${reactedByMe ? 'active' : ''}" type="button" data-action="toggle-dm-reaction" data-message-id="${m.id}" data-reaction="${emoji}">
                    ${emoji} ${count}
                  </button>
                `;
              }).join('');

              const contentHtml = isDeleted
                ? '<span class="msg-deleted-text"><em>🚫 This message was deleted</em></span>'
                : `${escapeHtml(m.content)}${isEdited ? ' <span class="msg-edited-tag">(edited)</span>' : ''}`;

              return `
                <div class="msg-row ${isMine ? 'mine' : 'theirs'}" data-message-id="${m.id}">
                  <div class="msg-bubble ${isMine ? 'bubble-mine' : 'bubble-theirs'} ${isDeleted ? 'bubble-deleted' : ''}">
                    <div class="msg-text">${contentHtml}</div>
                    <div class="msg-footer">
                      <span class="msg-time">${new Date(m.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                      ${receiptHtml}
                    </div>
                  </div>
                  ${!isDeleted ? `
                    <div class="msg-actions-row">
                      <div class="msg-rx-picker">
                        ${['👍', '❤️', '😂', '🔥', '👏', '😮', '😢'].map(emoji => `
                          <button class="rx-btn" type="button" data-action="toggle-dm-reaction" data-message-id="${m.id}" data-reaction="${emoji}">${emoji}</button>
                        `).join('')}
                      </div>
                      <div class="msg-manage-btns">
                        ${canEdit ? `<button class="msg-action-btn" type="button" data-action="start-edit-dm" data-message-id="${m.id}" data-content="${escapeHtml(m.content)}" title="Edit">✏️</button>` : ''}
                        ${canDelete ? `<button class="msg-action-btn" type="button" data-action="delete-dm" data-message-id="${m.id}" title="Delete">🗑️</button>` : ''}
                        ${canReport ? `<button class="msg-action-btn" type="button" data-action="start-report-dm" data-message-id="${m.id}" title="Report">🚩</button>` : ''}
                      </div>
                    </div>
                  ` : ''}
                  ${rxPills ? `<div class="msg-reactions-bar">${rxPills}</div>` : ''}
                </div>
              `;
            }).join('');

      const editingBanner = state.editingMessageId
        ? `
          <div class="editing-banner">
            <span>Editing message</span>
            <button class="btn btn-secondary btn-sm" type="button" data-action="cancel-edit-dm">Cancel</button>
          </div>
        `
        : '';

      const typingIndicator = isTyping
        ? `<div class="typing-indicator-bar"><span class="typing-dots"><span></span><span></span><span></span></span> ${escapeHtml(friend.display_name)} is typing…</div>`
        : '';

      threadHtml = `
        <div class="thread-container">
          <div class="thread-header">
            <div class="thread-header-user">
              <button class="btn btn-secondary btn-icon mobile-only" type="button" data-action="close-active-chat" title="Back to conversations">←</button>
              <div class="conv-avatar-wrap">
                <div class="social-avatar">${escapeHtml(initials(friend.display_name || 'Player'))}</div>
                <span class="presence-dot ${pStatus}"></span>
              </div>
              <div class="thread-header-meta">
                <strong>${escapeHtml(friend.display_name)}</strong>
                <span class="thread-presence-label ${pStatus}">${statusLabel}</span>
              </div>
            </div>
            <div class="thread-header-actions">
              <button class="btn btn-secondary btn-sm" type="button" data-action="view-public-profile" data-player-id="${friend.player_id}">Profile</button>
            </div>
          </div>
          <div class="thread-messages" id="thread-messages-scroll">
            ${messagesHtml}
          </div>
          ${typingIndicator}
          ${editingBanner}
          <div class="thread-composer">
            <textarea id="dm-composer-input" maxlength="2000" placeholder="Type a message to ${escapeHtml(friend.display_name)}..." rows="1">${escapeHtml(state.messageDraft || '')}</textarea>
            <div class="composer-controls">
              <span class="char-count" id="dm-char-count">${(state.messageDraft || '').length} / 2000</span>
              <button class="btn btn-primary" type="button" data-action="${state.editingMessageId ? 'submit-edit-dm' : 'send-dm'}">
                ${state.editingMessageId ? 'Save' : 'Send'}
              </button>
            </div>
          </div>
        </div>
      `;
    }

    return `
      <div class="messaging-shell ${activeConv ? 'has-active-chat' : ''}">
        <div class="messaging-sidebar">
          <div class="messaging-search">
            <input type="search" id="dm-search-input" placeholder="Search conversations..." value="${escapeHtml(state.messageSearchQuery || '')}">
          </div>
          <div class="conv-list">
            ${convItems}
          </div>
        </div>
        <div class="messaging-main">
          ${threadHtml}
        </div>
      </div>
    `;
  }

  function renderReportModal() {
    return `
      <div class="social-modal-backdrop" data-action="close-report-modal">
        <div class="social-modal report-modal" data-action="modal-container">
          <div class="social-modal-header">
            <h2>Report Message</h2>
            <button class="btn btn-secondary" type="button" data-action="close-report-modal">✕</button>
          </div>
          <div class="social-modal-body">
            <p>Why are you reporting this message? Our safety team will review it.</p>
            <label for="report-reason-select" style="display:block;margin-top:.75rem;font-weight:600">Reason</label>
            <select id="report-reason-select" class="field" style="width:100%;margin-top:.25rem">
              <option value="inappropriate" ${state.reportingReason === 'inappropriate' ? 'selected' : ''}>Inappropriate Content</option>
              <option value="harassment" ${state.reportingReason === 'harassment' ? 'selected' : ''}>Harassment</option>
              <option value="spam" ${state.reportingReason === 'spam' ? 'selected' : ''}>Spam</option>
              <option value="abuse" ${state.reportingReason === 'abuse' ? 'selected' : ''}>Abuse</option>
              <option value="other" ${state.reportingReason === 'other' ? 'selected' : ''}>Other</option>
            </select>
            <label for="report-details-input" style="display:block;margin-top:.75rem;font-weight:600">Additional Details (Optional)</label>
            <textarea id="report-details-input" rows="3" class="field" style="height:auto;padding:.5rem;margin-top:.25rem" placeholder="Provide any additional context...">${escapeHtml(state.reportingDetails || '')}</textarea>
          </div>
          <div class="social-modal-actions">
            <button class="btn btn-primary" type="button" data-action="submit-report-dm">Submit Report</button>
            <button class="btn btn-secondary" type="button" data-action="close-report-modal">Cancel</button>
          </div>
        </div>
      </div>
    `;
  }

  function renderRoomChatDrawer() {
    if (!state.room) return '';
    const unreadBadge = (!state.roomChatOpen && state.roomChatUnread > 0)
      ? `<span class="chat-badge">${state.roomChatUnread}</span>`
      : '';
    const messages = state.room.chat_messages || [];
    const isClosed = state.room.phase === 'results';

    const drawerBody = messages.length === 0
      ? '<div class="room-chat-empty">No messages yet. Say hello to everyone!</div>'
      : messages.map(m => {
          const isMine = m.player_id === state.playerId;
          const rxList = m.reactions || {};
          const rxButtons = Object.entries(rxList).map(([rx, val]) => {
            const count = Array.isArray(val) ? val.length : (typeof val === 'number' ? val : 0);
            if (count <= 0) return '';
            const userReacted = Array.isArray(val) ? val.includes(state.playerId) : false;
            return `
              <button class="room-chat-rx-pill ${userReacted ? 'active' : ''}" type="button" data-action="toggle-room-reaction" data-message-id="${m.id}" data-reaction="${rx}">${rx} ${count}</button>
            `;
          }).join('');
          const author = m.sender_name || m.player_name || 'Player';
          const content = m.content || m.text || '';
          return `
            <div class="room-chat-msg ${isMine ? 'mine' : ''}">
              <div class="room-chat-author">
                <span>${escapeHtml(author)}${isMine ? ' (You)' : ''}</span>
                <span class="room-chat-time">${new Date(m.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
              </div>
              <div class="room-chat-text">${escapeHtml(content)}</div>
              <div class="room-chat-reactions">
                ${rxButtons}
                <div class="room-chat-rx-picker">
                  ${['👍', '❤️', '😂', '🔥', '👏', '😮', '😢'].map(emoji => `
                    <button class="rx-quick-btn" type="button" data-action="toggle-room-reaction" data-message-id="${m.id}" data-reaction="${emoji}">${emoji}</button>
                  `).join('')}
                </div>
              </div>
            </div>
          `;
        }).join('');

    const inputForm = isClosed
      ? '<div class="room-chat-closed">Room chat is closed for this match.</div>'
      : `
        <form class="room-chat-form" id="room-chat-form" onsubmit="return false;">
          <input type="text" id="room-chat-input" maxlength="500" placeholder="Type a message..." autocomplete="off">
          <button class="btn btn-primary btn-sm" type="button" data-action="send-room-chat">Send</button>
        </form>
      `;

    return `
      <div id="room-chat-container">
        <button id="room-chat-toggle" class="room-chat-toggle-btn ${state.roomChatOpen ? 'active' : ''}" type="button" data-action="toggle-room-chat" aria-label="Toggle Arena Chat">
          💬 Arena Chat ${unreadBadge}
        </button>
        <div id="room-chat-drawer" class="room-chat-drawer ${state.roomChatOpen ? 'open' : 'closed'}">
          <div class="room-chat-header">
            <h4>💬 Arena Chat (${escapeHtml(state.room.code)})</h4>
            <button class="btn btn-secondary btn-sm" type="button" data-action="toggle-room-chat" aria-label="Close Chat">✕</button>
          </div>
          <div class="room-chat-messages" id="room-chat-messages-container">
            ${drawerBody}
          </div>
          <div class="room-chat-footer">
            ${inputForm}
          </div>
        </div>
      </div>
    `;
  }

  function updateNotifBadgeUI() {
    const badge = document.querySelector('#notif-bell-badge');
    if (!badge) return;
    const count = state.notifUnreadCount || 0;
    if (count > 0) {
      badge.style.display = 'inline-block';
      badge.classList.remove('hidden');
      badge.textContent = count > 99 ? '99+' : String(count);
    } else {
      badge.style.display = 'none';
      badge.classList.add('hidden');
      badge.textContent = '0';
    }
  }

  function playChimeSound() {
    try {
      if (state.notifPreferences && state.notifPreferences.browser_sound === false) return;
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      const ctx = new AudioCtx();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(587.33, ctx.currentTime);
      osc.frequency.setValueAtTime(880, ctx.currentTime + 0.1);
      gain.gain.setValueAtTime(0.12, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.35);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.35);
    } catch {}
  }

  async function fetchNotificationUnreadCount() {
    if (!state.profileCredential) return;
    try {
      const { response, data } = await playerApi('/api/notifications/unread-count');
      if (response.ok && typeof data?.unread_count === 'number') {
        state.notifUnreadCount = data.unread_count;
        updateNotifBadgeUI();
      }
    } catch {}
  }

  async function fetchNotifications(filter, unreadOnly) {
    if (!state.profileCredential) return;
    if (filter !== undefined) state.notifTab = filter;
    if (unreadOnly !== undefined) state.notifUnreadOnly = unreadOnly;
    state.notifLoading = true;
    renderNotificationCenter();
    try {
      const q = new URLSearchParams();
      if (state.notifTab && state.notifTab !== 'all') q.set('filter', state.notifTab);
      if (state.notifUnreadOnly) q.set('unread_only', 'true');
      q.set('limit', '50');
      const { response, data } = await playerApi(`/api/notifications?${q.toString()}`);
      if (response.ok) {
        state.notifications = data.notifications || [];
      }
    } catch {}
    finally {
      state.notifLoading = false;
      renderNotificationCenter();
    }
  }

  async function fetchNotificationPreferences() {
    if (!state.profileCredential) return;
    try {
      const { response, data } = await playerApi('/api/notifications/preferences');
      if (response.ok) {
        state.notifPreferences = data;
        state.notifPreferencesOpen = true;
        renderNotificationPreferencesModal();
      }
    } catch {}
  }

  async function saveNotificationPreferences(prefs) {
    if (!state.profileCredential) return;
    try {
      const { response, data } = await playerApi('/api/notifications/preferences', {
        method: 'PATCH',
        body: JSON.stringify(prefs),
      });
      if (response.ok) {
        state.notifPreferences = data;
        state.notifPreferencesOpen = false;
        renderNotificationPreferencesModal();
        notify('Notification preferences updated.');
      } else {
        notify('Failed to update preferences.');
      }
    } catch {
      notify('Failed to update preferences.');
    }
  }

  function renderNotificationPreferencesModal() {
    const container = document.querySelector('#notif-modal-container');
    if (!container) return;
    if (!state.notifPreferencesOpen || !state.notifPreferences) {
      container.innerHTML = '';
      return;
    }
    const p = state.notifPreferences;
    const catRows = [
      { key: 'friend_requests', label: 'Friend Requests', desc: 'New friend requests from players' },
      { key: 'friendship_updates', label: 'Friendship Updates', desc: 'Accepted friend requests and updates' },
      { key: 'friend_online', label: 'Friend Online Alerts', desc: 'Alerts when your friends come online' },
      { key: 'room_invitations', label: 'Room Invitations', desc: 'Invitations to multiplayer arenas' },
      { key: 'rematch_challenges', label: 'Rematch Challenges', desc: 'Challenges to 1v1 rematches' },
      { key: 'direct_message_notifications', label: 'Direct Messages', desc: 'Private messages from friends' },
      { key: 'tournament_updates', label: 'Tournament Alerts', desc: 'Match calls, advancements, and eliminations' },
      { key: 'league_updates', label: 'League Alerts', desc: 'Season schedules, rounds, and standings' },
      { key: 'achievement_notifications', label: 'Achievements', desc: 'Badges, honors, and trophy milestones' },
      { key: 'rating_milestones', label: 'Rating Milestones', desc: 'Competitive skill tier and milestone upgrades' },
      { key: 'personal_best_notifications', label: 'Personal Bests', desc: 'New high-score and accuracy records' },
    ].map(item => `
      <div class="prefs-row">
        <div class="prefs-label">
          <span>${escapeHtml(item.label)}</span>
          <span>${escapeHtml(item.desc)}</span>
        </div>
        <div class="prefs-toggle">
          <input type="checkbox" id="pref-${item.key}" ${p[item.key] ? 'checked' : ''} data-pref-key="${item.key}">
        </div>
      </div>
    `).join('');

    const deliveryRows = [
      { key: 'visual_notifications', label: 'Visual In-App Notifications', desc: 'Show toast alerts and live badges' },
      { key: 'browser_sound', label: 'Sound Effects', desc: 'Play audio chime on incoming notifications' },
    ].map(item => `
      <div class="prefs-row">
        <div class="prefs-label">
          <span>${escapeHtml(item.label)}</span>
          <span>${escapeHtml(item.desc)}</span>
        </div>
        <div class="prefs-toggle">
          <input type="checkbox" id="pref-${item.key}" ${p[item.key] ? 'checked' : ''} data-pref-key="${item.key}">
        </div>
      </div>
    `).join('');

    container.innerHTML = `
      <div class="modal-backdrop" data-action="close-notif-backdrop">
        <div class="modal-card" style="max-width: 520px; max-height: 85vh; overflow-y: auto;">
          <div class="modal-header">
            <h3>Notification Preferences</h3>
            <button class="btn btn-secondary btn-sm" type="button" data-action="close-notif-prefs">✕</button>
          </div>
          <div class="modal-body">
            <h4 style="margin: 0.5rem 0 0.25rem; font-size: 0.85rem; color: #a3a6cc; text-transform: uppercase; letter-spacing: 0.05em;">Categories</h4>
            <div class="prefs-grid">
              ${catRows}
            </div>
            <h4 style="margin: 1rem 0 0.25rem; font-size: 0.85rem; color: #a3a6cc; text-transform: uppercase; letter-spacing: 0.05em;">Delivery</h4>
            <div class="prefs-grid">
              ${deliveryRows}
            </div>
          </div>
          <div class="modal-actions" style="margin-top: 1rem; display: flex; justify-content: flex-end; gap: 0.5rem;">
            <button class="btn btn-secondary" type="button" data-action="close-notif-prefs">Cancel</button>
            <button class="btn btn-primary" type="button" data-action="save-notif-prefs">Save Preferences</button>
          </div>
        </div>
      </div>
    `;
  }

  function renderNotificationCenter() {
    const panel = document.querySelector('#notif-center-panel');
    if (!panel) return;
    if (!state.notifCenterOpen) {
      panel.classList.remove('open');
      panel.classList.add('closed');
      return;
    }
    panel.classList.remove('closed');
    panel.classList.add('open');

    const tabs = [
      { id: 'all', label: 'All' },
      { id: 'social', label: 'Social' },
      { id: 'invitations', label: 'Invites' },
      { id: 'tournaments', label: 'Tournaments' },
      { id: 'achievements', label: 'Achievements' },
    ];
    const tabsHtml = tabs.map(t => `
      <button class="notif-tab ${state.notifTab === t.id ? 'active' : ''}" type="button" data-action="notif-tab" data-tab="${t.id}">${escapeHtml(t.label)}</button>
    `).join('');

    let listHtml = '';
    if (state.notifLoading && (!state.notifications || !state.notifications.length)) {
      listHtml = '<div class="notif-empty"><span class="loader"></span><p>Loading notifications…</p></div>';
    } else if (!state.notifications || !state.notifications.length) {
      listHtml = '<div class="notif-empty"><p>No notifications to display.</p></div>';
    } else {
      listHtml = state.notifications.map(n => {
        let icon = '🔔';
        if (n.notification_type?.startsWith('friend')) icon = '👥';
        else if (n.notification_type?.includes('invitation') || n.notification_type?.includes('rematch')) icon = '⚔️';
        else if (n.notification_type?.includes('tournament')) icon = '🏆';
        else if (n.notification_type?.includes('league')) icon = '🏅';
        else if (n.notification_type?.includes('achievement') || n.notification_type?.includes('milestone') || n.notification_type?.includes('personal_best')) icon = '🎖️';
        else if (n.notification_type === 'direct_message') icon = '💬';

        let navBtn = '';
        if (n.target_type) {
          let label = 'View';
          if (n.target_type === 'invitation') label = 'View Invite';
          else if (n.target_type === 'rematch') label = 'View Challenge';
          else if (n.target_type === 'message') label = 'Open Chat';
          else if (n.target_type === 'tournament') label = 'View Tournament';
          else if (n.target_type === 'league') label = 'View League';
          navBtn = `<button class="btn btn-primary btn-xs" type="button" data-action="notif-navigate" data-id="${n.id}" data-target-type="${escapeHtml(n.target_type)}" data-target-id="${escapeHtml(n.target_id || '')}">${escapeHtml(label)}</button>`;
        }

        return `
          <div class="notif-item ${n.is_read ? 'read' : 'unread'}" data-notif-id="${n.id}">
            <div class="notif-icon">${icon}</div>
            <div class="notif-body-wrap">
              <div class="notif-title">${escapeHtml(n.title)}</div>
              <div class="notif-text">${escapeHtml(n.body)}</div>
              <div class="notif-meta">
                <span>${timeAgo(n.created_at)}</span>
              </div>
              <div class="notif-actions">
                ${navBtn}
                <button class="btn btn-ghost btn-xs" type="button" data-action="notif-toggle-read" data-id="${n.id}" title="${n.is_read ? 'Mark as unread' : 'Mark as read'}">${n.is_read ? 'Mark unread' : 'Mark read'}</button>
                <button class="btn btn-ghost btn-xs" type="button" data-action="notif-delete" data-id="${n.id}" title="Delete notification">🗑️</button>
              </div>
            </div>
          </div>
        `;
      }).join('');
    }

    panel.innerHTML = `
      <div class="notif-header">
        <h3>🔔 Notifications ${state.notifUnreadCount > 0 ? `<span class="notif-badge" style="position:static;display:inline-block;">${state.notifUnreadCount}</span>` : ''}</h3>
        <div class="notif-header-actions">
          <button class="btn btn-secondary btn-sm" type="button" data-action="open-notif-prefs" title="Preferences">⚙️</button>
          <button class="btn btn-secondary btn-sm" type="button" data-action="close-notif-center" title="Close">✕</button>
        </div>
      </div>
      <div class="notif-tabs">
        ${tabsHtml}
      </div>
      <div class="notif-toolbar">
        <label style="display:flex;align-items:center;gap:4px;cursor:pointer;">
          <input type="checkbox" id="notif-unread-only-toggle" ${state.notifUnreadOnly ? 'checked' : ''} data-action="toggle-notif-unread-only"> Unread only
        </label>
        <div style="display:flex;gap:6px;">
          <button class="btn btn-ghost btn-sm" type="button" data-action="notif-mark-all-read">Mark all read</button>
          <button class="btn btn-ghost btn-sm" type="button" data-action="notif-clear-read">Clear read</button>
        </div>
      </div>
      <div class="notif-list">
        ${listHtml}
      </div>
    `;
  }

  function renderOverviewTab() {
    const o = state.socialOverview || {
      friend_count: state.friendsList.length,
      online_friends_count: 0,
      pending_requests_count: state.incomingRequests.length,
      pending_invitations_count: state.incomingInvitations.length,
      pending_challenges_count: state.incomingRematches.length,
      unread_messages_count: (state.conversations || []).reduce((acc, c) => acc + (c.unread_count || 0), 0),
      recent_activity: [],
    };

    const hasRequests = o.pending_requests_count > 0;
    const hasInvites = o.pending_invitations_count > 0;
    const hasChallenges = o.pending_challenges_count > 0;
    const hasUnreadDms = o.unread_messages_count > 0;

    let activityListHtml = '';
    const activities = o.recent_activity || [];
    if (activities.length === 0) {
      activityListHtml = `
        <div class="social-empty-state" style="padding:1.5rem 1rem;">
          <p style="color:#8e90b8;margin:0;">No recent activity yet in your circle. Connect with players, join tournaments, or play matches!</p>
        </div>
      `;
    } else {
      activityListHtml = activities.map(act => {
        let icon = '👥';
        if (act.type === 'tournament_podium') icon = '🏆';
        else if (act.type === 'league_podium') icon = '🥇';
        return `
          <div class="overview-activity-card">
            <div style="display:flex;align-items:center;gap:0.75rem;">
              <span style="font-size:1.4rem;">${icon}</span>
              <div class="overview-act-main">
                <span class="overview-act-title">${escapeHtml(act.title)}</span>
                <span class="overview-act-desc">${escapeHtml(act.description)}</span>
              </div>
            </div>
            <span class="overview-act-time">${timeAgo(act.timestamp)}</span>
          </div>
        `;
      }).join('');
    }

    return `
      <div class="social-overview-shell">
        <div class="overview-stats-grid">
          <div class="overview-card" data-action="social-tab" data-tab="friends">
            <span class="overview-card-val">${o.friend_count} <span style="font-size:0.85rem;color:#4ade80;">(${o.online_friends_count} Online)</span></span>
            <span class="overview-card-lbl">Friends</span>
          </div>
          <div class="overview-card ${hasRequests ? 'has-pending' : ''}" data-action="social-tab" data-tab="requests">
            <span class="overview-card-val">${o.pending_requests_count}</span>
            <span class="overview-card-lbl">Friend Requests ${hasRequests ? '🔥' : ''}</span>
          </div>
          <div class="overview-card ${hasInvites ? 'has-pending' : ''}" data-action="social-tab" data-tab="invitations">
            <span class="overview-card-val">${o.pending_invitations_count}</span>
            <span class="overview-card-lbl">Room Invitations ${hasInvites ? '⚔️' : ''}</span>
          </div>
          <div class="overview-card ${hasChallenges ? 'has-pending' : ''}" data-action="social-tab" data-tab="invitations">
            <span class="overview-card-val">${o.pending_challenges_count}</span>
            <span class="overview-card-lbl">Rematch Challenges ${hasChallenges ? '⚡' : ''}</span>
          </div>
          <div class="overview-card ${hasUnreadDms ? 'has-pending' : ''}" data-action="social-tab" data-tab="messages">
            <span class="overview-card-val">${o.unread_messages_count}</span>
            <span class="overview-card-lbl">Unread Messages ${hasUnreadDms ? '💬' : ''}</span>
          </div>
        </div>

        <div style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-bottom:1.5rem;">
          <button class="btn btn-secondary btn-sm" type="button" data-action="social-tab" data-tab="search">🔍 Find Players</button>
          <button class="btn btn-secondary btn-sm" type="button" data-action="social-tab" data-tab="messages">💬 Arena Chat / Messages</button>
          <button class="btn btn-secondary btn-sm" type="button" data-action="social-tab" data-tab="friends">👥 Manage Friends</button>
          <button class="btn btn-secondary btn-sm" type="button" data-action="open-notif-prefs">⚙️ Notification Preferences</button>
        </div>

        <div class="overview-activity-section">
          <h3>Recent Circle Activity</h3>
          <div class="overview-activity-list">
            ${activityListHtml}
          </div>
        </div>
      </div>
    `;
  }

  function socialScreen() {
    app.classList.remove('library-shell');
    if (!state.profileCredential) {
      app.innerHTML = `
        <div class="social-shell">
          <div class="social-header">
            <div>
              <h1>Social Hub</h1>
              <p>Connect with players, manage friendships, and build your competitive circle.</p>
            </div>
          </div>
          <div class="inline-profile-card">
            <div class="section-kicker">OPTIONAL PLAYER IDENTITY</div>
            <h3>Create Your Player Profile</h3>
            <p>Set a player display name below to unlock friend requests, online presence, direct messaging, room invitations, and rematches. Casual play remains available without a profile.</p>
            <div class="inline-profile-row">
              <input class="field" id="inline-social-profile-name" maxlength="24" autocomplete="nickname" placeholder="Your player name" value="${escapeHtml(state.name || '')}">
              <button class="btn btn-primary" type="button" data-action="inline-social-profile-create" ${state.profileLoading ? 'disabled' : ''}>${state.profileLoading ? 'Creating…' : 'Create & Unlock'}</button>
            </div>
            ${state.profileNotice ? `<p class="library-message" role="alert" style="margin-top:0.75rem;">${escapeHtml(state.profileNotice)}</p>` : ''}
            <div class="inline-profile-footer">
              Already have a saved bearer key? <a href="#" data-action="nav-profile">Sign in with saved credential</a>
            </div>
          </div>
        </div>
      `;
      return;
    }

    const unreadRequests = state.incomingRequests.length;
    const unreadInvitations = state.incomingInvitations.length;
    const unreadMessages = (state.conversations || []).reduce((acc, c) => acc + (c.unread_count || 0), 0);
    const navHtml = `
      <div class="social-nav" role="tablist">
        <button class="social-tab-btn ${state.socialTab === 'overview' ? 'active' : ''}" type="button" data-action="social-tab" data-tab="overview" role="tab" aria-selected="${state.socialTab === 'overview'}">
          Overview
        </button>
        <button class="social-tab-btn ${state.socialTab === 'friends' ? 'active' : ''}" type="button" data-action="social-tab" data-tab="friends" role="tab" aria-selected="${state.socialTab === 'friends'}">
          Friends <span class="social-count">${state.friendsList.length}</span>
        </button>
        <button class="social-tab-btn ${state.socialTab === 'messages' ? 'active' : ''}" type="button" data-action="social-tab" data-tab="messages" role="tab" aria-selected="${state.socialTab === 'messages'}">
          Messages ${unreadMessages > 0 ? `<span class="social-badge">${unreadMessages}</span>` : ''}
        </button>
        <button class="social-tab-btn ${state.socialTab === 'invitations' ? 'active' : ''}" type="button" data-action="social-tab" data-tab="invitations" role="tab" aria-selected="${state.socialTab === 'invitations'}">
          Invitations ${unreadInvitations > 0 ? `<span class="social-badge">${unreadInvitations}</span>` : ''}
        </button>
        <button class="social-tab-btn ${state.socialTab === 'search' ? 'active' : ''}" type="button" data-action="social-tab" data-tab="search" role="tab" aria-selected="${state.socialTab === 'search'}">
          Find Players
        </button>
        <button class="social-tab-btn ${state.socialTab === 'requests' ? 'active' : ''}" type="button" data-action="social-tab" data-tab="requests" role="tab" aria-selected="${state.socialTab === 'requests'}">
          Requests ${unreadRequests > 0 ? `<span class="social-badge">${unreadRequests}</span>` : ''}
        </button>
        <button class="social-tab-btn ${state.socialTab === 'blocked' ? 'active' : ''}" type="button" data-action="social-tab" data-tab="blocked" role="tab" aria-selected="${state.socialTab === 'blocked'}">
          Blocked <span class="social-count">${state.blockedList.length}</span>
        </button>
      </div>
    `;

    let bodyHtml = '';
    if (state.socialLoading && !state.friendsList.length && !state.playerSearchResults.length && !state.conversations.length && !state.socialOverview) {
      bodyHtml = '<div class="loading-state"><span class="loader"></span><p>Loading social data…</p></div>';
    } else if (state.socialTab === 'overview') {
      bodyHtml = renderOverviewTab();
    } else if (state.socialTab === 'friends') {
      bodyHtml = renderFriendsTab();
    } else if (state.socialTab === 'messages') {
      bodyHtml = renderMessagesTab();
    } else if (state.socialTab === 'invitations') {
      bodyHtml = renderInvitationsTab();
    } else if (state.socialTab === 'search') {
      bodyHtml = renderSearchTab();
    } else if (state.socialTab === 'requests') {
      bodyHtml = renderRequestsTab();
    } else if (state.socialTab === 'blocked') {
      bodyHtml = renderBlockedTab();
    }

    const modalHtml = state.selectedPublicProfile ? renderPublicProfileModal() : '';
    const reportModalHtml = state.reportingMessageId ? renderReportModal() : '';

    app.innerHTML = `
      <div class="social-shell">
        <div class="social-header">
          <div>
            <h1>Social Hub</h1>
            <p>Connect with players, manage friendships, and build your competitive circle.</p>
          </div>
          <button class="btn btn-secondary" type="button" data-action="refresh-social-hub">↻ Refresh</button>
        </div>
        ${navHtml}
        ${state.socialNotice ? `<p class="library-message" role="status">${escapeHtml(state.socialNotice)}</p>` : ''}
        <div class="social-tab-content">
          ${bodyHtml}
        </div>
        ${modalHtml}
        ${reportModalHtml}
      </div>
    `;
  }

  
  async function refreshIntelligence(renderBeforeFetch = true) {
    if (!state.profileCredential) {
      state.intelligenceError = 'Sign in or link a player profile to access AI intelligence.';
      render();
      return;
    }
    state.intelligenceLoading = true;
    state.intelligenceError = '';
    if (renderBeforeFetch) render();
    try {
      const [overviewRes, weaknessesRes, diffRes, pathsRes, recsRes, perfRes, reportsRes] = await Promise.all([
        playerApi('/api/intelligence/overview'),
        playerApi('/api/intelligence/weaknesses'),
        playerApi('/api/intelligence/difficulty'),
        playerApi('/api/intelligence/learning-paths'),
        playerApi('/api/intelligence/recommendations'),
        playerApi(`/api/intelligence/performance?time_window=${encodeURIComponent(state.intelligenceTimeWindow || 'recent')}`),
        playerApi('/api/intelligence/reports?limit=5'),
      ]);
      if (overviewRes && overviewRes.response.ok) state.intelligenceOverview = overviewRes.data;
      if (weaknessesRes && weaknessesRes.response.ok) state.intelligenceWeaknesses = weaknessesRes.data;
      if (diffRes && diffRes.response.ok) state.intelligenceDifficulty = diffRes.data;
      if (pathsRes && pathsRes.response.ok) state.intelligenceLearningPath = pathsRes.data.active_path;
      if (recsRes && recsRes.response.ok) state.intelligenceRecommendations = recsRes.data.recommendations || [];
      if (perfRes && perfRes.response.ok) state.intelligencePerformance = perfRes.data;
      if (reportsRes && reportsRes.response.ok) {
        state.intelligenceReports = reportsRes.data.reports || [];
        if (!state.intelligenceSelectedReport && state.intelligenceReports.length > 0) {
          state.intelligenceSelectedReport = state.intelligenceReports[0];
        }
      }
    } catch (err) {
      state.intelligenceError = err.message || 'Failed to load AI intelligence data.';
    } finally {
      state.intelligenceLoading = false;
      render();
    }
  }

  function renderIntelOverview() {
    const ov = state.intelligenceOverview || {};
    const sum = ov.summary || {};
    const diff = state.intelligenceDifficulty || {};
    const path = state.intelligenceLearningPath;
    const topRec = (state.intelligenceRecommendations && state.intelligenceRecommendations.length > 0) ? state.intelligenceRecommendations[0] : null;
    const weaknesses = state.intelligenceWeaknesses || {};
    const topWeak = (weaknesses.weak_topics && weaknesses.weak_topics.length > 0) ? weaknesses.weak_topics[0] : null;

    return `
      <div class="intel-grid">
        <div class="intel-card">
          <h3>⚡ AI Mastery Status</h3>
          <div style="font-size: 1.8rem; font-weight: 800; color: #fff; margin: 0.25rem 0;">${escapeHtml(sum.mastery_level || 'Apprentice')}</div>
          <div class="intel-card-desc">
            Evaluated <strong>${sum.total_questions_answered || 0}</strong> questions with <strong>${Math.round(sum.overall_accuracy || 0)}%</strong> overall accuracy.
          </div>
          <div style="margin-top: 0.5rem; display: flex; gap: 0.5rem; flex-wrap: wrap;">
            <span class="intel-stat-badge intel-badge-strong">🎯 ${Math.round(sum.overall_accuracy || 0)}% Accuracy</span>
            <span class="intel-stat-badge intel-badge-diff">📚 ${sum.total_topics_tracked || 0} Topics</span>
          </div>
        </div>

        <div class="intel-card">
          <h3>🎯 Adaptive Difficulty</h3>
          <div class="intel-card-desc">Real-time match difficulty recommendation tailored to your performance threshold.</div>
          <div style="margin: 0.4rem 0;">
            <div style="font-size: 0.82rem; color: #8e91b5;">Current: <strong style="color: #fff; text-transform: uppercase;">${escapeHtml(diff.current_difficulty || state.preferences?.difficulty || 'medium')}</strong></div>
            <div style="font-size: 1.4rem; font-weight: 800; color: #a78bfa; text-transform: uppercase;">${escapeHtml(diff.recommended_difficulty || 'medium')}</div>
            <div style="font-size: 0.78rem; color: #7c5cff;">Confidence: ${Math.round((diff.confidence || 0) * 100)}%</div>
          </div>
          <button class="btn btn-primary" type="button" data-action="intel-tab" data-tab="difficulty">Inspect &amp; Apply →</button>
        </div>

        <div class="intel-card">
          <h3>🧭 Active Learning Path</h3>
          ${path ? `
            <div style="font-weight: 700; color: #fff; font-size: 0.95rem;">${escapeHtml(path.title)}</div>
            <div class="intel-card-desc">Topic: <strong>${escapeHtml(path.topic)}</strong> (${escapeHtml(path.target_difficulty)})</div>
            <div style="margin: 0.5rem 0;">
              <div class="topic-progress-bg">
                <div class="topic-progress-fill topic-fill-strong" style="width: ${Math.min(100, Math.max(0, path.progress_percent || 0))}%;"></div>
              </div>
              <div style="font-size: 0.76rem; color: #8587a8; margin-top: 0.25rem;">${Math.round(path.progress_percent || 0)}% Complete</div>
            </div>
            <button class="btn btn-primary" type="button" data-action="intel-tab" data-tab="paths">Continue Path →</button>
          ` : `
            <div class="intel-card-desc">No active path right now. Generate an AI-structured step-by-step learning progression.</div>
            <button class="btn btn-primary" style="margin-top: auto;" type="button" data-action="regenerate-path">Generate Learning Path</button>
          `}
        </div>

        <div class="intel-card">
          <h3>💡 Top Recommendation</h3>
          ${topRec ? `
            <div style="font-weight: 700; color: #fff; font-size: 0.95rem;">${escapeHtml(topRec.title)}</div>
            <div class="rec-reason" style="margin: 0.25rem 0;">${escapeHtml(topRec.reason)}</div>
            <div style="display: flex; justify-content: space-between; align-items: center; margin-top: auto;">
              <span class="intel-stat-badge intel-badge-diff">${Math.round(topRec.match_score * 100)}% Match</span>
              <button class="btn btn-primary" type="button" data-action="start-rec-quiz" data-category="${escapeHtml(topRec.category)}" data-difficulty="${escapeHtml(topRec.difficulty)}">Practice Now</button>
            </div>
          ` : `
            <div class="intel-card-desc">Complete a few quizzes to reveal personalized intelligent recommendations.</div>
            <button class="btn btn-secondary" style="margin-top: auto;" type="button" data-action="intel-tab" data-tab="recommendations">Explore All</button>
          `}
        </div>
      </div>

      ${topWeak ? `
        <div class="intel-card" style="border-left: 4px solid #ef4444; background: linear-gradient(135deg, #1f1424, #14162e);">
          <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem;">
            <div>
              <strong style="color: #f87171; font-size: 0.95rem;">Growth Opportunity Detected: ${escapeHtml(topWeak.topic)}</strong>
              <div style="font-size: 0.82rem; color: #b2b5dc; margin-top: 0.2rem;">
                Accuracy is currently ${Math.round(topWeak.accuracy)}% across ${topWeak.attempts} questions. Strengthen this weak topic with targeted practice.
              </div>
            </div>
            <button class="btn btn-primary" type="button" data-action="start-rec-quiz" data-category="${escapeHtml(topWeak.topic)}" data-difficulty="easy">Strengthen Topic</button>
          </div>
        </div>
      ` : ''}
    `;
  }

  function renderIntelTopics() {
    const weaknesses = state.intelligenceWeaknesses || {};
    const strong = weaknesses.strong_topics || [];
    const weak = weaknesses.weak_topics || [];
    const improving = weaknesses.improving_topics || [];
    const declining = weaknesses.declining_topics || [];

    const renderTopicItem = (t, statusClass, badgeClass, label) => `
      <div class="topic-item">
        <div class="topic-header">
          <span class="topic-title">${escapeHtml(t.topic)}</span>
          <span class="intel-stat-badge ${badgeClass}">${label} (${Math.round(t.accuracy)}%)</span>
        </div>
        <div class="topic-meta" style="margin-bottom: 0.4rem;">
          ${t.attempts} attempts &bull; Avg response: ${Number(t.avg_answer_time_seconds || 0).toFixed(1)}s &bull; Trend: <strong>${escapeHtml(t.trend || 'stable')}</strong>
        </div>
        <div class="topic-progress-bg">
          <div class="topic-progress-fill ${statusClass}" style="width: ${Math.min(100, Math.max(5, t.accuracy))}%;"></div>
        </div>
      </div>
    `;

    return `
      <div class="intel-grid" style="grid-template-columns: 1fr 1fr;">
        <div class="intel-card">
          <h3>🌟 Mastery Strengths (${strong.length})</h3>
          <div class="intel-card-desc">Topics where you have achieved high consistency (accuracy &ge; 75%).</div>
          <div class="topic-list" style="margin-top: 0.5rem;">
            ${strong.length > 0 ? strong.map(t => renderTopicItem(t, 'topic-fill-strong', 'intel-badge-strong', 'Strong')).join('') : '<div class="intel-card-desc">No strong mastery topics yet. Keep practicing!</div>'}
          </div>
        </div>

        <div class="intel-card">
          <h3>⚠️ Focus &amp; Growth Areas (${weak.length})</h3>
          <div class="intel-card-desc">Topics below target mastery (accuracy &lt; 60%) where targeted drills will accelerate improvement.</div>
          <div class="topic-list" style="margin-top: 0.5rem;">
            ${weak.length > 0 ? weak.map(t => renderTopicItem(t, 'topic-fill-weak', 'intel-badge-weak', 'Needs Practice')).join('') : '<div class="intel-card-desc">No weak topics identified! You are performing well across all categories.</div>'}
          </div>
        </div>
      </div>

      <div class="intel-card" style="margin-top: 1rem;">
        <h3>📈 Performance Trajectory</h3>
        <div style="display: flex; gap: 1.5rem; flex-wrap: wrap; margin-top: 0.5rem;">
          <div style="flex: 1; min-width: 250px;">
            <h4 style="color: #4ade80; font-size: 0.9rem; margin-bottom: 0.4rem;">🚀 Improving Topics</h4>
            ${improving.length > 0 ? improving.map(t => `<div style="font-size: 0.84rem; color: #d1d5db; padding: 0.2rem 0;">&bull; <strong>${escapeHtml(t.topic)}</strong> (+${Math.round(t.recent_accuracy - t.accuracy)}% recent boost)</div>`).join('') : '<div style="font-size: 0.8rem; color: #8587a8;">No trending improvement data yet.</div>'}
          </div>
          <div style="flex: 1; min-width: 250px;">
            <h4 style="color: #f87171; font-size: 0.9rem; margin-bottom: 0.4rem;">📉 Declining Topics</h4>
            ${declining.length > 0 ? declining.map(t => `<div style="font-size: 0.84rem; color: #d1d5db; padding: 0.2rem 0;">&bull; <strong>${escapeHtml(t.topic)}</strong> (${Math.round(t.recent_accuracy - t.accuracy)}% recent drop)</div>`).join('') : '<div style="font-size: 0.8rem; color: #8587a8;">No declining trends detected. Excellent stability!</div>'}
          </div>
        </div>
      </div>
    `;
  }

  function renderIntelDifficulty() {
    const diff = state.intelligenceDifficulty || {};
    const cur = diff.current_difficulty || state.preferences?.difficulty || 'medium';
    const rec = diff.recommended_difficulty || 'medium';
    const isDifferent = cur.toLowerCase() !== rec.toLowerCase();

    return `
      <div class="intel-card">
        <div class="diff-hero">
          <div class="diff-badge-current">CURRENT SOLO DIFFICULTY: <strong style="color: #fff; text-transform: uppercase;">${escapeHtml(cur)}</strong></div>
          <div class="diff-badge-recommended" style="color: ${rec === 'hard' ? '#f87171' : rec === 'easy' ? '#4ade80' : '#a78bfa'};">${escapeHtml(rec)}</div>
          <div style="font-size: 0.88rem; color: #9497bc;">Recommendation Confidence: <strong>${Math.round((diff.confidence || 0) * 100)}%</strong></div>

          <div class="diff-reason-box">
            ${escapeHtml(diff.reason || 'Difficulty recommendation based on your recent attempt accuracy and question volume.')}
          </div>

          <div style="display: flex; gap: 0.75rem; margin-top: 0.5rem; flex-wrap: wrap; justify-content: center;">
            <button class="btn btn-primary" type="button" data-action="apply-recommended-diff" data-difficulty="${escapeHtml(rec)}">
              ✓ Apply Recommended Difficulty (${escapeHtml(rec.toUpperCase())})
            </button>
            <button class="btn btn-secondary" type="button" data-action="refresh-intel">↻ Re-evaluate</button>
          </div>
        </div>
      </div>

      <div class="intel-grid" style="margin-top: 1.5rem;">
        <div class="intel-card">
          <h3>⚖️ Adaptive Promotion Thresholds</h3>
          <div class="intel-card-desc">How QuizVerse AI calculates solo practice difficulty adaptations:</div>
          <ul style="font-size: 0.82rem; color: #b2b5dc; line-height: 1.6; padding-left: 1.2rem; margin: 0.5rem 0;">
            <li><strong>Promotion to Hard:</strong> &ge; 80% accuracy across at least 3 attempts.</li>
            <li><strong>Maintenance:</strong> 50% to 79% accuracy maintains optimal challenge level.</li>
            <li><strong>Demotion to Easy:</strong> &lt; 50% accuracy reduces difficulty to rebuild mastery.</li>
            <li><strong>Evidence Requirement:</strong> At least 3 question attempts required before triggering difficulty promotion or demotion.</li>
          </ul>
        </div>

        <div class="intel-card">
          <h3>🔒 Fair Competitive Boundaries</h3>
          <div class="intel-card-desc">Safety &amp; Integrity Guarantees:</div>
          <div style="font-size: 0.82rem; color: #b2b5dc; line-height: 1.5; margin-top: 0.5rem;">
            Adaptive difficulty is strictly scoped to <strong>Solo Practice</strong> and personalized learning paths.
            Multiplayer rooms, ranked fixture matches, and tournament brackets remain 100% standardized and authoritative.
          </div>
        </div>
      </div>
    `;
  }

  function renderIntelPath() {
    const path = state.intelligenceLearningPath;

    if (!path) {
      return `
        <div class="intel-card" style="text-align: center; padding: 2.5rem 1rem;">
          <h2 style="justify-content: center;">🧭 Personalized Learning Paths</h2>
          <p class="intel-card-desc" style="max-width: 500px; margin: 0.5rem auto 1.5rem;">
            QuizVerse AI synthesizes your weak topics, recent accuracy, and difficulty progression into a 4-stage mastery curriculum.
          </p>
          <button class="btn btn-primary" type="button" data-action="regenerate-path">Generate New Learning Path</button>
        </div>
      `;
    }

    const steps = path.steps || [];
    const completedCount = steps.filter(s => s.status === 'completed' || s.is_completed).length;
    const progressVal = Number(path.progress_pct ?? path.progress_percent ?? (steps.length ? (completedCount / steps.length * 100) : 0)) || 0;

    return `
      <div class="intel-card">
        <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 1rem;">
          <div>
            <h2 style="font-size: 1.3rem;">${escapeHtml(path.title)}</h2>
            <div class="intel-card-desc" style="margin-top: 0.25rem;">
              Focus Topic: <strong style="color: #fff;">${escapeHtml(path.topic || path.target_category || 'General Knowledge')}</strong> &bull; Target: <strong style="color: #a78bfa; text-transform: uppercase;">${escapeHtml(path.target_difficulty || path.recommended_difficulty || 'medium')}</strong>
            </div>
          </div>
          <div style="display: flex; gap: 0.5rem;">
            <button class="btn btn-secondary" type="button" data-action="regenerate-path">↻ Regenerate</button>
          </div>
        </div>

        <div style="margin: 1rem 0;">
          <div style="display: flex; justify-content: space-between; font-size: 0.8rem; color: #8587a8; margin-bottom: 0.35rem;">
            <span>Curriculum Progress (${completedCount} of ${steps.length} Steps)</span>
            <span style="font-weight: 700; color: #fff;">${Math.round(progressVal)}%</span>
          </div>
          <div class="topic-progress-bg">
            <div class="topic-progress-fill topic-fill-strong" style="width: ${Math.min(100, Math.max(0, progressVal))}%;"></div>
          </div>
        </div>

        <div style="display: flex; flex-direction: column; gap: 0.85rem; margin-top: 0.5rem;">
          ${steps.map((s, idx) => {
            const isCompleted = s.status === 'completed' || s.is_completed;
            const isCurrent = !isCompleted && (idx === 0 || (steps[idx - 1].status === 'completed' || steps[idx - 1].is_completed));
            return `
              <div class="path-step-card ${isCompleted ? 'completed' : ''}">
                <div class="path-step-num">${isCompleted ? '✓' : (idx + 1)}</div>
                <div class="path-step-body">
                  <div class="path-step-title">${escapeHtml(s.title)}</div>
                  <div class="path-step-desc">${escapeHtml(s.description)}</div>
                  <div style="font-size: 0.74rem; color: #7c5cff; margin-top: 0.25rem; text-transform: uppercase; font-weight: 700;">
                    Stage: ${escapeHtml(s.step_type || s.activity_type || 'practice')}
                  </div>
                  <div class="path-step-actions">
                    ${isCompleted ? `
                      <span class="intel-stat-badge intel-badge-strong">✓ Completed</span>
                    ` : `
                      <button class="btn btn-primary" type="button" data-action="complete-path-step" data-path-id="${path.id}" data-step-id="${s.id}">
                        Mark Step as Completed
                      </button>
                      <button class="btn btn-secondary" type="button" data-action="start-rec-quiz" data-category="${escapeHtml(path.topic || path.target_category || 'General Knowledge')}" data-difficulty="${escapeHtml(path.target_difficulty || path.recommended_difficulty || 'medium')}">
                        Practice Questions
                      </button>
                    `}
                  </div>
                </div>
              </div>
            `;
          }).join('')}
        </div>
      </div>
    `;
  }

  function renderIntelRecommendations() {
    const recs = state.intelligenceRecommendations || [];

    if (recs.length === 0) {
      return `
        <div class="intel-card" style="text-align: center; padding: 2.5rem 1rem;">
          <h2 style="justify-content: center;">💡 Personalized Recommendations</h2>
          <p class="intel-card-desc" style="max-width: 500px; margin: 0.5rem auto 1.5rem;">
            Play a few solo or multiplayer matches to give QuizVerse AI the performance data needed to suggest personalized study quizzes.
          </p>
          <button class="btn btn-primary" type="button" data-action="refresh-intel">↻ Refresh Recommendations</button>
        </div>
      `;
    }

    return `
      <div style="margin-bottom: 1rem; display: flex; justify-content: space-between; align-items: center;">
        <div style="font-size: 0.88rem; color: #9497bc;">Found <strong>${recs.length}</strong> AI-tailored study recommendations.</div>
        <button class="btn btn-secondary" type="button" data-action="refresh-intel">↻ Refresh</button>
      </div>

      <div class="intel-grid">
        ${recs.map(r => `
          <div class="rec-card">
            <div class="rec-card-top">
              <span class="intel-stat-badge intel-badge-diff">${Math.round(r.match_score * 100)}% Match</span>
              <span class="intel-stat-badge intel-badge-practice" style="text-transform: uppercase;">${escapeHtml(r.difficulty)}</span>
            </div>
            <div class="rec-title">${escapeHtml(r.title)}</div>
            <div style="font-size: 0.8rem; color: #8587a8;">Category: <strong>${escapeHtml(r.category)}</strong> &bull; Mode: <strong>${escapeHtml(r.mode || 'classic')}</strong></div>
            <div class="rec-reason">${escapeHtml(r.reason)}</div>
            <div style="margin-top: auto; padding-top: 0.5rem;">
              <button class="btn btn-primary" style="width: 100%;" type="button" data-action="start-rec-quiz" data-category="${escapeHtml(r.category)}" data-difficulty="${escapeHtml(r.difficulty)}">
                Practice This Category
              </button>
            </div>
          </div>
        `).join('')}
      </div>
    `;
  }

  function renderIntelPerformance() {
    const perf = state.intelligencePerformance || {};
    const win = state.intelligenceTimeWindow || 'recent';
    const accuracy = Math.round(perf.overall_accuracy || 0);
    const questionsAnswered = perf.questions_answered || 0;
    const avgTime = Number(perf.avg_answer_time_seconds || 0).toFixed(1);
    const currStreak = perf.current_streak || 0;
    const maxStreak = perf.max_streak || 0;

    const diffBreakdown = perf.difficulty_breakdown || {};
    const modeBreakdown = perf.mode_breakdown || {};

    return `
      <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.75rem; margin-bottom: 1.25rem;">
        <div style="display: flex; gap: 0.4rem; flex-wrap: wrap;">
          <button class="intel-tab-btn ${win === 'recent' ? 'active' : ''}" type="button" data-action="set-perf-window" data-window="recent">Recent (Last 10)</button>
          <button class="intel-tab-btn ${win === '7_days' ? 'active' : ''}" type="button" data-action="set-perf-window" data-window="7_days">Last 7 Days</button>
          <button class="intel-tab-btn ${win === '30_days' ? 'active' : ''}" type="button" data-action="set-perf-window" data-window="30_days">Last 30 Days</button>
          <button class="intel-tab-btn ${win === 'all_time' ? 'active' : ''}" type="button" data-action="set-perf-window" data-window="all_time">All Time</button>
        </div>
        <button class="btn btn-secondary" type="button" data-action="refresh-intel">↻ Refresh Analytics</button>
      </div>

      <div class="intel-grid">
        <div class="intel-card">
          <h3>🎯 Accuracy</h3>
          <div style="font-size: 2.2rem; font-weight: 800; color: #4ade80;">${accuracy}%</div>
          <div class="intel-card-desc">Correct answers across selected window.</div>
        </div>

        <div class="intel-card">
          <h3>📝 Questions Evaluated</h3>
          <div style="font-size: 2.2rem; font-weight: 800; color: #fff;">${questionsAnswered}</div>
          <div class="intel-card-desc">Total questions answered in this period.</div>
        </div>

        <div class="intel-card">
          <h3>⚡ Speed &amp; Precision</h3>
          <div style="font-size: 2.2rem; font-weight: 800; color: #a78bfa;">${avgTime}s</div>
          <div class="intel-card-desc">Average response time per question.</div>
        </div>

        <div class="intel-card">
          <h3>🔥 Streaks</h3>
          <div style="font-size: 2.2rem; font-weight: 800; color: #facc15;">${currStreak} <span style="font-size: 1rem; color: #8587a8;">(Max: ${maxStreak})</span></div>
          <div class="intel-card-desc">Current and peak consecutive correct answers.</div>
        </div>
      </div>

      <div class="intel-grid" style="grid-template-columns: 1fr 1fr; margin-top: 1rem;">
        <div class="intel-card">
          <h3>📊 Breakdown by Difficulty</h3>
          <div style="display: flex; flex-direction: column; gap: 0.75rem; margin-top: 0.5rem;">
            ${['easy', 'medium', 'hard'].map(d => {
              const data = diffBreakdown[d] || { accuracy: 0, attempts: 0 };
              return `
                <div>
                  <div style="display: flex; justify-content: space-between; font-size: 0.82rem; margin-bottom: 0.25rem;">
                    <span style="font-weight: 700; text-transform: uppercase;">${d}</span>
                    <span style="color: #8587a8;">${Math.round(data.accuracy)}% (${data.attempts} questions)</span>
                  </div>
                  <div class="topic-progress-bg">
                    <div class="topic-progress-fill ${data.accuracy >= 75 ? 'topic-fill-strong' : data.accuracy < 50 ? 'topic-fill-weak' : 'topic-fill-practice'}" style="width: ${Math.min(100, Math.max(0, data.accuracy))}%;"></div>
                  </div>
                </div>
              `;
            }).join('')}
          </div>
        </div>

        <div class="intel-card">
          <h3>🎮 Breakdown by Game Mode</h3>
          <div style="display: flex; flex-direction: column; gap: 0.5rem; margin-top: 0.5rem;">
            ${Object.keys(modeBreakdown).length > 0 ? Object.entries(modeBreakdown).map(([mode, data]) => `
              <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.84rem; padding: 0.35rem 0; border-bottom: 1px solid #1f2240;">
                <span style="font-weight: 600; text-transform: capitalize;">${escapeHtml(mode.replace('_', ' '))}</span>
                <span class="intel-stat-badge intel-badge-strong">${Math.round(data.accuracy || 0)}% (${data.attempts || 0} q's)</span>
              </div>
            `).join('') : '<div class="intel-card-desc">No match records for this time window.</div>'}
          </div>
        </div>
      </div>
    `;
  }

  function renderIntelReports() {
    const reports = state.intelligenceReports || [];
    const active = state.intelligenceSelectedReport || (reports.length > 0 ? reports[0] : null);

    return `
      <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.75rem; margin-bottom: 1.25rem;">
        <div>
          <h2 style="font-size: 1.3rem; margin: 0; color: #fff;">AI Learning Reports</h2>
          <div style="font-size: 0.82rem; color: #8587a8;">In-depth synthesis of strengths, weaknesses, and personalized study schedules.</div>
        </div>
        <button class="btn btn-primary" type="button" data-action="generate-report-btn">
          ✨ Generate Comprehensive AI Report
        </button>
      </div>

      ${reports.length > 0 ? `
        <div style="display: flex; gap: 0.5rem; overflow-x: auto; padding-bottom: 0.5rem; margin-bottom: 1rem;">
          ${reports.map(r => `
            <button class="intel-tab-btn ${active && active.id === r.id ? 'active' : ''}" type="button" data-action="select-report" data-report-id="${r.id}">
              Report ${escapeHtml(r.created_at ? new Date(r.created_at).toLocaleDateString() : 'Recent')} (${escapeHtml(r.time_window || 'recent')})
            </button>
          `).join('')}
        </div>
      ` : ''}

      ${active ? `
        <div class="report-container">
          <div class="report-header">
            <div>
              <div style="font-weight: 700; font-size: 1.15rem; color: #fff;">Personalized Learning Assessment</div>
              <div style="font-size: 0.78rem; color: #8587a8;">Generated on ${escapeHtml(active.created_at || 'Just now')} &bull; Window: <strong>${escapeHtml(active.time_window || 'recent')}</strong></div>
            </div>
            <div>
              <span class="intel-stat-badge intel-badge-strong" style="font-size: 0.9rem; padding: 0.35rem 0.75rem;">
                Overall Score: ${Math.round(active.overall_score || 0)}/100
              </span>
            </div>
          </div>

          <div class="report-summary-box">
            <div style="font-weight: 700; color: #a78bfa; margin-bottom: 0.3rem;">Executive Summary</div>
            ${escapeHtml(active.executive_summary || active.summary || 'Summary unavailable.')}
          </div>

          <div class="intel-grid" style="grid-template-columns: 1fr 1fr;">
            <div class="intel-card">
              <h3 style="color: #4ade80;">🌟 Key Strengths</h3>
              <div class="report-advice-list">
                ${(active.key_strengths || []).length > 0 ? active.key_strengths.map(s => `
                  <div class="report-advice-item">
                    <span style="color: #4ade80;">✓</span>
                    <span>${escapeHtml(s)}</span>
                  </div>
                `).join('') : '<div style="font-size: 0.8rem; color: #8587a8;">Keep practicing to develop signature strengths.</div>'}
              </div>
            </div>

            <div class="intel-card">
              <h3 style="color: #f87171;">🎯 Growth Areas</h3>
              <div class="report-advice-list">
                ${(active.growth_areas || []).length > 0 ? active.growth_areas.map(g => `
                  <div class="report-advice-item">
                    <span style="color: #f87171;">&bull;</span>
                    <span>${escapeHtml(g)}</span>
                  </div>
                `).join('') : '<div style="font-size: 0.8rem; color: #8587a8;">No critical weaknesses identified!</div>'}
              </div>
            </div>
          </div>

          <div class="intel-card">
            <h3 style="color: #a78bfa;">📋 Recommended Action Plan</h3>
            <div class="report-advice-list">
              ${(active.actionable_recommendations || []).length > 0 ? active.actionable_recommendations.map((a, i) => `
                <div class="report-advice-item">
                  <span style="font-weight: 700; color: #7c5cff;">${i + 1}.</span>
                  <span>${escapeHtml(a)}</span>
                </div>
              `).join('') : '<div style="font-size: 0.8rem; color: #8587a8;">Complete more matches to receive customized study recommendations.</div>'}
            </div>
          </div>

          ${active.study_schedule && Object.keys(active.study_schedule).length > 0 ? `
            <div class="intel-card">
              <h3 style="color: #facc15;">📅 Suggested Weekly Study Schedule</h3>
              <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0.75rem; margin-top: 0.5rem;">
                ${Object.entries(active.study_schedule).map(([day, plan]) => `
                  <div style="background: #111326; border: 1px solid #232545; border-radius: 8px; padding: 0.75rem;">
                    <div style="font-weight: 700; color: #fff; font-size: 0.84rem; text-transform: capitalize;">${escapeHtml(day)}</div>
                    <div style="font-size: 0.78rem; color: #b2b5dc; margin-top: 0.25rem;">${escapeHtml(plan)}</div>
                  </div>
                `).join('')}
              </div>
            </div>
          ` : ''}

          ${active.motivational_sign_off ? `
            <div style="font-style: italic; font-size: 0.85rem; color: #8587a8; text-align: center; padding-top: 0.5rem;">
              &ldquo;${escapeHtml(active.motivational_sign_off)}&rdquo;
            </div>
          ` : ''}
        </div>
      ` : `
        <div class="intel-card" style="text-align: center; padding: 2.5rem 1rem;">
          <h2 style="justify-content: center;">📊 No Learning Reports Generated Yet</h2>
          <p class="intel-card-desc" style="max-width: 500px; margin: 0.5rem auto 1.5rem;">
            Generate an AI learning report to get executive feedback, strength/weakness synthesis, and a study plan.
          </p>
          <button class="btn btn-primary" type="button" data-action="generate-report-btn">Generate AI Report</button>
        </div>
      `}
    `;
  }

  function renderIntelCreator() {
    const analysis = state.intelligenceQuestionAnalysis;

    return `
      <div class="intel-card" style="margin-bottom: 1.5rem;">
        <h2>🛠️ Question Quality &amp; Answer Validation Studio</h2>
        <div class="intel-card-desc">
          Evaluate custom questions with multi-factor AI quality scoring, duplicate detection, and deterministic mathematical/factual answer validation.
        </div>

        <form id="intel-creator-form" style="display: flex; flex-direction: column; gap: 0.85rem; margin-top: 1rem;">
          <div>
            <label style="display: block; font-weight: 700; font-size: 0.84rem; margin-bottom: 0.3rem;">Question Prompt:</label>
            <textarea id="intel-q-prompt" class="form-control" rows="2" placeholder="e.g. What is the derivative of x^2 with respect to x?" style="width: 100%; box-sizing: border-box; background: #111326; border: 1px solid #2b2e54; color: #fff; border-radius: 8px; padding: 0.6rem;"></textarea>
          </div>

          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem;">
            <div>
              <label style="display: block; font-size: 0.8rem; margin-bottom: 0.2rem;">Option A (Index 0):</label>
              <input id="intel-q-opt0" class="form-control" type="text" placeholder="e.g. 2x" style="width: 100%; box-sizing: border-box; background: #111326; border: 1px solid #2b2e54; color: #fff; border-radius: 8px; padding: 0.5rem;" />
            </div>
            <div>
              <label style="display: block; font-size: 0.8rem; margin-bottom: 0.2rem;">Option B (Index 1):</label>
              <input id="intel-q-opt1" class="form-control" type="text" placeholder="e.g. x" style="width: 100%; box-sizing: border-box; background: #111326; border: 1px solid #2b2e54; color: #fff; border-radius: 8px; padding: 0.5rem;" />
            </div>
            <div>
              <label style="display: block; font-size: 0.8rem; margin-bottom: 0.2rem;">Option C (Index 2):</label>
              <input id="intel-q-opt2" class="form-control" type="text" placeholder="e.g. x^2" style="width: 100%; box-sizing: border-box; background: #111326; border: 1px solid #2b2e54; color: #fff; border-radius: 8px; padding: 0.5rem;" />
            </div>
            <div>
              <label style="display: block; font-size: 0.8rem; margin-bottom: 0.2rem;">Option D (Index 3):</label>
              <input id="intel-q-opt3" class="form-control" type="text" placeholder="e.g. 2" style="width: 100%; box-sizing: border-box; background: #111326; border: 1px solid #2b2e54; color: #fff; border-radius: 8px; padding: 0.5rem;" />
            </div>
          </div>

          <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 0.75rem;">
            <div>
              <label style="display: block; font-size: 0.8rem; margin-bottom: 0.2rem;">Correct Answer:</label>
              <select id="intel-q-correct" class="form-control" style="width: 100%; box-sizing: border-box; background: #111326; border: 1px solid #2b2e54; color: #fff; border-radius: 8px; padding: 0.5rem;">
                <option value="0">Option A (Index 0)</option>
                <option value="1">Option B (Index 1)</option>
                <option value="2">Option C (Index 2)</option>
                <option value="3">Option D (Index 3)</option>
              </select>
            </div>
            <div>
              <label style="display: block; font-size: 0.8rem; margin-bottom: 0.2rem;">Category:</label>
              <input id="intel-q-category" class="form-control" type="text" value="Mathematics" style="width: 100%; box-sizing: border-box; background: #111326; border: 1px solid #2b2e54; color: #fff; border-radius: 8px; padding: 0.5rem;" />
            </div>
            <div>
              <label style="display: block; font-size: 0.8rem; margin-bottom: 0.2rem;">Difficulty:</label>
              <select id="intel-q-difficulty" class="form-control" style="width: 100%; box-sizing: border-box; background: #111326; border: 1px solid #2b2e54; color: #fff; border-radius: 8px; padding: 0.5rem;">
                <option value="easy">Easy</option>
                <option value="medium" selected>Medium</option>
                <option value="hard">Hard</option>
              </select>
            </div>
          </div>

          <div>
            <label style="display: block; font-size: 0.8rem; margin-bottom: 0.2rem;">Explanation &amp; Justification:</label>
            <textarea id="intel-q-explanation" class="form-control" rows="2" placeholder="e.g. By the power rule of calculus, d/dx(x^n) = n*x^(n-1). Therefore, d/dx(x^2) = 2x." style="width: 100%; box-sizing: border-box; background: #111326; border: 1px solid #2b2e54; color: #fff; border-radius: 8px; padding: 0.6rem;"></textarea>
          </div>

          <div style="display: flex; gap: 0.75rem; margin-top: 0.5rem;">
            <button class="btn btn-primary" type="button" data-action="analyze-question">
              🔍 Analyze Question Quality
            </button>
          </div>
        </form>
      </div>

      ${analysis ? `
        <div class="intel-card" style="border-left: 4px solid ${analysis.quality_score >= 80 ? '#4ade80' : analysis.quality_score >= 60 ? '#facc15' : '#ef4444'};">
          <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem;">
            <div>
              <h3 style="margin: 0; font-size: 1.15rem;">Analysis Results</h3>
              <div style="font-size: 0.8rem; color: #8587a8;">Multi-factor quality assessment report</div>
            </div>
            <div>
              <span class="intel-stat-badge ${analysis.quality_score >= 80 ? 'intel-badge-strong' : analysis.quality_score >= 60 ? 'intel-badge-practice' : 'intel-badge-weak'}" style="font-size: 1.1rem; padding: 0.4rem 0.8rem;">
                Quality Score: ${Math.round(analysis.quality_score)}/100
              </span>
            </div>
          </div>

          <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.5rem; margin: 1rem 0;">
            <div style="background: #111326; padding: 0.6rem; border-radius: 8px; text-align: center;">
              <div style="font-size: 0.74rem; color: #8587a8;">Clarity</div>
              <div style="font-weight: 700; color: #fff;">${Math.round(analysis.clarity_score || 0)}%</div>
            </div>
            <div style="background: #111326; padding: 0.6rem; border-radius: 8px; text-align: center;">
              <div style="font-size: 0.74rem; color: #8587a8;">Consistency</div>
              <div style="font-weight: 700; color: #fff;">${Math.round(analysis.consistency_score || 0)}%</div>
            </div>
            <div style="background: #111326; padding: 0.6rem; border-radius: 8px; text-align: center;">
              <div style="font-size: 0.74rem; color: #8587a8;">Explanation</div>
              <div style="font-weight: 700; color: #fff;">${Math.round(analysis.explanation_score || 0)}%</div>
            </div>
            <div style="background: #111326; padding: 0.6rem; border-radius: 8px; text-align: center;">
              <div style="font-size: 0.74rem; color: #8587a8;">Ambiguity Risk</div>
              <div style="font-weight: 700; color: #fff;">${Math.round(analysis.ambiguity_score || 0)}%</div>
            </div>
            <div style="background: #111326; padding: 0.6rem; border-radius: 8px; text-align: center;">
              <div style="font-size: 0.74rem; color: #8587a8;">Duplicate Risk</div>
              <div style="font-weight: 700; color: #fff;">${Math.round(analysis.duplicate_risk || 0)}%</div>
            </div>
          </div>

          ${(analysis.issues || []).length > 0 ? `
            <div style="margin-top: 0.5rem;">
              <h4 style="color: #f87171; font-size: 0.88rem; margin-bottom: 0.4rem;">⚠️ Detected Issues:</h4>
              <ul style="font-size: 0.82rem; color: #fca5a5; padding-left: 1.2rem; margin: 0;">
                ${analysis.issues.map(iss => `<li>${escapeHtml(iss)}</li>`).join('')}
              </ul>
            </div>
          ` : '<div style="color: #4ade80; font-size: 0.84rem; margin-top: 0.5rem;">✓ No critical issues detected!</div>'}

          ${(analysis.suggestions || []).length > 0 ? `
            <div style="margin-top: 0.75rem;">
              <h4 style="color: #a78bfa; font-size: 0.88rem; margin-bottom: 0.4rem;">💡 Suggestions for Improvement:</h4>
              <ul style="font-size: 0.82rem; color: #d1d5db; padding-left: 1.2rem; margin: 0;">
                ${analysis.suggestions.map(sug => `<li>${escapeHtml(sug)}</li>`).join('')}
              </ul>
            </div>
          ` : ''}

          ${analysis.answer_validation ? `
            <div style="margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px solid #2b2e54;">
              <h4 style="color: #fff; font-size: 0.88rem; margin-bottom: 0.25rem;">Answer Validation</h4>
              <div style="font-size: 0.82rem; color: ${analysis.answer_validation.is_valid ? '#4ade80' : '#f87171'};">
                ${analysis.answer_validation.is_valid ? '✓ Answer mathematically/factually consistent with options.' : `✗ Validation issue: ${escapeHtml(analysis.answer_validation.reason || 'Verification failed')}`}
              </div>
            </div>
          ` : ''}
        </div>
      ` : ''}
    `;
  }

  async function refreshCommunity(renderBeforeFetch = true) {
    state.communityLoading = true;
    state.communityError = '';
    if (renderBeforeFetch) render();
    try {
      if (state.profileCredential) {
        try {
          const modRes = await playerApi('/api/community/moderation/reports?limit=1');
          state.communityIsModerator = modRes.response.ok;
        } catch { state.communityIsModerator = false; }
      } else {
        state.communityIsModerator = false;
      }

      const tab = state.communityTab || 'discover';
      if (tab === 'discover') {
        const { response, data } = await playerApi('/api/community/overview');
        if (response.ok) state.communityOverview = data;
      } else if (tab === 'explore') {
        const q = state.communitySearchQuery || '';
        const cat = state.communityFilterCategory || '';
        const diff = state.communityFilterDifficulty || '';
        const sort = state.communitySortBy || 'recent';
        const url = `/api/community/quizzes?q=${encodeURIComponent(q)}&category=${encodeURIComponent(cat)}&difficulty=${encodeURIComponent(diff)}&sort_by=${encodeURIComponent(sort)}`;
        const { response, data } = await playerApi(url);
        if (response.ok) state.communitySearchResults = data;
      } else if (tab === 'creators') {
        const { response, data } = await playerApi('/api/community/creators');
        if (response.ok) state.communityCreators = data || [];
      } else if (tab === 'studio') {
        if (state.profileCredential) {
          const profRes = await playerApi('/api/community/creators/me');
          if (profRes.response.ok) {
            state.communityMyProfile = profRes.data;
            const qRes = await playerApi(`/api/community/creators/${profRes.data.creator_id}/quizzes`);
            if (qRes.response.ok) state.communityMyQuizzes = qRes.data || [];
          }
        }
      } else if (tab === 'favorites') {
        if (state.profileCredential) {
          const { response, data } = await playerApi('/api/community/favorites');
          if (response.ok) state.communityFavorites = data || [];
        }
      } else if (tab === 'collections') {
        const { response, data } = await playerApi('/api/community/collections');
        if (response.ok) state.communityCollections = data || [];
      } else if (tab === 'moderation') {
        if (state.communityIsModerator) {
          const [repRes, auditRes] = await Promise.all([
            playerApi('/api/community/moderation/reports'),
            playerApi('/api/community/moderation/audit'),
          ]);
          if (repRes.response.ok) state.communityModerationReports = repRes.data?.items || [];
          if (auditRes.response.ok) state.communityModerationAudit = auditRes.data || [];
        }
      }
    } catch (err) {
      state.communityError = err.message || 'Failed to load community data.';
    } finally {
      state.communityLoading = false;
      render();
    }
  }

  function renderCommQuizCard(q) {
    const isFav = (state.communityFavorites || []).some(f => f.quiz_id === q.quiz_id);
    return `
      <div class="comm-card" data-quiz-id="${escapeHtml(q.quiz_id)}">
        <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 0.5rem;">
          <span class="comm-badge comm-badge-plays" style="text-transform: uppercase;">${escapeHtml(q.difficulty || 'medium')}</span>
          <div style="display: flex; gap: 0.35rem;">
            <span class="comm-badge comm-badge-rating">★ ${q.average_rating || 0} (${q.total_reviews || 0})</span>
            <span class="comm-badge" style="background: #111326; color: #8588ae;">${q.total_plays || 0} plays</span>
          </div>
        </div>

        <div class="comm-card-title">${escapeHtml(q.title || 'Untitled Quiz')}</div>
        <div class="comm-card-desc">${escapeHtml(q.description || 'No description provided.')}</div>

        <div class="comm-card-meta">
          <span>Category: <strong style="color: #fff;">${escapeHtml(q.category || 'General')}</strong></span>
          <span>By: <strong style="color: #a78bfa;">${escapeHtml(q.creator_display_name || 'Creator')}</strong></span>
        </div>

        ${q.quality_score ? `
          <div style="font-size: 0.74rem; color: #4ade80; display: flex; align-items: center; gap: 4px;">
            <span>🧠 AI Quality Assessed:</span> <strong>${Math.round(q.quality_score)}/100</strong>
          </div>
        ` : ''}

        <div style="display: flex; gap: 0.4rem; margin-top: auto; padding-top: 0.5rem; flex-wrap: wrap;">
          <button class="btn btn-primary" style="flex: 1;" type="button" data-action="play-community-quiz" data-quiz-id="${escapeHtml(q.quiz_id)}">
            ▶ Play
          </button>
          <button class="btn btn-secondary" type="button" data-action="toggle-favorite" data-quiz-id="${escapeHtml(q.quiz_id)}" title="Save Quiz">
            ${isFav ? '★ Saved' : '☆ Save'}
          </button>
          <button class="btn btn-secondary" type="button" data-action="open-quiz-reviews" data-quiz-id="${escapeHtml(q.quiz_id)}" data-quiz-title="${escapeHtml(q.title)}" title="Reviews">
            💬
          </button>
          <button class="btn btn-secondary" type="button" data-action="share-community-quiz" data-quiz-id="${escapeHtml(q.quiz_id)}" data-slug="${escapeHtml(q.share_slug || '')}" title="Share">
            🔗
          </button>
          <button class="btn btn-secondary" style="color: #f87171;" type="button" data-action="open-report-modal" data-target-type="quiz" data-target-id="${escapeHtml(q.quiz_id)}" title="Report">
            🚩
          </button>
        </div>
      </div>
    `;
  }

  function renderCommDiscover() {
    const ov = state.communityOverview || {};
    const featured = ov.featured || [];
    const trending = ov.trending || [];
    const popular = ov.popular || [];
    const recent = ov.recent || [];

    return `
      <div style="display: flex; flex-direction: column; gap: 2rem;">
        <div>
          <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 0.75rem;">
            <h2 style="font-size: 1.25rem; color: #fff; margin: 0;">✨ Featured Quizzes</h2>
            <span style="font-size: 0.8rem; color: #8588ae;">Curated high-quality community picks</span>
          </div>
          ${featured.length > 0 ? `
            <div class="comm-grid">${featured.map(renderCommQuizCard).join('')}</div>
          ` : '<div class="comm-card"><p class="comm-card-desc">No featured quizzes yet. Be the first to publish!</p></div>'}
        </div>

        <div>
          <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 0.75rem;">
            <h2 style="font-size: 1.25rem; color: #fff; margin: 0;">🔥 Trending This Week</h2>
            <span style="font-size: 0.8rem; color: #8588ae;">Most active players and verified attempts (last 7 days)</span>
          </div>
          ${trending.length > 0 ? `
            <div class="comm-grid">${trending.map(renderCommQuizCard).join('')}</div>
          ` : '<div class="comm-card"><p class="comm-card-desc">Play community quizzes to spark the trending chart!</p></div>'}
        </div>

        <div>
          <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 0.75rem;">
            <h2 style="font-size: 1.25rem; color: #fff; margin: 0;">👑 All-Time Popular</h2>
            <span style="font-size: 0.8rem; color: #8588ae;">Top played quizzes across the ecosystem</span>
          </div>
          ${popular.length > 0 ? `
            <div class="comm-grid">${popular.map(renderCommQuizCard).join('')}</div>
          ` : '<div class="comm-card"><p class="comm-card-desc">No popular quizzes recorded yet.</p></div>'}
        </div>

        <div>
          <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 0.75rem;">
            <h2 style="font-size: 1.25rem; color: #fff; margin: 0;">⚡ Recently Published</h2>
            <span style="font-size: 0.8rem; color: #8588ae;">Fresh challenges from the community</span>
          </div>
          ${recent.length > 0 ? `
            <div class="comm-grid">${recent.map(renderCommQuizCard).join('')}</div>
          ` : '<div class="comm-card"><p class="comm-card-desc">No recent quizzes.</p></div>'}
        </div>
      </div>
    `;
  }

  function renderCommExplore() {
    const res = state.communitySearchResults || { items: [], total: 0 };
    const items = res.items || [];

    return `
      <div style="display: flex; flex-direction: column; gap: 1.25rem;">
        <form id="comm-search-form" class="comm-filter-bar" onsubmit="event.preventDefault();">
          <input id="comm-search-input" type="search" placeholder="Search by title, description, creator, or topic…" value="${escapeHtml(state.communitySearchQuery || '')}" style="flex: 2; min-width: 200px;" />
          
          <select id="comm-filter-difficulty" style="min-width: 120px;">
            <option value="">All Difficulties</option>
            <option value="easy" ${state.communityFilterDifficulty === 'easy' ? 'selected' : ''}>Easy</option>
            <option value="medium" ${state.communityFilterDifficulty === 'medium' ? 'selected' : ''}>Medium</option>
            <option value="hard" ${state.communityFilterDifficulty === 'hard' ? 'selected' : ''}>Hard</option>
          </select>

          <select id="comm-sort-by" style="min-width: 130px;">
            <option value="recent" ${state.communitySortBy === 'recent' ? 'selected' : ''}>Recently Published</option>
            <option value="popular" ${state.communitySortBy === 'popular' ? 'selected' : ''}>Most Played</option>
            <option value="top_rated" ${state.communitySortBy === 'top_rated' ? 'selected' : ''}>Top Rated</option>
          </select>

          <button class="btn btn-primary" type="button" data-action="exec-comm-search">Search</button>
        </form>

        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.85rem; color: #8588ae;">
          <span>Found <strong>${res.total || 0}</strong> community quizzes</span>
        </div>

        ${items.length > 0 ? `
          <div class="comm-grid">${items.map(renderCommQuizCard).join('')}</div>
        ` : `
          <div class="comm-card" style="text-align: center; padding: 2.5rem 1rem;">
            <h2>🔍 No matching quizzes found</h2>
            <p class="comm-card-desc" style="max-width: 450px; margin: 0.5rem auto 1rem;">
              Try refining your search terms, removing difficulty filters, or browsing our curated discovery tray.
            </p>
          </div>
        `}
      </div>
    `;
  }

  function renderCommCreators() {
    const creators = state.communityCreators || [];

    return `
      <div>
        <div style="margin-bottom: 1.25rem;">
          <h2 style="font-size: 1.3rem; color: #fff; margin: 0;">🌟 Featured Community Creators</h2>
          <div style="font-size: 0.82rem; color: #8588ae;">Follow top educators and authors to receive real-time notifications when they publish new quizzes.</div>
        </div>

        ${creators.length > 0 ? `
          <div class="comm-grid">
            ${creators.map(c => `
              <div class="comm-card comm-creator-card">
                <div style="display: flex; gap: 0.85rem; align-items: center;">
                  <div class="creator-avatar" style="width: 48px; height: 48px; font-size: 1.2rem;">
                    ${escapeHtml((c.creator_display_name || 'C')[0].toUpperCase())}
                  </div>
                  <div>
                    <div style="font-weight: 700; color: #fff; font-size: 1rem;">${escapeHtml(c.creator_display_name)}</div>
                    <div class="comm-badge-plays" style="font-size: 0.76rem; color: #8588ae;">👥 ${c.follower_count || 0} followers &bull; 🎮 ${c.total_plays || 0} plays</div>
                  </div>
                </div>

                <div class="comm-card-desc" style="margin-top: 0.4rem;">
                  ${escapeHtml(c.creator_bio || 'Passionate quiz author and community contributor.')}
                </div>

                ${(c.specializations || []).length > 0 ? `
                  <div class="creator-specs">
                    ${c.specializations.map(s => `<span class="creator-spec-pill">${escapeHtml(s)}</span>`).join('')}
                  </div>
                ` : ''}

                <div style="display: flex; justify-content: space-between; align-items: center; margin-top: auto; padding-top: 0.5rem;">
                  <span class="comm-badge comm-badge-rating">★ ${c.average_rating || 0} (${c.total_reviews || 0})</span>
                  <button class="btn btn-primary" type="button" data-action="toggle-follow-creator" data-creator-id="${escapeHtml(c.creator_id)}" data-id="${escapeHtml(c.creator_id)}">
                    + Follow
                  </button>
                </div>
              </div>
            `).join('')}
          </div>
        ` : `
          <div class="comm-card" style="text-align: center; padding: 2.5rem 1rem;">
            <h2>👤 No creators found yet</h2>
            <p class="comm-card-desc">Publish your first quiz in Creator Studio to join the global creator roster!</p>
          </div>
        `}
      </div>
    `;
  }

  function renderCommStudio() {
    if (!state.profileCredential) {
      return `
        <div class="comm-card" style="text-align: center; padding: 2.5rem 1rem;">
          <h2>🔒 Creator Authentication Required</h2>
          <p class="comm-card-desc" style="max-width: 480px; margin: 0.5rem auto 1.5rem;">
            Creator Studio lets you author custom quizzes, validate questions with AI, and track verified play analytics. Sign in or create a player profile to unlock Creator Studio.
          </p>
          <button class="btn btn-primary" type="button" data-action="nav-profile">Go to Profile</button>
        </div>
      `;
    }

    const p = state.communityMyProfile || {};
    const quizzes = state.communityMyQuizzes || [];

    return `
      <div style="display: flex; flex-direction: column; gap: 1.5rem;">
        <div class="creator-hero-card comm-hero">
          <div class="creator-avatar">${escapeHtml((p.creator_display_name || state.profile?.display_name || 'C')[0].toUpperCase())}</div>
          <div class="creator-meta comm-hero-bio">
            <h2 style="margin: 0; color: #fff; font-size: 1.4rem;">${escapeHtml(p.creator_display_name || state.profile?.display_name || 'Creator')}</h2>
            <div class="comm-hero-bio-text" style="font-size: 0.82rem; color: #a5a8ce; margin: 0.25rem 0;">
              ${escapeHtml(p.creator_bio || 'No bio set. Introduce yourself to players and students!')}
            </div>
            <div style="display: flex; gap: 1rem; flex-wrap: wrap; margin-top: 0.5rem; font-size: 0.8rem; color: #8588ae;">
              <span>📚 <strong>${p.published_quiz_count || 0}</strong> Published</span>
              <span>🎮 <strong>${p.total_plays || 0}</strong> Plays</span>
              <span>⭐ <strong>${p.average_rating || 0}</strong> Rating</span>
              <span class="comm-badge-plays">👥 <strong>${p.follower_count || 0}</strong> Followers</span>
            </div>
          </div>
          <div>
            <button class="btn btn-secondary" type="button" data-action="toggle-edit-creator-profile">✏️ Edit Bio &amp; Tags</button>
          </div>
        </div>

        <div id="comm-edit-profile-box" class="comm-card" style="display: ${state.communityEditingProfile ? 'block' : 'none'};">
          <h3>✏️ Update Creator Profile</h3>
          <form id="comm-edit-creator-form" style="display: flex; flex-direction: column; gap: 0.75rem; margin-top: 0.5rem;">
            <div>
              <label style="font-size: 0.8rem; display: block; margin-bottom: 0.2rem;">Display Name:</label>
              <input id="comm-creator-name-inp" class="form-control" type="text" value="${escapeHtml(p.creator_display_name || '')}" style="width: 100%; box-sizing: border-box; background: #111326; border: 1px solid #2b2e54; color: #fff; padding: 0.5rem; border-radius: 6px;" />
            </div>
            <div>
              <label style="font-size: 0.8rem; display: block; margin-bottom: 0.2rem;">Biography:</label>
              <textarea id="comm-profile-bio" name="comm-creator-bio-inp" class="form-control" rows="2" style="width: 100%; box-sizing: border-box; background: #111326; border: 1px solid #2b2e54; color: #fff; padding: 0.5rem; border-radius: 6px;">${escapeHtml(p.creator_bio || '')}</textarea>
            </div>
            <div>
              <label style="font-size: 0.8rem; display: block; margin-bottom: 0.2rem;">Specializations (comma separated):</label>
              <input id="comm-profile-specialty" name="comm-creator-specs-inp" class="form-control" type="text" value="${escapeHtml((p.specializations || []).join(', '))}" placeholder="e.g. Science, Mathematics, History" style="width: 100%; box-sizing: border-box; background: #111326; border: 1px solid #2b2e54; color: #fff; padding: 0.5rem; border-radius: 6px;" />
            </div>
            <div>
              <label style="font-size: 0.8rem; display: block; margin-bottom: 0.2rem;">Website / Links:</label>
              <input id="comm-profile-website" class="form-control" type="text" placeholder="https://..." style="width: 100%; box-sizing: border-box; background: #111326; border: 1px solid #2b2e54; color: #fff; padding: 0.5rem; border-radius: 6px;" />
            </div>
            <div style="display: flex; gap: 0.5rem;">
              <button class="btn btn-primary" type="button" data-action="save-creator-profile">Save Profile</button>
              <button class="btn btn-secondary" type="button" data-action="toggle-edit-creator-profile">Cancel</button>
            </div>
          </form>
        </div>

        <div class="comm-card">
          <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem;">
            <div>
              <h3 style="margin: 0; font-size: 1.15rem; color: #fff;">✍️ Quick Quiz Authoring &amp; Publishing</h3>
              <div style="font-size: 0.8rem; color: #8588ae;">Write a quiz to test pre-publication validation, AI quality check, and instant community publishing.</div>
            </div>
            <button class="btn btn-secondary" type="button" data-action="toggle-quick-author-form">Toggle Form</button>
          </div>

          <form id="comm-quick-author-form" style="display: ${state.communityAuthoringQuiz ? 'flex' : 'none'}; flex-direction: column; gap: 0.85rem; margin-top: 1rem;">
            <div style="display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 0.75rem;">
              <div>
                <label style="font-size: 0.8rem; display: block; margin-bottom: 0.2rem;">Quiz Title:</label>
                <input id="comm-new-quiz-title" name="studio-q-title" class="form-control" type="text" placeholder="e.g. Deep Sea Creatures 101" style="width: 100%; box-sizing: border-box; background: #111326; border: 1px solid #2b2e54; color: #fff; padding: 0.5rem; border-radius: 6px;" />
              </div>
              <div>
                <label style="font-size: 0.8rem; display: block; margin-bottom: 0.2rem;">Category:</label>
                <input id="comm-new-quiz-category" name="studio-q-category" class="form-control" type="text" placeholder="e.g. Oceanography" style="width: 100%; box-sizing: border-box; background: #111326; border: 1px solid #2b2e54; color: #fff; padding: 0.5rem; border-radius: 6px;" />
              </div>
              <div>
                <label style="font-size: 0.8rem; display: block; margin-bottom: 0.2rem;">Difficulty:</label>
                <select id="comm-new-quiz-difficulty" name="studio-q-diff" class="form-control" style="width: 100%; box-sizing: border-box; background: #111326; border: 1px solid #2b2e54; color: #fff; padding: 0.5rem; border-radius: 6px;">
                  <option value="easy">Easy</option>
                  <option value="medium" selected>Medium</option>
                  <option value="hard">Hard</option>
                </select>
              </div>
            </div>

            <div>
              <label style="font-size: 0.8rem; display: block; margin-bottom: 0.2rem;">Description:</label>
              <input id="comm-new-quiz-desc" class="form-control" type="text" placeholder="Quiz summary..." style="width: 100%; box-sizing: border-box; background: #111326; border: 1px solid #2b2e54; color: #fff; padding: 0.5rem; border-radius: 6px;" />
            </div>

            <div style="background: #111326; padding: 0.85rem; border-radius: 8px; border: 1px solid #212447;">
              <strong style="color: #7c5cff; font-size: 0.88rem;">Question 1:</strong>
              <input id="comm-new-q1-text" name="studio-q1-prompt" class="form-control" type="text" placeholder="Question prompt..." style="width: 100%; box-sizing: border-box; background: #0c0e1e; border: 1px solid #2b2e54; color: #fff; padding: 0.45rem; border-radius: 6px; margin: 0.35rem 0;" />
              <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem;">
                <input id="comm-new-q1-a1" name="studio-q1-o0" class="form-control" type="text" placeholder="Option A" style="background: #0c0e1e; border: 1px solid #2b2e54; color: #fff; padding: 0.4rem; border-radius: 6px;" />
                <input id="comm-new-q1-a2" name="studio-q1-o1" class="form-control" type="text" placeholder="Option B" style="background: #0c0e1e; border: 1px solid #2b2e54; color: #fff; padding: 0.4rem; border-radius: 6px;" />
                <input id="comm-new-q1-a3" name="studio-q1-o2" class="form-control" type="text" placeholder="Option C" style="background: #0c0e1e; border: 1px solid #2b2e54; color: #fff; padding: 0.4rem; border-radius: 6px;" />
                <input id="comm-new-q1-a4" name="studio-q1-o3" class="form-control" type="text" placeholder="Option D" style="background: #0c0e1e; border: 1px solid #2b2e54; color: #fff; padding: 0.4rem; border-radius: 6px;" />
              </div>
            </div>

            <div>
              <button class="btn btn-primary" type="button" data-action="studio-quick-create">
                🚀 Validate &amp; Publish Quiz
              </button>
            </div>
          </form>
        </div>

        <div>
          <h3 style="font-size: 1.2rem; color: #fff; margin-bottom: 0.75rem;">My Authoring Portfolio (${quizzes.length})</h3>
          ${quizzes.length > 0 ? `
            <div class="comm-grid">
              ${quizzes.map(q => {
                const badgeClass = q.status === 'published' ? 'comm-badge-pub' : q.status === 'suspended' ? 'comm-badge-suspended' : q.status === 'pending_review' ? 'comm-badge-pending' : 'comm-badge-draft';
                return `
                  <div class="comm-card">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                      <span class="comm-badge ${badgeClass}" style="text-transform: uppercase;">${escapeHtml(q.status)}</span>
                      <span class="comm-badge" style="background: #111326; color: #8588ae;">${escapeHtml(q.visibility)}</span>
                    </div>

                    <div class="comm-card-title">${escapeHtml(q.title)}</div>
                    <div class="comm-card-desc">${escapeHtml(q.description || 'Custom quiz item.')}</div>

                    <div class="comm-card-meta">
                      <span>Category: <strong style="color: #fff;">${escapeHtml(q.category)}</strong></span>
                      <span class="comm-badge-plays">Plays: <strong style="color: #4ade80;">${q.total_plays || 0}</strong> &bull; ★ ${q.average_rating || 0}</span>
                    </div>

                    <div style="display: flex; gap: 0.4rem; margin-top: auto; padding-top: 0.5rem; flex-wrap: wrap;">
                      ${q.status === 'published' ? `
                        <button class="btn btn-secondary" style="flex: 1;" type="button" data-action="studio-unpublish-quiz" data-quiz-id="${escapeHtml(q.quiz_id)}">
                          Unpublish
                        </button>
                      ` : `
                        <button class="btn btn-primary" style="flex: 1;" type="button" data-action="studio-publish-quiz" data-quiz-id="${escapeHtml(q.quiz_id)}">
                          Publish
                        </button>
                      `}
                      <button class="btn btn-secondary" type="button" data-action="open-creator-analytics" data-creator-id="${escapeHtml(p.creator_id)}" title="Analytics">
                        📊
                      </button>
                    </div>
                  </div>
                `;
              }).join('')}
            </div>
          ` : `
            <div class="comm-card"><p class="comm-card-desc">You have not created any quizzes yet. Use the quick authoring tool above to publish your first one!</p></div>
          `}
        </div>
      </div>
    `;
  }

  function renderCommFavorites() {
    const favs = state.communityFavorites || [];

    return `
      <div>
        <div style="margin-bottom: 1.25rem;">
          <h2 style="font-size: 1.3rem; color: #fff; margin: 0;">⭐ Saved Quizzes (${favs.length})</h2>
          <div style="font-size: 0.82rem; color: #8588ae;">Your private personal bookmarks for quick practice and revision.</div>
        </div>

        ${favs.length > 0 ? `
          <div class="comm-grid">
            ${favs.map(f => `
              <div class="comm-card">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                  <span class="comm-badge comm-badge-plays" style="text-transform: uppercase;">${escapeHtml(f.difficulty)}</span>
                  <span class="comm-badge comm-badge-rating">★ ${f.average_rating || 0}</span>
                </div>

                <div class="comm-card-title">${escapeHtml(f.title)}</div>
                <div class="comm-card-desc">${escapeHtml(f.description || 'Bookmarked quiz.')}</div>

                <div class="comm-card-meta">
                  <span>Category: <strong style="color: #fff;">${escapeHtml(f.category)}</strong></span>
                  <span>By: <strong style="color: #a78bfa;">${escapeHtml(f.creator_display_name)}</strong></span>
                </div>

                <div style="display: flex; gap: 0.5rem; margin-top: auto; padding-top: 0.5rem;">
                  <button class="btn btn-primary" style="flex: 1;" type="button" data-action="play-community-quiz" data-quiz-id="${escapeHtml(f.quiz_id)}">
                    ▶ Play Now
                  </button>
                  <button class="btn btn-secondary" type="button" data-action="remove-favorite" data-quiz-id="${escapeHtml(f.quiz_id)}">
                    ✕ Remove
                  </button>
                </div>
              </div>
            `).join('')}
          </div>
        ` : `
          <div class="comm-card" style="text-align: center; padding: 2.5rem 1rem;">
            <h2>⭐ No saved quizzes</h2>
            <p class="comm-card-desc">Click 'Save' on any quiz in the Discover or Search views to bookmark it here.</p>
          </div>
        `}
      </div>
    `;
  }

  function renderCommCollections() {
    const cols = state.communityCollections || [];
    const active = state.communityActiveCollection;

    return `
      <div style="display: flex; flex-direction: column; gap: 1.5rem;">
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem;">
          <div>
            <h2 style="font-size: 1.3rem; color: #fff; margin: 0;">📚 Curated Playlists &amp; Subject Collections</h2>
            <div style="font-size: 0.82rem; color: #8588ae;">Structured multi-quiz pathways for exam preparation and subject mastery.</div>
          </div>
          <button class="btn btn-primary" type="button" data-action="toggle-new-collection-form">+ Create Collection</button>
        </div>

        <div id="comm-new-collection-box" class="comm-card" style="display: ${state.communityCreatingCollection ? 'block' : 'none'};">
          <h3>📚 Create New Quiz Playlist</h3>
          <form id="comm-create-col-form" style="display: flex; flex-direction: column; gap: 0.75rem; margin-top: 0.5rem;">
            <div>
              <label style="font-size: 0.8rem; display: block; margin-bottom: 0.2rem;">Title:</label>
              <input id="comm-collection-title" name="comm-col-title-inp" class="form-control" type="text" placeholder="e.g. Java Masterclass Playlist" style="width: 100%; box-sizing: border-box; background: #111326; border: 1px solid #2b2e54; color: #fff; padding: 0.5rem; border-radius: 6px;" />
            </div>
            <div>
              <label style="font-size: 0.8rem; display: block; margin-bottom: 0.2rem;">Description:</label>
              <textarea id="comm-collection-desc" name="comm-col-desc-inp" class="form-control" rows="2" placeholder="Describe the focus and goals of this playlist..." style="width: 100%; box-sizing: border-box; background: #111326; border: 1px solid #2b2e54; color: #fff; padding: 0.5rem; border-radius: 6px;"></textarea>
            </div>
            <div style="display: flex; gap: 0.5rem;">
              <button class="btn btn-primary" type="button" data-action="submit-create-collection">Create</button>
              <button class="btn btn-secondary" type="button" data-action="toggle-new-collection-form">Cancel</button>
            </div>
          </form>
        </div>

        ${active ? `
          <div class="comm-card" style="border-color: #7c5cff;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <div>
                <h3 style="margin: 0; color: #fff;">${escapeHtml(active.title)}</h3>
                <div style="font-size: 0.82rem; color: #8588ae;">By ${escapeHtml(active.owner_name)} &bull; ${active.item_count || 0} quizzes</div>
              </div>
              <button class="btn btn-secondary" type="button" data-action="close-active-collection">✕ Close</button>
            </div>

            <p class="comm-card-desc" style="margin-top: 0.5rem;">${escapeHtml(active.description || 'Curriculum playlist.')}</p>

            <div style="display: flex; flex-direction: column; gap: 0.5rem; margin-top: 0.5rem;">
              ${(active.items || []).map((it, idx) => `
                <div class="collection-item-row">
                  <div>
                    <span style="font-weight: 700; color: #7c5cff; margin-right: 0.5rem;">${idx + 1}.</span>
                    <strong style="color: #fff;">${escapeHtml(it.title)}</strong>
                    <span style="font-size: 0.76rem; color: #8588ae; margin-left: 0.5rem;">(${escapeHtml(it.category)})</span>
                  </div>
                  <div style="display: flex; gap: 0.4rem;">
                    <button class="btn btn-primary" style="padding: 0.3rem 0.6rem; font-size: 0.78rem;" type="button" data-action="play-community-quiz" data-quiz-id="${escapeHtml(it.quiz_id)}">
                      Play
                    </button>
                    ${active.owner_id === state.profile?.id ? `
                      <button class="btn btn-secondary" style="padding: 0.3rem 0.6rem; font-size: 0.78rem; color: #f87171;" type="button" data-action="remove-quiz-from-col" data-col-id="${escapeHtml(active.id)}" data-quiz-id="${escapeHtml(it.quiz_id)}">
                        Remove
                      </button>
                    ` : ''}
                  </div>
                </div>
              `).join('')}
            </div>
          </div>
        ` : ''}

        ${cols.length > 0 ? `
          <div class="comm-grid">
            ${cols.map(c => `
              <div class="comm-card">
                <div class="comm-card-title">${escapeHtml(c.title)}</div>
                <div class="comm-card-desc">${escapeHtml(c.description || 'Curated community collection.')}</div>
                <div class="comm-card-meta">
                  <span>Author: <strong style="color: #a78bfa;">${escapeHtml(c.owner_name)}</strong></span>
                  <span><strong>${c.item_count || 0}</strong> quizzes</span>
                </div>
                <div style="margin-top: auto; padding-top: 0.5rem;">
                  <button class="btn btn-primary" style="width: 100%;" type="button" data-action="view-collection" data-col-id="${escapeHtml(c.id)}">
                    Open Playlist (${c.item_count || 0} Quizzes)
                  </button>
                </div>
              </div>
            `).join('')}
          </div>
        ` : `
          <div class="comm-card"><p class="comm-card-desc">No playlists found. Create the first one above!</p></div>
        `}
      </div>
    `;
  }

  function renderCommModeration() {
    const reports = state.communityModerationReports || [];
    const audit = state.communityModerationAudit || [];

    return `
      <div style="display: flex; flex-direction: column; gap: 1.5rem;">
        <div>
          <h2 style="font-size: 1.3rem; color: #fff; margin: 0 0 0.5rem 0;">🛡️ Community Moderation Queue</h2>
          <div style="font-size: 0.82rem; color: #8588ae;">Manage reported quizzes, reviews, and community safety enforcement.</div>
        </div>

        <div class="comm-card" style="padding: 0; overflow-x: auto;">
          <table class="mod-table">
            <thead>
              <tr>
                <th>Target</th>
                <th>Target ID</th>
                <th>Reason</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              ${reports.length > 0 ? reports.map(r => `
                <tr>
                  <td><span class="comm-badge comm-badge-plays">${escapeHtml(r.target_type)}</span></td>
                  <td><code>${escapeHtml(r.target_id.slice(0, 12))}…</code></td>
                  <td><strong style="color: #f87171;">${escapeHtml(r.reason)}</strong></td>
                  <td>
                    <span class="comm-badge ${r.status === 'open' ? 'comm-badge-pending' : 'comm-badge-pub'}">
                      ${escapeHtml(r.status)}
                    </span>
                  </td>
                  <td>
                    <div style="display: flex; gap: 0.35rem;">
                      ${r.target_type === 'quiz' ? `
                        <button class="btn btn-secondary" style="font-size: 0.74rem; padding: 0.25rem 0.5rem; color: #f87171;" type="button" data-action="mod-resolve" data-report-id="${escapeHtml(r.id)}" data-mod-action="suspend_quiz">
                          Suspend
                        </button>
                        <button class="btn btn-secondary" style="font-size: 0.74rem; padding: 0.25rem 0.5rem; color: #4ade80;" type="button" data-action="mod-resolve" data-report-id="${escapeHtml(r.id)}" data-mod-action="restore_quiz">
                          Restore
                        </button>
                      ` : ''}
                      ${r.target_type === 'review' ? `
                        <button class="btn btn-secondary" style="font-size: 0.74rem; padding: 0.25rem 0.5rem; color: #f87171;" type="button" data-action="mod-resolve" data-report-id="${escapeHtml(r.id)}" data-mod-action="hide_review">
                          Hide
                        </button>
                      ` : ''}
                      <button class="btn btn-secondary" style="font-size: 0.74rem; padding: 0.25rem 0.5rem;" type="button" data-action="mod-resolve" data-report-id="${escapeHtml(r.id)}" data-mod-action="dismiss_report">
                        Dismiss
                      </button>
                    </div>
                  </td>
                </tr>
              `).join('') : `
                <tr>
                  <td colspan="5" style="text-align: center; padding: 2rem; color: #8588ae;">
                    Queue clean! No open moderation reports.
                  </td>
                </tr>
              `}
            </tbody>
          </table>
        </div>

        <div>
          <h3 style="font-size: 1.15rem; color: #fff; margin-bottom: 0.5rem;">📜 Moderation Audit Trail</h3>
          <div class="comm-card" style="padding: 0; overflow-x: auto;">
            <table class="mod-table">
              <thead>
                <tr>
                  <th>Moderator</th>
                  <th>Action</th>
                  <th>Target Type</th>
                  <th>Target ID</th>
                  <th>Timestamp</th>
                </tr>
              </thead>
              <tbody>
                ${audit.length > 0 ? audit.map(a => `
                  <tr>
                    <td><strong>${escapeHtml(a.moderator_name)}</strong></td>
                    <td><code>${escapeHtml(a.action_type)}</code></td>
                    <td>${escapeHtml(a.target_type)}</td>
                    <td>${escapeHtml(a.target_id.slice(0, 12))}…</td>
                    <td style="color: #8588ae;">${timeAgo(a.created_at)}</td>
                  </tr>
                `).join('') : `
                  <tr>
                    <td colspan="5" style="text-align: center; padding: 1.5rem; color: #8588ae;">No recent audit logs.</td>
                  </tr>
                `}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    `;
  }

  function renderCommModals() {
    let html = '';
    // Review Modal
    if (state.communityActiveQuiz) {
      const qid = state.communityActiveQuiz.id;
      const qtitle = state.communityActiveQuiz.title;
      const reviews = state.communityActiveReviews || [];
      html += `
        <div id="comm-reviews-modal" class="notif-modal-backdrop" style="display: flex;">
          <div class="comm-card" style="width: 100%; max-width: 580px; max-height: 85vh; overflow-y: auto; background: #13152a; border-color: #3f447a;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <h3 style="margin: 0; color: #fff;">Ratings &amp; Reviews: ${escapeHtml(qtitle)}</h3>
              <button class="btn btn-secondary" type="button" data-action="close-quiz-reviews">✕</button>
            </div>

            <div style="background: #191b38; padding: 1rem; border-radius: 10px; margin: 0.75rem 0;">
              <div style="font-weight: 700; font-size: 0.88rem; color: #fff; margin-bottom: 0.5rem;">Submit Verified Rating:</div>
              <div style="display: flex; gap: 0.5rem; align-items: center; margin-bottom: 0.5rem;">
                <label style="font-size: 0.82rem;">Stars:</label>
                <select id="review-star-rating" style="background: #0e1022; border: 1px solid #2d3058; color: #facc15; padding: 0.35rem; border-radius: 6px;">
                  <option value="5" selected>★★★★★ (5 Stars)</option>
                  <option value="4">★★★★☆ (4 Stars)</option>
                  <option value="3">★★★☆☆ (3 Stars)</option>
                  <option value="2">★★☆☆☆ (2 Stars)</option>
                  <option value="1">★☆☆☆☆ (1 Star)</option>
                </select>
              </div>
              <textarea id="comm-review-text" name="review-text-input" rows="2" placeholder="Write feedback for the quiz creator (optional)..." style="width: 100%; box-sizing: border-box; background: #0e1022; border: 1px solid #2d3058; color: #fff; padding: 0.5rem; border-radius: 6px;"></textarea>
              <button class="btn btn-primary" style="margin-top: 0.5rem;" type="button" data-action="submit-quiz-review" data-quiz-id="${escapeHtml(qid)}">
                Submit Review
              </button>
            </div>

            <div style="display: flex; flex-direction: column; gap: 0.75rem; margin-top: 0.5rem;">
              <div style="font-size: 0.84rem; color: #8588ae; font-weight: 700;">Verified Reviews (${reviews.length}):</div>
              ${reviews.length > 0 ? reviews.map(r => `
                <div class="review-item comm-review-card">
                  <div class="review-header">
                    <div>
                      <strong style="color: #fff;">${escapeHtml(r.reviewer_name)}</strong>
                      <span style="color: #facc15; margin-left: 0.5rem;">${'★'.repeat(r.rating)}${'☆'.repeat(5 - r.rating)}</span>
                    </div>
                    <span style="font-size: 0.75rem; color: #8588ae;">${timeAgo(r.created_at)}</span>
                  </div>
                  ${r.review_text ? `<div class="comm-review-body" style="font-size: 0.84rem; color: #b2b5dc; line-height: 1.4;">${escapeHtml(r.review_text)}</div>` : ''}

                  ${r.creator_response ? `
                    <div class="review-creator-reply">
                      <strong style="color: #a78bfa;">Creator Response:</strong> ${escapeHtml(r.creator_response)}
                    </div>
                  ` : ''}

                  <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.76rem; color: #8588ae; margin-top: 0.25rem;">
                    <button class="btn btn-secondary" style="padding: 0.2rem 0.5rem; font-size: 0.74rem;" type="button" data-action="mark-review-helpful" data-review-id="${escapeHtml(r.id)}">
                      👍 Helpful (${r.helpful_count || 0})
                    </button>
                    ${r.player_id === state.profile?.id ? `
                      <button class="btn btn-secondary" style="padding: 0.2rem 0.5rem; font-size: 0.74rem; color: #f87171;" type="button" data-action="delete-own-review" data-review-id="${escapeHtml(r.id)}">
                        Delete
                      </button>
                    ` : ''}
                  </div>
                </div>
              `).join('') : '<div style="color: #8588ae; font-size: 0.82rem;">No written reviews yet. Be the first!</div>'}
            </div>
          </div>
        </div>
      `;
    }

    // Report Modal
    if (state.communityActiveReportTarget) {
      const tgt = state.communityActiveReportTarget;
      html += `
        <div id="comm-report-modal" class="notif-modal-backdrop" style="display: flex;">
          <div class="comm-card" style="width: 100%; max-width: 460px; background: #13152a; border-color: #63242c;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <h3 style="margin: 0; color: #f87171;">🚩 Report Content</h3>
              <button class="btn btn-secondary" type="button" data-action="close-comm-modal">✕</button>
            </div>
            <p style="font-size: 0.82rem; color: #8588ae; margin: 0.4rem 0;">
              Help keep QuizVerse Live safe. Reports are audited privately by human moderators.
            </p>

            <form id="comm-report-form" style="display: flex; flex-direction: column; gap: 0.75rem; margin-top: 0.5rem;">
              <div>
                <label style="font-size: 0.8rem; display: block; margin-bottom: 0.2rem;">Reason:</label>
                <select id="report-reason-select" style="width: 100%; box-sizing: border-box; background: #0e1022; border: 1px solid #2d3058; color: #fff; padding: 0.5rem; border-radius: 6px;">
                  <option value="inappropriate">Inappropriate / Offensive Content</option>
                  <option value="incorrect_answer">Incorrect or Misleading Question</option>
                  <option value="spam">Spam / Advertising</option>
                  <option value="plagiarism">Plagiarism / Copied Quiz</option>
                  <option value="harassment">Harassment</option>
                  <option value="other">Other Violation</option>
                </select>
              </div>

              <div>
                <label style="font-size: 0.8rem; display: block; margin-bottom: 0.2rem;">Details:</label>
                <textarea id="comm-report-details" name="report-details-text" rows="3" placeholder="Provide context to assist moderator review..." style="width: 100%; box-sizing: border-box; background: #0e1022; border: 1px solid #2d3058; color: #fff; padding: 0.5rem; border-radius: 6px;"></textarea>
              </div>

              <div style="display: flex; gap: 0.5rem;">
                <button class="btn btn-primary" style="background: #dc2626; border-color: #dc2626;" type="button" data-action="submit-community-report" data-target-type="${escapeHtml(tgt.target_type)}" data-target-id="${escapeHtml(tgt.target_id)}">
                  Submit Report
                </button>
                <button class="btn btn-secondary" type="button" data-action="close-comm-modal">Cancel</button>
              </div>
            </form>
          </div>
        </div>
      `;
    }

    // Analytics Modal
    if (state.communityAnalyticsModal) {
      const a = state.communityAnalyticsModal;
      html += `
        <div class="notif-modal-backdrop" style="display: flex;">
          <div class="comm-card" style="width: 100%; max-width: 520px; background: #13152a; border-color: #3f447a;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <h3 style="margin: 0; color: #fff;">📊 Creator Analytics</h3>
              <button class="btn btn-secondary" type="button" data-action="close-comm-modal">✕</button>
            </div>

            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; margin: 1rem 0;">
              <div style="background: #111326; padding: 0.75rem; border-radius: 8px;">
                <div style="font-size: 0.76rem; color: #8588ae;">Total Plays</div>
                <div style="font-size: 1.5rem; font-weight: 800; color: #4ade80;">${a.total_plays || 0}</div>
              </div>
              <div style="background: #111326; padding: 0.75rem; border-radius: 8px;">
                <div style="font-size: 0.76rem; color: #8588ae;">Unique Players</div>
                <div style="font-size: 1.5rem; font-weight: 800; color: #fff;">${a.unique_players || 0}</div>
              </div>
              <div style="background: #111326; padding: 0.75rem; border-radius: 8px;">
                <div style="font-size: 0.76rem; color: #8588ae;">Average Rating</div>
                <div style="font-size: 1.5rem; font-weight: 800; color: #facc15;">★ ${a.average_rating || 0}</div>
              </div>
              <div style="background: #111326; padding: 0.75rem; border-radius: 8px;">
                <div style="font-size: 0.76rem; color: #8588ae;">Followers</div>
                <div style="font-size: 1.5rem; font-weight: 800; color: #a78bfa;">${a.follower_count || 0}</div>
              </div>
            </div>

            <h4 style="color: #fff; font-size: 0.9rem; margin-bottom: 0.4rem;">Top Performing Quizzes</h4>
            <div style="display: flex; flex-direction: column; gap: 0.4rem;">
              ${(a.popular_quizzes || []).map(pq => `
                <div style="display: flex; justify-content: space-between; font-size: 0.82rem; background: #111326; padding: 0.4rem 0.6rem; border-radius: 6px;">
                  <strong style="color: #fff;">${escapeHtml(pq.title)}</strong>
                  <span style="color: #a78bfa;">${pq.total_plays || 0} plays &bull; ★ ${pq.average_rating || 0}</span>
                </div>
              `).join('')}
            </div>
          </div>
        </div>
      `;
    }

    return html;
  }

  function communityScreen() {
    const tab = state.communityTab || 'discover';

    let bodyHtml = '';
    if (state.communityLoading && !state.communityOverview && !state.communitySearchResults && !state.communityCreators.length) {
      bodyHtml = '<div class="comm-card" style="text-align: center; padding: 2rem;"><p>Loading Community Hub…</p></div>';
    } else {
      switch (tab) {
        case 'explore': bodyHtml = renderCommExplore(); break;
        case 'creators': bodyHtml = renderCommCreators(); break;
        case 'studio': bodyHtml = renderCommStudio(); break;
        case 'favorites': bodyHtml = renderCommFavorites(); break;
        case 'collections': bodyHtml = renderCommCollections(); break;
        case 'moderation': bodyHtml = renderCommModeration(); break;
        default: bodyHtml = renderCommDiscover(); break;
      }
    }

    app.innerHTML = `
      <div class="comm-shell">
        <div class="comm-header">
          <div>
            <h1>🌍 Creator &amp; Community Ecosystem</h1>
            <p style="margin: 0.25rem 0 0; font-size: 0.88rem; color: #9497bc;">
              Discover community quizzes, follow top educators, create playlists, and build your creator legacy.
            </p>
          </div>
          <button class="btn btn-secondary" type="button" data-action="refresh-community">↻ Refresh</button>
        </div>

        <nav class="comm-nav" aria-label="Community Tabs">
          <button class="comm-tab-btn ${tab === 'discover' ? 'active' : ''}" type="button" data-action="comm-tab" data-tab="discover">🌟 Discover</button>
          <button class="comm-tab-btn ${tab === 'explore' ? 'active' : ''}" type="button" data-action="comm-tab" data-tab="explore">🔍 Search &amp; Explore</button>
          <button class="comm-tab-btn ${tab === 'creators' ? 'active' : ''}" type="button" data-action="comm-tab" data-tab="creators">👤 Creators</button>
          <button class="comm-tab-btn ${tab === 'studio' ? 'active' : ''}" type="button" data-action="comm-tab" data-tab="studio">🛠️ Creator Studio</button>
          <button class="comm-tab-btn ${tab === 'favorites' ? 'active' : ''}" type="button" data-action="comm-tab" data-tab="favorites">⭐ Saved Quizzes</button>
          <button class="comm-tab-btn ${tab === 'collections' ? 'active' : ''}" type="button" data-action="comm-tab" data-tab="collections">📚 Collections &amp; Playlists</button>
          ${state.communityIsModerator ? `<button class="comm-tab-btn ${tab === 'moderation' ? 'active' : ''}" type="button" data-action="comm-tab" data-tab="moderation">🛡️ Moderation</button>` : ''}
        </nav>

        ${state.communityError ? `<p class="library-message" role="status" style="color: #f87171; border-color: rgba(239, 68, 68, 0.4);">${escapeHtml(state.communityError)}</p>` : ''}

        <div class="comm-tab-content">
          ${bodyHtml}
        </div>

        ${renderCommModals()}
      </div>
    `;
  }

  function intelligenceScreen() {
    const tab = state.intelligenceTab || 'overview';

    let bodyHtml = '';
    if (state.intelligenceLoading && !state.intelligenceOverview) {
      bodyHtml = `
        <div class="intel-grid">
          <div class="skeleton-card" style="padding:1.5rem;"><div class="skeleton-line" style="width:40%;"></div><div class="skeleton-line" style="width:80%;"></div><div class="skeleton-line" style="width:60%;"></div></div>
          <div class="skeleton-card" style="padding:1.5rem;"><div class="skeleton-line" style="width:50%;"></div><div class="skeleton-line" style="width:70%;"></div><div class="skeleton-line" style="width:55%;"></div></div>
        </div>
      `;
    } else if (state.intelligenceError && !state.profileCredential) {
      bodyHtml = `
        <div class="inline-profile-card" style="text-align: left; max-width: 680px; margin: 1.5rem auto;">
          <div class="section-kicker">PERSONALIZED AI LEARNING</div>
          <h3>Unlock AI Intelligence &amp; Mastery</h3>
          <p class="intel-card-desc">
            AI intelligence analyzes your performance, identifies weakness topics, personalizes difficulty, and creates custom curriculums. Set a player name below to initialize your learning profile.
          </p>
          <div class="inline-profile-row">
            <input class="field" id="inline-intel-profile-name" maxlength="24" autocomplete="nickname" placeholder="Your player name" value="${escapeHtml(state.name || '')}">
            <button class="btn btn-primary" type="button" data-action="inline-intel-profile-create" ${state.profileLoading ? 'disabled' : ''}>${state.profileLoading ? 'Creating…' : 'Create & Unlock'}</button>
          </div>
          ${state.profileNotice ? `<p class="library-message" role="alert" style="margin-top:0.75rem;">${escapeHtml(state.profileNotice)}</p>` : ''}
          <div class="inline-profile-footer">
            Already have a saved bearer key? <a href="#" data-action="nav-profile">Sign in with saved credential</a>
          </div>
        </div>
      `;
    } else {
      switch (tab) {
        case 'topics': bodyHtml = renderIntelTopics(); break;
        case 'difficulty': bodyHtml = renderIntelDifficulty(); break;
        case 'paths': bodyHtml = renderIntelPath(); break;
        case 'recommendations': bodyHtml = renderIntelRecommendations(); break;
        case 'performance': bodyHtml = renderIntelPerformance(); break;
        case 'reports': bodyHtml = renderIntelReports(); break;
        case 'creator': bodyHtml = renderIntelCreator(); break;
        default: bodyHtml = renderIntelOverview(); break;
      }
    }

    app.innerHTML = `
      <div class="intel-shell">
        <div class="intel-header">
          <div>
            <h1>🧠 AI Intelligence &amp; Learning Hub</h1>
            <p style="margin: 0.25rem 0 0; font-size: 0.88rem; color: #9497bc;">
              Adaptive learning algorithms, weakness diagnosis, personalized curriculums, and question quality analysis.
            </p>
          </div>
          <button class="btn btn-secondary" type="button" data-action="refresh-intel">↻ Refresh</button>
        </div>

        <nav class="intel-nav" aria-label="AI Intelligence Tabs">
          <button class="intel-tab-btn ${tab === 'overview' ? 'active' : ''}" type="button" data-action="intel-tab" data-tab="overview">Overview</button>
          <button class="intel-tab-btn ${tab === 'topics' ? 'active' : ''}" type="button" data-action="intel-tab" data-tab="topics">Topic Mastery &amp; Weaknesses</button>
          <button class="intel-tab-btn ${tab === 'difficulty' ? 'active' : ''}" type="button" data-action="intel-tab" data-tab="difficulty">Adaptive Difficulty</button>
          <button class="intel-tab-btn ${tab === 'paths' ? 'active' : ''}" type="button" data-action="intel-tab" data-tab="paths">Learning Paths</button>
          <button class="intel-tab-btn ${tab === 'recommendations' ? 'active' : ''}" type="button" data-action="intel-tab" data-tab="recommendations">Recommendations</button>
          <button class="intel-tab-btn ${tab === 'performance' ? 'active' : ''}" type="button" data-action="intel-tab" data-tab="performance">Performance Analytics</button>
          <button class="intel-tab-btn ${tab === 'reports' ? 'active' : ''}" type="button" data-action="intel-tab" data-tab="reports">AI Learning Reports</button>
          <button class="intel-tab-btn ${tab === 'creator' ? 'active' : ''}" type="button" data-action="intel-tab" data-tab="creator">Quality Creator Studio</button>
        </nav>

        ${state.intelligenceError ? `<p class="library-message" role="status" style="color: #f87171; border-color: rgba(239, 68, 68, 0.4);">${escapeHtml(state.intelligenceError)}</p>` : ''}

        <div class="intel-tab-content">
          ${bodyHtml}
        </div>
      </div>
    `;
  }

  function render() {
    if (state.tick) clearInterval(state.tick);
    if (state.view === 'intelligence') {
      intelligenceScreen();
      return;
    }
    if (state.view === 'community') {
      communityScreen();
      return;
    }
    if (state.view === 'social') {
      socialScreen();
      return;
    }
    if (state.view === 'leagues') {
      leaguesScreen();
      return;
    }
    if (state.view === 'tournaments') {
      tournamentsScreen();
      return;
    }
    if (state.view === 'rankings') {
      rankingsScreen();
      return;
    }
    if (state.view === 'profile') {
      profileScreen();
      return;
    }
    if (state.view === 'library') {
      app.classList.add('library-shell');
      if (state.screen !== 'library') {
        state.screen = 'library';
        window.QuizLibrary.mount(app, notify);
      }
      return;
    }
    app.classList.remove('library-shell');
    if (!state.room) {
      state.screen = 'home';
      app.innerHTML = home();
      installModeSelector();
      return;
    }
    const phase = state.room.phase;
    state.screen = phase === 'waiting' ? 'lobby' : phase === 'results' ? 'results' : 'game';
    const offline = state.connected ? '' : '<div class="offline-banner" role="status">Connection interrupted — reconnecting to your arena…</div>';
    app.innerHTML = `${offline}${phase === 'waiting' ? lobby() : phase === 'results' ? results() : game()}${renderRoomChatDrawer()}`;
    if (phase === 'results' && state.room.match_history && state.room.match_history.status !== 'not_linked') {
      const notice = document.createElement('p');
      notice.className = `profile-history-status${state.room.match_history.status === 'retrying' ? ' warning' : ''}`;
      notice.setAttribute('role', 'status');
      notice.textContent = state.room.match_history.status === 'saved'
        ? 'This profile-linked match is saved to your history.'
        : state.room.match_history.status === 'retrying'
          ? 'Match history is not saved yet. The server is retrying; please wait before starting a rematch.'
          : 'Saving this match to your profile history…';
      app.querySelector('.results-card')?.prepend(notice);
    }
    updateModePresentation();
    const roundTag = app.querySelector('.round-tag');
    if (roundTag) roundTag.textContent = `ROUND ${String(Math.min(state.room.round, state.room.total_rounds)).padStart(2, '0')} / ${state.room.total_rounds}`;
    const resultsLabel = app.querySelector('.results-card .section-kicker');
    if (resultsLabel) resultsLabel.textContent = `${state.room.total_rounds} ROUNDS COMPLETE`;
    if (['question','countdown','reveal','submission','voting','escape_puzzle'].includes(phase)) {
      state.tick = setInterval(updateTimer, 100);
      updateTimer();
    }
  }
  function updateTimer() {
    const number = document.querySelector('#timer-number');
    const ring = document.querySelector('#timer-ring');
    if (!number || !ring || !state.room?.deadline) return;
    const left = Math.max(0, (state.room.deadline - (Date.now() + (state.clockOffsetMs || 0))) / 1000);
    const duration = state.room.phase === 'countdown' ? 3
      : state.room.phase === 'submission' ? 30
        : state.room.phase === 'voting' ? 15
          : state.room.phase === 'reveal' ? (state.room.mode === 'bluff_master' ? 5 : 3)
            : state.room.phase === 'escape_puzzle' ? 900 : Number(state.room.question_seconds || 15);
    number.textContent = Math.ceil(left).toString();
    ring.style.setProperty('--progress', Math.min(1, left / duration).toString());
    ring.classList.toggle('warning', left <= 5 && ['question','submission','voting','escape_puzzle'].includes(state.room.phase));
  }
  function leave() {
    if (state.socket) state.socket.close(1000, 'Player left the arena');
    sessionStorage.removeItem('qv-token');
    state.token = '';
    state.room = null;
    state.playerId = null;
    state.screen = 'home';
    render();
    setTimeout(connect, 0);
  }
  app.addEventListener('click', async event => {
    const target = event.target.closest('[data-action]');
    if (!target) return;
    const action = target.dataset.action;
    if (action === 'modal-container') return;
    const nickname = document.querySelector('#player-name');
    if (nickname) {
      state.name = nickname.value.trim();
      sessionStorage.setItem('qv-name', state.name);
    }
    if (action === 'inline-social-profile-create' || action === 'inline-intel-profile-create') {
      const inputId = action === 'inline-social-profile-create' ? '#inline-social-profile-name' : '#inline-intel-profile-name';
      const displayName = document.querySelector(inputId)?.value.trim() || '';
      if (!displayName) { notify('Please enter a display name first.'); return; }
      state.profileLoading = true; state.profileNotice = ''; render();
      try {
        const { response, data } = await playerApi('/api/player-profiles', { method: 'POST', body: JSON.stringify({ display_name: displayName }) });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not create a player profile.');
        state.profileCredential = data.credential; state.oneTimeCredential = data.credential;
        state.name = displayName;
        sessionStorage.setItem('qv-name', displayName);
        sessionStorage.setItem(PLAYER_CREDENTIAL_KEY, data.credential);
        state.profileLoading = false;
        void initPresence();
        void fetchNotificationUnreadCount();
        notify('Profile created! Welcome to QuizVerse Live.');
        if (state.view === 'social') await refreshSocial();
        else if (state.view === 'intelligence') await refreshIntelligence();
        else render();
      } catch (error) {
        state.profileLoading = false;
        state.profileNotice = error.message || 'Could not create a player profile.';
        render();
      }
      return;
    }
    if (action === 'profile-create') {
      const displayName = document.querySelector('#profile-create-name')?.value || '';
      state.profileLoading = true; state.profileNotice = ''; render();
      try {
        const { response, data } = await playerApi('/api/player-profiles', { method: 'POST', body: JSON.stringify({ display_name: displayName }) });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not create a player profile.');
        state.profileCredential = data.credential; state.oneTimeCredential = data.credential;
        state.ratingHistoryPage = 0; state.ratingHistoryScope = ''; state.ratingHistory = [];
        sessionStorage.setItem(PLAYER_CREDENTIAL_KEY, data.credential);
        await refreshProfile();
      } catch (error) { state.profileLoading = false; state.profileNotice = error.message || 'Could not create a player profile.'; render(); }
    } else if (action === 'profile-signin') {
      const credential = document.querySelector('#profile-credential')?.value.trim() || '';
      if (!credential) { notify('Paste your saved player credential to sign in.'); return; }
      state.profileCredential = credential; state.oneTimeCredential = '';
      state.ratingHistoryPage = 0; state.ratingHistoryScope = ''; state.ratingHistory = [];
      sessionStorage.setItem(PLAYER_CREDENTIAL_KEY, credential);
      await refreshProfile();
    } else if (action === 'profile-update') {
      const displayName = document.querySelector('#profile-name-update')?.value || '';
      try {
        const { response, data } = await playerApi('/api/player-profiles/me', { method: 'PATCH', body: JSON.stringify({ display_name: displayName, expected_version: state.profile.version }) });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not update your profile.');
        state.profile = data; state.profileNotice = 'Display name updated.'; render();
      } catch (error) { state.profileNotice = error.message || 'Could not update your profile.'; render(); }
    } else if (action === 'profile-rotate') {
      try {
        const { response, data } = await playerApi('/api/player-profiles/me/credentials/rotate', { method: 'POST' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not rotate the credential.');
        state.profileCredential = data.credential; state.oneTimeCredential = data.credential;
        sessionStorage.setItem(PLAYER_CREDENTIAL_KEY, data.credential);
        await refreshProfile();
      } catch (error) { state.profileNotice = error.message || 'Could not rotate the credential.'; render(); }
    } else if (action === 'profile-copy-credential') {
      if (!state.oneTimeCredential) { notify('There is no new credential to copy.'); return; }
      try {
        await navigator.clipboard.writeText(state.oneTimeCredential);
        state.oneTimeCredential = '';
        state.profileNotice = 'Credential copied. Keep it private; it cannot be recovered if lost.';
        render();
      } catch { notify('Clipboard access is unavailable. The credential remains stored for this tab; rotate it later if needed.'); }
    } else if (action === 'profile-logout') {
      closePresence();
      sessionStorage.removeItem(PLAYER_CREDENTIAL_KEY);
      state.profileCredential = ''; state.profile = null; state.profileStats = null; state.profileMatches = [];
      state.myRatings = null; state.ratingHistory = []; state.ratingHistoryTotal = 0; state.ratingHistoryPage = 0;
      state.careerAdvancedStats = null; state.careerHonors = null; state.h2hData = null; state.h2hModalOpen = false;
      state.oneTimeCredential = ''; state.profileNotice = 'Signed out on this tab.'; render();
    } else if (action === 'profile-delete') {
      if (!window.confirm('Delete this pseudonymous profile? Credentials will be revoked and retained match records anonymized. This cannot be undone.')) return;
      const { response, data } = await playerApi('/api/player-profiles/me', { method: 'DELETE' });
      if (!response.ok) { state.profileNotice = data?.error?.message || 'Could not delete this profile.'; render(); return; }
      closePresence();
      sessionStorage.removeItem(PLAYER_CREDENTIAL_KEY);
      state.profileCredential = ''; state.profile = null; state.profileStats = null; state.profileMatches = [];
      state.myRatings = null; state.ratingHistory = []; state.ratingHistoryTotal = 0; state.ratingHistoryPage = 0;
      state.careerAdvancedStats = null; state.careerHonors = null; state.h2hData = null; state.h2hModalOpen = false;
      state.oneTimeCredential = ''; state.profileNotice = 'Profile deleted and identifying profile data anonymized.'; render();
    } else if (action === 'profile-more') {
      state.profilePage += 1; await refreshProfile();
    } else if (action === 'rating-history-prev') {
      if (state.ratingHistoryPage > 0) { state.ratingHistoryPage -= 1; await refreshRatingHistory(); }
    } else if (action === 'rating-history-next') {
      if ((state.ratingHistoryPage + 1) * 20 < state.ratingHistoryTotal) { state.ratingHistoryPage += 1; await refreshRatingHistory(); }
    } else if (action === 'rating-history-retry') {
      await refreshRatingHistory();
    } else if (action === 'open-h2h') {
      const opponentId = target.dataset.opponentId;
      const opponentName = target.dataset.opponentName || '';
      if (!opponentId) return;
      state.h2hModalOpen = true;
      state.h2hLoading = true;
      state.h2hError = '';
      state.h2hData = null;
      state.h2hOpponentId = opponentId;
      state.h2hOpponentName = opponentName;
      render();
      try {
        const { response, data } = await playerApi(`/api/competition/head-to-head?player_one_id=me&player_two_id=${encodeURIComponent(opponentId)}`);
        if (!response.ok) throw new Error(data?.error?.message || 'Could not load head-to-head comparison.');
        state.h2hData = data;
      } catch (err) {
        state.h2hError = err.message || 'Could not load head-to-head comparison.';
      } finally {
        state.h2hLoading = false;
        render();
      }
    } else if (action === 'profile-h2h-compare') {
      const oppInput = document.querySelector('#h2h-opponent-input');
      const opponentId = oppInput?.value.trim();
      if (!opponentId) {
        notify('Please enter an opponent player profile ID.');
        oppInput?.focus();
        return;
      }
      if (state.profile && opponentId === state.profile.id) {
        notify('Cannot compare head-to-head with yourself.');
        return;
      }
      state.h2hModalOpen = true;
      state.h2hLoading = true;
      state.h2hError = '';
      state.h2hData = null;
      state.h2hOpponentId = opponentId;
      state.h2hOpponentName = '';
      render();
      try {
        const { response, data } = await playerApi(`/api/competition/head-to-head?player_one_id=me&player_two_id=${encodeURIComponent(opponentId)}`);
        if (!response.ok) throw new Error(data?.error?.message || 'Could not load head-to-head comparison.');
        state.h2hData = data;
      } catch (err) {
        state.h2hError = err.message || 'Could not load head-to-head comparison.';
      } finally {
        state.h2hLoading = false;
        render();
      }
    } else if (action === 'h2h-close') {
      state.h2hModalOpen = false;
      state.h2hData = null;
      state.h2hError = '';
      render();
    } else if (action === 'quiz-library') {
      state.view = 'library';
      state.screen = '';
      setActiveNavigation();
      render();
    } else if (action === 'create' || action === 'join') {
      if (!state.name || state.name.length > 18 || !/^[\p{L}\p{N}_ .'-]+$/u.test(state.name)) {
        document.querySelector('#name-error').textContent = 'Use 1–18 letters or numbers for your nickname.';
        nickname?.focus();
        return;
      }
      document.querySelector('#name-error').textContent = '';
      if (action === 'create') {
        const identityTicket = await getLiveIdentityTicket();
        if (identityTicket === false) return;
        const mode = document.querySelector('#game-mode')?.value || 'classic';
        const solo = mode === 'solo_practice';
        const scope = document.querySelector('#match-scope')?.value || 'built_in';
        const selectedQuizzes = [...(document.querySelector('#match-quiz')?.selectedOptions || [])].slice(0, 20).map(option => option.value);
        const isPrivate = document.querySelector('#match-private')?.checked || false;
        send('create', {
          name: state.name, mode, ...(identityTicket ? { identity_ticket: identityTicket } : {}),
          access_policy: isPrivate ? 'invite_only' : 'public',
          question_count: Number(document.querySelector('#match-count')?.value || 10),
          difficulty: solo ? state.preferences.difficulty : document.querySelector('#match-difficulty')?.value || 'medium',
          learning_preset: solo ? state.preferences.learning_preset : document.querySelector('#match-preset')?.value || 'teen_challenge',
          topic_preferences: solo ? state.preferences.topics : {},
          language: solo ? state.preferences.language : document.querySelector('#match-language')?.value || 'en',
          categories: [...(document.querySelector('#match-categories')?.selectedOptions || [])].map(option => option.value),
          content_scope: scope,
          quiz_ids: scope === 'selected_quizzes' ? selectedQuizzes : [],
          library_capability: scope === 'selected_quizzes' && selectedQuizzes.some(id=>state.ownedQuizzes.some(q=>q.id===id && q.visibility==='private')) ? (sessionStorage.getItem('qv-owner-capability') || undefined) : undefined,
          exam_collection_id: document.querySelector('#match-exam')?.value || null,
        });
      }
      else {
        document.querySelector('#join-controls').hidden = false;
        document.querySelector('#room-code').focus();
      }
    } else if (action === 'confirm-join') {
      const code = document.querySelector('#room-code').value.trim().toUpperCase();
      if (!/^[A-HJ-NP-Z2-9]{6}$/.test(code)) {
        notify('Enter a valid six-character arena code.');
        document.querySelector('#room-code').focus();
        return;
      }
      const identityTicket = await getLiveIdentityTicket();
      if (identityTicket === false) return;
      send('join', { name: state.name, code, ...(identityTicket ? { identity_ticket: identityTicket } : {}) });
    } else if (action === 'copy') {
      try { await navigator.clipboard.writeText(state.room.code); notify('Arena code copied. Rally your crew!'); }
      catch { notify(`Your arena code is ${state.room.code}`); }
    } else if (action === 'start') send('start');
    else if (action === 'answer') send('answer', { choice: Number(target.dataset.choice) });
    else if (action === 'ability') send('ability', { ability: target.dataset.ability, request_id: crypto.randomUUID() });
    else if (action === 'bluff-submit') {
      const answer = target.closest('.quiz-card')?.querySelector('#bluff-answer')?.value || document.querySelector('#bluff-answer')?.value || '';
      if (!answer.trim()) { notify('Enter a fake answer first.'); return; }
      send('bluff_submit', { answer, request_id: crypto.randomUUID() });
    }
    else if (action === 'bluff-vote') send('bluff_vote', { choice_id: target.dataset.choiceId, request_id: crypto.randomUUID() });
    else if (action === 'escape-answer') {
      const answer = document.querySelector('#escape-answer')?.value || '';
      if (!answer.trim()) { notify('Enter the team answer first.'); return; }
      send('escape_answer', { answer, puzzle_index: state.room.escape.current_puzzle.index, request_id: crypto.randomUUID() });
    }
    else if (action === 'escape-hint') send('escape_hint', { request_id: crypto.randomUUID() });
    else if (action === 'rematch') {
      if (state.room?.mode === 'solo_practice') state.soloReplayPending = true;
      send('rematch');
    }
    else if (action === 'lobby') send('lobby');
    else if (action === 'leave') leave();
    else if (action === 'rankings-scope') {
      const scope = target.dataset.scope;
      if (scope === 'global') {
        state.rankingsScope = 'global';
      } else {
        state.rankingsScope = 'mode';
        state.rankingsMode = scope;
      }
      state.rankingsPage = 0;
      state.rankingsData = null;
      void refreshRankings();
    }
    else if (action === 'rankings-prev') {
      if (state.rankingsPage > 0) {
        state.rankingsPage--;
        void refreshRankings();
      }
    }
    else if (action === 'rankings-next') {
      if (state.rankingsData?.has_more) {
        state.rankingsPage++;
        void refreshRankings();
      }
    }
    else if (action === 'rankings-retry') {
      void refreshRankings();
    }
    else if (action === 'open-tournament-create') {
      state.showTournamentCreateModal = true;
      render();
    }
    else if (action === 'close-tournament-create') {
      state.showTournamentCreateModal = false;
      render();
    }
    else if (action === 'tournament-submit-create') {
      const name = document.querySelector('#tourn-name')?.value.trim();
      const mode = document.querySelector('#tourn-mode')?.value;
      const capacity = Number(document.querySelector('#tourn-capacity')?.value || 8);
      const description = document.querySelector('#tourn-desc')?.value.trim();
      if (!name) { notify('Please enter a tournament name.'); return; }
      try {
        const { response, data } = await playerApi('/api/tournaments', {
          method: 'POST',
          body: JSON.stringify({ name, mode, capacity, description: description || null })
        });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not create tournament.');
        notify('Tournament created!');
        state.showTournamentCreateModal = false;
        state.selectedTournamentId = data.id;
        await refreshTournamentDetail(data.id);
        render();
      } catch (err) {
        notify(err.message || 'Error creating tournament.');
      }
    }
    else if (action === 'view-tournament') {
      const tId = target.dataset.id;
      if (tId) {
        state.selectedTournamentId = tId;
        await refreshTournamentDetail(tId);
        render();
      }
    }
    else if (action === 'back-to-tournaments') {
      state.selectedTournamentId = null;
      state.selectedTournamentDetail = null;
      state.selectedTournamentBracket = null;
      void refreshTournaments();
    }
    else if (action === 'tournaments-retry') {
      void refreshTournaments();
    }
    else if (action === 'go-to-profile') {
      state.view = 'profile';
      state.screen = '';
      setActiveNavigation();
      void refreshProfile();
    }
    else if (action === 'open-tournament-reg') {
      const tId = target.dataset.id;
      try {
        const { response, data } = await playerApi(`/api/tournaments/${tId}/open-registration`, { method: 'POST' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not open registration.');
        notify('Registration opened!');
        await refreshTournamentDetail(tId);
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'close-tournament-reg') {
      const tId = target.dataset.id;
      try {
        const { response, data } = await playerApi(`/api/tournaments/${tId}/close-registration`, { method: 'POST' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not close registration.');
        notify('Registration closed!');
        await refreshTournamentDetail(tId);
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'register-tournament') {
      const tId = target.dataset.id;
      try {
        const { response, data } = await playerApi(`/api/tournaments/${tId}/registration`, { method: 'POST' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not register for tournament.');
        notify('Registered successfully for tournament!');
        if (state.selectedTournamentId) await refreshTournamentDetail(tId);
        else await refreshTournaments();
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'withdraw-tournament') {
      const tId = target.dataset.id;
      try {
        const { response, data } = await playerApi(`/api/tournaments/${tId}/registration`, { method: 'DELETE' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not withdraw from tournament.');
        notify('Withdrawn from tournament.');
        if (state.selectedTournamentId) await refreshTournamentDetail(tId);
        else await refreshTournaments();
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'start-tournament') {
      const tId = target.dataset.id;
      try {
        const { response, data } = await playerApi(`/api/tournaments/${tId}/start`, { method: 'POST' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not start tournament.');
        notify('Tournament started and bracket generated!');
        await refreshTournamentDetail(tId);
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'cancel-tournament') {
      const tId = target.dataset.id;
      try {
        const { response, data } = await playerApi(`/api/tournaments/${tId}/cancel`, { method: 'POST' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not cancel tournament.');
        notify('Tournament cancelled.');
        await refreshTournamentDetail(tId);
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'launch-tournament-match') {
      const tId = target.dataset.tournId;
      const mId = target.dataset.matchId;
      try {
        // Step 1: Issue ticket
        const ticketRes = await playerApi(`/api/tournaments/${tId}/matches/${mId}/admission-ticket`, { method: 'POST' });
        if (!ticketRes.response.ok) throw new Error(ticketRes.data?.error?.message || 'Could not get match admission ticket.');
        const ticket = ticketRes.data.ticket;

        // Step 2: Launch fixture
        const launchRes = await playerApi(`/api/tournaments/${tId}/matches/${mId}/launch`, {
          method: 'POST',
          body: JSON.stringify({ ticket, player_name: state.profile?.display_name || state.name || 'Player' })
        });
        if (!launchRes.response.ok) throw new Error(launchRes.data?.error?.message || 'Could not launch match room.');

        const joined = launchRes.data;
        state.token = joined.token;
        state.playerId = joined.player_id;
        state.room = joined.state;
        sessionStorage.setItem('qv-token', state.token);
        send('reconnect', { token: joined.token });
        state.view = 'play';
        setActiveNavigation();
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'forfeit-tournament-match') {
      const tId = target.dataset.tournId;
      const mId = target.dataset.matchId;
      try {
        const { response, data } = await playerApi(`/api/tournaments/${tId}/matches/${mId}/forfeit`, {
          method: 'POST',
          body: JSON.stringify({ reason: 'player_forfeit' })
        });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not forfeit match.');
        notify('Match forfeited.');
        await refreshTournamentDetail(tId);
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'dispute-tournament-match') {
      const tId = target.dataset.tournId;
      const mId = target.dataset.matchId;
      const reason = 'player_dispute';
      try {
        const { response, data } = await playerApi(`/api/tournaments/${tId}/matches/${mId}/disputes`, {
          method: 'POST',
          body: JSON.stringify({ reason })
        });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not submit dispute.');
        notify('Dispute recorded.');
        await refreshTournamentDetail(tId);
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'open-league-create') {
      state.showLeagueCreateModal = true;
      render();
    }
    else if (action === 'close-league-create') {
      state.showLeagueCreateModal = false;
      render();
    }
    else if (action === 'league-submit-create') {
      const name = document.querySelector('#league-name')?.value.trim();
      const mode = document.querySelector('#league-mode')?.value;
      const description = document.querySelector('#league-desc')?.value.trim();
      if (!name) { notify('Please enter a league name.'); return; }
      try {
        const { response, data } = await playerApi('/api/leagues', {
          method: 'POST',
          body: JSON.stringify({ name, mode, description: description || null })
        });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not create league.');
        notify('League created!');
        state.showLeagueCreateModal = false;
        state.selectedLeagueId = data.id;
        state.selectedSeasonId = null;
        await refreshLeagueDetail(data.id);
        render();
      } catch (err) {
        notify(err.message || 'Error creating league.');
      }
    }
    else if (action === 'view-league') {
      const lId = target.dataset.id;
      if (lId) {
        state.selectedLeagueId = lId;
        state.selectedSeasonId = null;
        await refreshLeagueDetail(lId);
        render();
      }
    }
    else if (action === 'back-to-leagues') {
      state.selectedLeagueId = null;
      state.selectedLeagueDetail = null;
      state.selectedSeasonId = null;
      state.selectedSeasonDetail = null;
      void refreshLeagues();
    }
    else if (action === 'leagues-retry') {
      void refreshLeagues();
    }
    else if (action === 'open-season-create') {
      state.showSeasonCreateModal = true;
      render();
    }
    else if (action === 'close-season-create') {
      state.showSeasonCreateModal = false;
      render();
    }
    else if (action === 'season-submit-create') {
      const name = document.querySelector('#season-name')?.value.trim();
      const capacity = Number(document.querySelector('#season-capacity')?.value || 16);
      if (!name) { notify('Please enter a season name.'); return; }
      try {
        const { response, data } = await playerApi(`/api/leagues/${state.selectedLeagueId}/seasons`, {
          method: 'POST',
          body: JSON.stringify({ name, capacity })
        });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not create season.');
        notify('Season created!');
        state.showSeasonCreateModal = false;
        state.selectedSeasonId = data.id;
        await refreshSeasonDetail(state.selectedLeagueId, data.id);
        render();
      } catch (err) {
        notify(err.message || 'Error creating season.');
      }
    }
    else if (action === 'view-season') {
      const lId = target.dataset.leagueId;
      const sId = target.dataset.seasonId;
      if (lId && sId) {
        state.selectedLeagueId = lId;
        state.selectedSeasonId = sId;
        await refreshSeasonDetail(lId, sId);
        render();
      }
    }
    else if (action === 'back-to-league') {
      state.selectedSeasonId = null;
      state.selectedSeasonDetail = null;
      if (state.selectedLeagueId) await refreshLeagueDetail(state.selectedLeagueId);
      render();
    }
    else if (action === 'open-season-reg') {
      const lId = target.dataset.leagueId;
      const sId = target.dataset.seasonId;
      try {
        const { response, data } = await playerApi(`/api/leagues/${lId}/seasons/${sId}/open-registration`, { method: 'POST' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not open registration.');
        notify('Registration opened!');
        await refreshSeasonDetail(lId, sId);
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'close-season-reg') {
      const lId = target.dataset.leagueId;
      const sId = target.dataset.seasonId;
      try {
        const { response, data } = await playerApi(`/api/leagues/${lId}/seasons/${sId}/close-registration`, { method: 'POST' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not close registration.');
        notify('Registration closed!');
        await refreshSeasonDetail(lId, sId);
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'register-season') {
      const lId = target.dataset.leagueId;
      const sId = target.dataset.seasonId;
      try {
        const { response, data } = await playerApi(`/api/leagues/${lId}/seasons/${sId}/register`, { method: 'POST' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not register for season.');
        notify('Registered successfully for season!');
        await refreshSeasonDetail(lId, sId);
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'withdraw-season') {
      const lId = target.dataset.leagueId;
      const sId = target.dataset.seasonId;
      try {
        const { response, data } = await playerApi(`/api/leagues/${lId}/seasons/${sId}/withdraw`, { method: 'POST' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not withdraw from season.');
        notify('Withdrawn from season.');
        await refreshSeasonDetail(lId, sId);
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'start-season') {
      const lId = target.dataset.leagueId;
      const sId = target.dataset.seasonId;
      try {
        const { response, data } = await playerApi(`/api/leagues/${lId}/seasons/${sId}/start`, { method: 'POST' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not start season.');
        notify('Season started and round-robin fixtures generated!');
        await refreshSeasonDetail(lId, sId);
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'cancel-season') {
      const lId = target.dataset.leagueId;
      const sId = target.dataset.seasonId;
      try {
        const { response, data } = await playerApi(`/api/leagues/${lId}/seasons/${sId}/cancel`, { method: 'POST' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not cancel season.');
        notify('Season cancelled.');
        await refreshSeasonDetail(lId, sId);
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'launch-season-fixture') {
      const lId = target.dataset.leagueId;
      const sId = target.dataset.seasonId;
      const fId = target.dataset.fixtureId;
      try {
        // Step 1: Issue ticket
        const ticketRes = await playerApi(`/api/leagues/${lId}/seasons/${sId}/fixtures/${fId}/ticket`, { method: 'POST' });
        if (!ticketRes.response.ok) throw new Error(ticketRes.data?.error?.message || 'Could not get fixture admission ticket.');
        const ticket = ticketRes.data.ticket;

        // Step 2: Launch fixture
        const launchRes = await playerApi(`/api/leagues/${lId}/seasons/${sId}/fixtures/${fId}/launch`, {
          method: 'POST',
          body: JSON.stringify({ ticket, player_name: state.profile?.display_name || state.name || 'Player' })
        });
        if (!launchRes.response.ok) throw new Error(launchRes.data?.error?.message || 'Could not launch match room.');

        const joined = launchRes.data;
        state.token = joined.token;
        state.playerId = joined.player_id;
        state.room = joined.state;
        sessionStorage.setItem('qv-token', state.token);
        send('reconnect', { token: joined.token });
        state.view = 'play';
        setActiveNavigation();
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'forfeit-season-fixture') {
      const lId = target.dataset.leagueId;
      const sId = target.dataset.seasonId;
      const fId = target.dataset.fixtureId;
      try {
        const { response, data } = await playerApi(`/api/leagues/${lId}/seasons/${sId}/fixtures/${fId}/forfeit`, {
          method: 'POST',
          body: JSON.stringify({ reason: 'player_forfeit' })
        });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not forfeit fixture.');
        notify('Fixture forfeited.');
        await refreshSeasonDetail(lId, sId);
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'dispute-season-fixture') {
      const lId = target.dataset.leagueId;
      const sId = target.dataset.seasonId;
      const fId = target.dataset.fixtureId;
      const reason = 'player_dispute';
      try {
        const { response, data } = await playerApi(`/api/leagues/${lId}/seasons/${sId}/fixtures/${fId}/dispute`, {
          method: 'POST',
          body: JSON.stringify({ reason })
        });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not submit dispute.');
        notify('Dispute recorded.');
        await refreshSeasonDetail(lId, sId);
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'nav-profile') {
      state.view = 'profile';
      state.screen = '';
      setActiveNavigation();
      void refreshProfile();
    }
    else if (action === 'refresh-social-hub') {
      void refreshSocial();
    }
    else if (action === 'intel-tab') {
      state.intelligenceTab = target.dataset.tab;
      render();
    }
    else if (action === 'refresh-intel') {
      void refreshIntelligence();
    }
    else if (action === 'set-perf-window') {
      state.intelligenceTimeWindow = target.dataset.window;
      render();
      try {
        const { response, data } = await playerApi(`/api/intelligence/performance?time_window=${encodeURIComponent(state.intelligenceTimeWindow)}`);
        if (response.ok) {
          state.intelligencePerformance = data;
          render();
        }
      } catch (err) { notify(err.message); }
    }
    else if (action === 'apply-recommended-diff') {
      const diff = target.dataset.difficulty;
      try {
        if (state.preferences) {
          state.preferences.difficulty = diff;
          localStorage.setItem(PREFERENCE_KEY, JSON.stringify(state.preferences));
        }
        notify(`Difficulty preference updated to ${diff.toUpperCase()}!`);
        void refreshIntelligence();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'complete-path-step') {
      const pathId = target.dataset.pathId;
      const stepId = target.dataset.stepId;
      try {
        const { response, data } = await playerApi(`/api/intelligence/learning-paths/${pathId}/steps/${stepId}/complete`, {
          method: 'POST'
        });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not complete learning path step.');
        state.intelligenceLearningPath = data.path || data;
        notify('Step marked as completed! Keep going!');
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'regenerate-path') {
      try {
        state.intelligenceLoading = true;
        render();
        const { response, data } = await playerApi('/api/intelligence/learning-paths/generate', {
          method: 'POST',
          body: JSON.stringify({ regenerate: true })
        });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not generate learning path.');
        state.intelligenceLearningPath = data;
        state.intelligenceTab = 'paths';
        notify('Personalized learning path generated!');
      } catch (err) { notify(err.message); }
      finally {
        state.intelligenceLoading = false;
        render();
      }
    }
    else if (action === 'generate-report-btn') {
      try {
        state.intelligenceLoading = true;
        render();
        const { response, data } = await playerApi('/api/intelligence/reports/generate', {
          method: 'POST',
          body: JSON.stringify({ time_window: state.intelligenceTimeWindow || 'recent' })
        });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not generate report.');
        state.intelligenceReports.unshift(data);
        state.intelligenceSelectedReport = data;
        state.intelligenceTab = 'reports';
        notify('Comprehensive AI report generated!');
      } catch (err) { notify(err.message); }
      finally {
        state.intelligenceLoading = false;
        render();
      }
    }
    else if (action === 'select-report') {
      const repId = target.dataset.reportId;
      const found = state.intelligenceReports.find(r => r.id === repId);
      if (found) {
        state.intelligenceSelectedReport = found;
        render();
      }
    }
    else if (action === 'analyze-question') {
      const prompt = document.querySelector('#intel-q-prompt')?.value.trim() || '';
      const opt0 = document.querySelector('#intel-q-opt0')?.value.trim() || '';
      const opt1 = document.querySelector('#intel-q-opt1')?.value.trim() || '';
      const opt2 = document.querySelector('#intel-q-opt2')?.value.trim() || '';
      const opt3 = document.querySelector('#intel-q-opt3')?.value.trim() || '';
      const correct = parseInt(document.querySelector('#intel-q-correct')?.value || '0', 10);
      const category = document.querySelector('#intel-q-category')?.value.trim() || 'General';
      const difficulty = document.querySelector('#intel-q-difficulty')?.value || 'medium';
      const explanation = document.querySelector('#intel-q-explanation')?.value.trim() || '';

      const opts = [opt0, opt1, opt2, opt3].filter(o => o.length > 0);
      if (prompt.length < 5 || opts.length < 2) {
        notify('Please enter a question prompt and at least 2 options.');
        return;
      }
      try {
        const { response, data } = await playerApi('/api/intelligence/questions/analyze', {
          method: 'POST',
          body: JSON.stringify({
            prompt,
            options: opts,
            correct_index: Math.min(correct, opts.length - 1),
            category,
            difficulty,
            explanation
          })
        });
        if (!response.ok) throw new Error(data?.error?.message || 'Question analysis failed.');
        state.intelligenceQuestionAnalysis = data;
        notify('Question analysis completed!');
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'start-rec-quiz') {
      const category = target.dataset.category;
      const difficulty = target.dataset.difficulty;
      if (state.preferences) {
        if (difficulty) state.preferences.difficulty = difficulty;
      }
      state.view = 'play';
      state.screen = 'home';
      setActiveNavigation();
      render();
      notify(`Ready to practice ${category} (${difficulty})! Click Solo Practice to start.`);
    }

    else if (action === 'social-tab') {
      state.socialTab = target.dataset.tab;
      render();
      if (state.socialTab === 'overview') {
        playerApi('/api/notifications/social-overview').then(res => {
          if (res.response.ok) {
            state.socialOverview = res.data;
            render();
          }
        });
      } else if (state.socialTab === 'messages') {
        playerApi('/api/messages/conversations').then(res => {
          if (res.response.ok) {
            state.conversations = res.data.conversations || [];
            render();
          }
        });
      }
    }
    else if (action === 'exec-player-search') {
      const query = document.querySelector('#player-search-input')?.value.trim() || '';
      state.searchQuery = query;
      if (query.length < 2) {
        notify('Search query must be at least 2 characters.');
        return;
      }
      state.socialLoading = true;
      render();
      try {
        const { response, data } = await playerApi(`/api/players/search?query=${encodeURIComponent(query)}&limit=20`);
        if (!response.ok) throw new Error(data?.error?.message || 'Search failed.');
        state.playerSearchResults = data.results || [];
      } catch (err) {
        notify(err.message);
      } finally {
        state.socialLoading = false;
        render();
      }
    }
    else if (action === 'send-friend-request') {
      const recipientId = target.dataset.recipientId;
      try {
        const { response, data } = await playerApi('/api/friends/requests', {
          method: 'POST',
          body: JSON.stringify({ recipient_id: recipientId })
        });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not send friend request.');
        notify(data.status === 'accepted' ? 'Reciprocal request: You are now friends!' : 'Friend request sent!');
        await refreshSocial(false);
        if (state.selectedPublicProfile && state.selectedPublicProfile.player_id === recipientId) {
          state.selectedPublicProfile.relationship_state = data.status === 'accepted' ? 'friends' : 'outgoing_request';
        }
        if (state.searchQuery) {
          const q = state.searchQuery;
          const { response: sRes, data: sData } = await playerApi(`/api/players/search?query=${encodeURIComponent(q)}&limit=20`);
          if (sRes.ok) state.playerSearchResults = sData.results || [];
        }
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'accept-request') {
      const reqId = target.dataset.requestId;
      try {
        const { response, data } = await playerApi(`/api/friends/requests/${reqId}/accept`, { method: 'POST' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not accept request.');
        notify('Friend request accepted!');
        await refreshSocial();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'reject-request') {
      const reqId = target.dataset.requestId;
      try {
        const { response, data } = await playerApi(`/api/friends/requests/${reqId}/reject`, { method: 'POST' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not reject request.');
        notify('Friend request rejected.');
        await refreshSocial();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'cancel-request') {
      const reqId = target.dataset.requestId;
      try {
        const { response, data } = await playerApi(`/api/friends/requests/${reqId}`, { method: 'DELETE' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not cancel request.');
        notify('Friend request cancelled.');
        await refreshSocial();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'remove-friend') {
      const friendId = target.dataset.friendId;
      try {
        const { response, data } = await playerApi(`/api/friends/${friendId}`, { method: 'DELETE' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not remove friend.');
        notify('Friend removed.');
        if (state.selectedPublicProfile && state.selectedPublicProfile.player_id === friendId) {
          state.selectedPublicProfile.relationship_state = 'none';
        }
        await refreshSocial();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'block-player') {
      const playerId = target.dataset.playerId;
      try {
        const { response, data } = await playerApi('/api/blocks', {
          method: 'POST',
          body: JSON.stringify({ target_id: playerId })
        });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not block player.');
        notify('Player blocked.');
        if (state.selectedPublicProfile && state.selectedPublicProfile.player_id === playerId) {
          state.selectedPublicProfile = null;
        }
        await refreshSocial();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'unblock-player') {
      const playerId = target.dataset.playerId;
      try {
        const { response, data } = await playerApi(`/api/blocks/${playerId}`, { method: 'DELETE' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not unblock player.');
        notify('Player unblocked.');
        if (state.selectedPublicProfile && state.selectedPublicProfile.player_id === playerId) {
          state.selectedPublicProfile.relationship_state = 'none';
        }
        await refreshSocial();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'view-public-profile') {
      const playerId = target.dataset.playerId;
      try {
        const { response, data } = await playerApi(`/api/players/${playerId}/public-profile`);
        if (!response.ok) throw new Error(data?.error?.message || 'Could not load public profile.');
        state.selectedPublicProfile = data;
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'close-public-profile') {
      state.selectedPublicProfile = null;
      render();
    }
    else if (action === 'invite-friend') {
      const friendId = target.dataset.friendId;
      if (!state.room || !state.room.code) {
        notify('You must be inside an arena waiting room to send invitations.');
        return;
      }
      if (state.room.phase !== 'waiting') {
        notify('Invitations can only be sent while the arena is waiting for players.');
        return;
      }
      try {
        const { response, data } = await playerApi('/api/social/invitations', {
          method: 'POST',
          body: JSON.stringify({ invitee_id: friendId, room_code: state.room.code })
        });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not send arena invitation.');
        notify('Arena invitation sent!');
        await refreshSocial();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'accept-invitation') {
      const invitationId = target.dataset.invitationId;
      try {
        const acceptRes = await playerApi(`/api/social/invitations/${invitationId}/accept`, { method: 'POST' });
        if (!acceptRes.response.ok) throw new Error(acceptRes.data?.error?.message || 'Could not accept invitation.');
        const ticket = acceptRes.data.admission_ticket;
        const joinRes = await playerApi(`/api/social/invitations/${invitationId}/join`, {
          method: 'POST',
          body: JSON.stringify({ admission_ticket: ticket, player_name: state.name || state.profile?.display_name || 'Player' })
        });
        if (!joinRes.response.ok) throw new Error(joinRes.data?.error?.message || 'Could not enter private arena.');
        state.token = joinRes.data.token;
        state.playerId = joinRes.data.player_id;
        state.room = joinRes.data.state;
        state.clockOffsetMs = Number(state.room.server_now || Date.now()) - Date.now();
        sessionStorage.setItem('qv-token', state.token);
        sessionStorage.setItem('qv-name', state.name || state.room.players.find(p => p.id === state.playerId)?.name || '');
        state.view = 'play';
        state.screen = 'lobby';
        setActiveNavigation();
        if (state.socket && state.socket.readyState === WebSocket.OPEN) {
          send('reconnect', { token: state.token });
        } else {
          connect();
        }
        notify('Joined private arena!');
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'decline-invitation') {
      const invitationId = target.dataset.invitationId;
      try {
        const { response, data } = await playerApi(`/api/social/invitations/${invitationId}/decline`, { method: 'POST' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not decline invitation.');
        notify('Invitation declined.');
        await refreshSocial();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'cancel-invitation') {
      const invitationId = target.dataset.invitationId;
      try {
        const { response, data } = await playerApi(`/api/social/invitations/${invitationId}`, { method: 'DELETE' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not cancel invitation.');
        notify('Invitation cancelled.');
        await refreshSocial();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'challenge-rematch') {
      const opponentId = target.dataset.opponentId;
      const originalMatchId = target.dataset.matchId;
      try {
        const { response, data } = await playerApi('/api/social/rematches', {
          method: 'POST',
          body: JSON.stringify({ opponent_id: opponentId, original_match_id: originalMatchId })
        });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not issue rematch challenge.');
        notify('Rematch challenge issued!');
        await refreshRematches();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'accept-rematch') {
      const challengeId = target.dataset.challengeId;
      try {
        const { response, data } = await playerApi(`/api/social/rematches/${challengeId}/accept`, { method: 'POST' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not accept rematch challenge.');
        state.token = data.token;
        state.playerId = data.player_id;
        state.room = data.state;
        state.clockOffsetMs = Number(state.room.server_now || Date.now()) - Date.now();
        sessionStorage.setItem('qv-token', state.token);
        sessionStorage.setItem('qv-name', state.name || state.room.players.find(p => p.id === state.playerId)?.name || '');
        state.view = 'play';
        state.screen = 'lobby';
        setActiveNavigation();
        if (state.socket && state.socket.readyState === WebSocket.OPEN) {
          send('reconnect', { token: state.token });
        } else {
          connect();
        }
        notify('Rematch arena created!');
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'decline-rematch') {
      const challengeId = target.dataset.challengeId;
      try {
        const { response, data } = await playerApi(`/api/social/rematches/${challengeId}/decline`, { method: 'POST' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not decline rematch.');
        notify('Rematch challenge declined.');
        await refreshRematches();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'cancel-rematch') {
      const challengeId = target.dataset.challengeId;
      try {
        const { response, data } = await playerApi(`/api/social/rematches/${challengeId}`, { method: 'DELETE' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not cancel rematch.');
        notify('Rematch challenge cancelled.');
        await refreshRematches();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'open-friend-chat') {
      const friendId = target.dataset.friendId;
      const friendName = target.dataset.friendName;
      await openConversationWithFriend(friendId, friendName);
    }
    else if (action === 'select-conversation') {
      const convId = target.dataset.conversationId;
      state.activeConversationId = convId;
      state.editingMessageId = null;
      state.messageDraft = '';
      render();
      await refreshActiveMessages();
      render();
      setTimeout(scrollThreadToBottom, 50);
    }
    else if (action === 'close-active-chat') {
      state.activeConversationId = null;
      render();
    }
    else if (action === 'send-dm') {
      const input = document.querySelector('#dm-composer-input');
      const text = input ? input.value.trim() : '';
      if (!text) return;
      if (!state.activeConversationId) return;
      try {
        const { response, data } = await playerApi(`/api/messages/conversations/${state.activeConversationId}/messages`, {
          method: 'POST',
          body: JSON.stringify({ content: text })
        });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not send message.');
        if (!state.activeMessages.some(m => m.id === data.id)) {
          state.activeMessages.push(data);
        }
        state.messageDraft = '';
        if (input) input.value = '';
        const conv = (state.conversations || []).find(c => c.id === state.activeConversationId);
        if (conv) {
          conv.last_message_preview = text.slice(0, 60);
          conv.last_message_at = data.created_at;
        }
        render();
        setTimeout(scrollThreadToBottom, 50);
      } catch (err) { notify(err.message); }
    }
    else if (action === 'start-edit-dm') {
      state.editingMessageId = target.dataset.messageId;
      state.messageDraft = target.dataset.content || '';
      render();
      const input = document.querySelector('#dm-composer-input');
      if (input) { input.focus(); input.selectionStart = input.selectionEnd = input.value.length; }
    }
    else if (action === 'cancel-edit-dm') {
      state.editingMessageId = null;
      state.messageDraft = '';
      render();
    }
    else if (action === 'submit-edit-dm') {
      const input = document.querySelector('#dm-composer-input');
      const text = input ? input.value.trim() : '';
      if (!text || !state.editingMessageId) return;
      try {
        const { response, data } = await playerApi(`/api/messages/${state.editingMessageId}`, {
          method: 'PATCH',
          body: JSON.stringify({ content: text })
        });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not edit message.');
        const idx = state.activeMessages.findIndex(m => m.id === state.editingMessageId);
        if (idx !== -1) {
          state.activeMessages[idx] = { ...state.activeMessages[idx], ...data };
        }
        state.editingMessageId = null;
        state.messageDraft = '';
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'delete-dm') {
      const msgId = target.dataset.messageId;
      try {
        const { response, data } = await playerApi(`/api/messages/${msgId}`, { method: 'DELETE' });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not delete message.');
        const m = state.activeMessages.find(item => item.id === msgId);
        if (m) {
          m.is_deleted = true;
          m.content = '[Message deleted]';
          m.deleted_at = data.deleted_at;
        }
        notify('Message deleted.');
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'toggle-dm-reaction') {
      const msgId = target.dataset.messageId;
      const rx = target.dataset.reaction;
      const msg = state.activeMessages.find(m => m.id === msgId);
      let alreadyReacted = false;
      if (msg && msg.reactions) {
        const val = msg.reactions[rx];
        if (Array.isArray(val)) alreadyReacted = val.includes(state.profile?.player_id);
        else if (typeof val === 'object') alreadyReacted = !!val.reacted_by_me;
      }
      try {
        if (alreadyReacted) {
          const { response, data } = await playerApi(`/api/messages/${msgId}/reactions/${encodeURIComponent(rx)}`, { method: 'DELETE' });
          if (response.ok && msg) msg.reactions = data.reactions;
        } else {
          const { response, data } = await playerApi(`/api/messages/${msgId}/reactions`, {
            method: 'POST',
            body: JSON.stringify({ reaction: rx })
          });
          if (response.ok && msg) msg.reactions = data.reactions;
        }
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'start-report-dm') {
      state.reportingMessageId = target.dataset.messageId;
      state.reportingReason = 'inappropriate';
      state.reportingDetails = '';
      render();
    }
    else if (action === 'close-report-modal') {
      state.reportingMessageId = null;
      render();
    }
    else if (action === 'submit-report-dm') {
      if (!state.reportingMessageId) return;
      const reasonSelect = document.querySelector('#report-reason-select');
      const detailsInput = document.querySelector('#report-details-input');
      const reason = reasonSelect ? reasonSelect.value : state.reportingReason;
      const details = detailsInput ? detailsInput.value : state.reportingDetails;
      try {
        const { response, data } = await playerApi(`/api/messages/${state.reportingMessageId}/report`, {
          method: 'POST',
          body: JSON.stringify({ reason, details: details || null })
        });
        if (!response.ok) throw new Error(data?.error?.message || 'Could not submit report.');
        notify('Report submitted. Thank you for keeping QuizVerse safe.');
        state.reportingMessageId = null;
        render();
      } catch (err) { notify(err.message); }
    }
    else if (action === 'toggle-room-chat') {
      state.roomChatOpen = !state.roomChatOpen;
      if (state.roomChatOpen) state.roomChatUnread = 0;
      render();
      if (state.roomChatOpen) {
        setTimeout(() => {
          const chatBox = document.querySelector('#room-chat-messages-container');
          if (chatBox) chatBox.scrollTop = chatBox.scrollHeight;
          document.querySelector('#room-chat-input')?.focus();
        }, 50);
      }
    }
    else if (action === 'send-room-chat') {
      const input = document.querySelector('#room-chat-input');
      const text = input ? input.value.trim() : '';
      if (text) {
        send('room_chat', { text });
        if (input) input.value = '';
      }
    }
    else if (action === 'toggle-room-reaction') {
      const msgId = target.dataset.messageId;
      const reaction = target.dataset.reaction;
      send('room_chat_reaction', { message_id: msgId, reaction });
    }
    else if (action === 'comm-tab') {
      state.communityTab = target.dataset.tab;
      void refreshCommunity();
    }
    else if (action === 'refresh-community') {
      void refreshCommunity();
    }
    else if (action === 'exec-comm-search') {
      const searchInput = document.querySelector('#comm-search-input');
      const diffSelect = document.querySelector('#comm-filter-difficulty');
      const sortSelect = document.querySelector('#comm-sort-by');
      state.communitySearchQuery = searchInput ? searchInput.value.trim() : '';
      state.communityFilterDifficulty = diffSelect ? diffSelect.value : '';
      state.communitySortBy = sortSelect ? sortSelect.value : 'popular';
      void refreshCommunity();
    }
    else if (action === 'toggle-follow-creator') {
      const creatorId = target.dataset.id;
      if (!creatorId) return;
      const isFollowing = target.dataset.following === 'true';
      try {
        if (isFollowing) {
          await playerApi(`/api/community/creators/follow/${creatorId}`, { method: 'DELETE' });
          notify('Unfollowed creator', 'info');
        } else {
          await playerApi(`/api/community/creators/follow/${creatorId}`, { method: 'POST' });
          notify('Following creator!', 'success');
        }
        void refreshCommunity();
      } catch (err) {
        notify(err.message || 'Follow action failed', 'error');
      }
    }
    else if (action === 'toggle-edit-creator-profile') {
      state.communityEditingProfile = !state.communityEditingProfile;
      render();
    }
    else if (action === 'save-creator-profile') {
      const bio = document.querySelector('#comm-profile-bio')?.value.trim() || document.querySelector('#comm-creator-bio-inp')?.value.trim() || '';
      const specialty = document.querySelector('#comm-profile-specialty')?.value.trim() || document.querySelector('#comm-creator-specs-inp')?.value.trim() || '';
      const specializations = specialty ? specialty.split(',').map(s => s.trim()).filter(Boolean) : [];
      const displayName = document.querySelector('#comm-creator-name-inp')?.value.trim() || undefined;
      try {
        const { response, data } = await playerApi('/api/community/creators/me', {
          method: 'PUT',
          body: JSON.stringify({ bio, display_name: displayName, specializations })
        });
        if (!response.ok) throw new Error(data?.message || 'Failed to update creator profile');
        notify('Creator profile updated', 'success');
        state.communityEditingProfile = false;
        void refreshCommunity();
      } catch (err) {
        notify(err.message || 'Failed to update creator profile', 'error');
      }
    }
    else if (action === 'toggle-quick-author-form') {
      state.communityAuthoringQuiz = !state.communityAuthoringQuiz;
      render();
    }
    else if (action === 'studio-quick-create') {
      const title = document.querySelector('#comm-new-quiz-title')?.value.trim() || document.querySelector('#studio-q-title')?.value.trim();
      const desc = document.querySelector('#comm-new-quiz-desc')?.value.trim();
      const cat = document.querySelector('#comm-new-quiz-category')?.value.trim() || document.querySelector('#studio-q-category')?.value.trim() || 'General';
      const diff = document.querySelector('#comm-new-quiz-difficulty')?.value || document.querySelector('#studio-q-diff')?.value || 'medium';
      const q1 = document.querySelector('#comm-new-q1-text')?.value.trim() || document.querySelector('#studio-q1-prompt')?.value.trim();
      const a1 = document.querySelector('#comm-new-q1-a1')?.value.trim() || document.querySelector('#studio-q1-o0')?.value.trim();
      const a2 = document.querySelector('#comm-new-q1-a2')?.value.trim() || document.querySelector('#studio-q1-o1')?.value.trim();
      const a3 = document.querySelector('#comm-new-q1-a3')?.value.trim() || document.querySelector('#studio-q1-o2')?.value.trim();
      const a4 = document.querySelector('#comm-new-q1-a4')?.value.trim() || document.querySelector('#studio-q1-o3')?.value.trim();
      if (!title || !q1 || !a1 || !a2) {
        notify('Please provide at least a title and 1 question with 2 options', 'error');
        return;
      }
      try {
        let cap = sessionStorage.getItem('qv-owner-capability');
        if (!cap) {
          const capRes = await fetch('/api/quiz-owners', { method: 'POST' });
          if (capRes.ok) {
            const capData = await capRes.json();
            cap = capData.capability;
            sessionStorage.setItem('qv-owner-capability', cap);
          }
        }
        const createRes = await fetch('/api/quizzes', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(cap ? { 'Authorization': `Bearer ${cap}` } : {})
          },
          body: JSON.stringify({
            title,
            description: desc || 'Community authored quiz',
            category: cat,
            difficulty: diff,
            visibility: 'public',
            default_time_limit_seconds: 15,
            questions: [
              {
                prompt: q1,
                options: [a1, a2, a3 || 'Option 3', a4 || 'Option 4'],
                correct_option_index: 0,
                explanation: 'Created via Community Studio'
              },
              {
                prompt: 'What is the deepest known oceanic depression on Earth?',
                options: ['Challenger Deep', 'Java Trench', 'Puerto Rico Trench', 'Molley Deep'],
                correct_option_index: 0,
                explanation: 'Challenger Deep is located in the southern Mariana Trench.'
              }
            ]
          })
        });
        const createData = await createRes.json();
        if (!createRes.ok || !createData.id) {
          throw new Error(createData?.detail || createData?.message || 'Quiz creation failed');
        }
        const pubRes = await playerApi(`/api/community/quizzes/${createData.id}/publish`, {
          method: 'POST',
          body: JSON.stringify({ visibility: 'public' })
        });
        if (!pubRes.response.ok) {
          throw new Error(pubRes.data?.detail || pubRes.data?.message || 'Publishing failed');
        }
        notify('Quiz created and published to Community!', 'success');
        state.communityAuthoringQuiz = false;
        void refreshCommunity();
      } catch (err) {
        notify(err.message || 'Quiz creation/publishing failed', 'error');
      }
    }
    else if (action === 'studio-publish-quiz') {
      const id = target.dataset.quizId || target.dataset.id;
      try {
        await playerApi(`/api/community/quizzes/${id}/publish`, {
          method: 'POST',
          body: JSON.stringify({ visibility: 'public' })
        });
        notify('Quiz published!', 'success');
        void refreshCommunity();
      } catch (err) {
        notify(err.message || 'Publishing failed', 'error');
      }
    }
    else if (action === 'studio-unpublish-quiz') {
      const id = target.dataset.quizId || target.dataset.id;
      try {
        await playerApi(`/api/community/quizzes/${id}/unpublish`, { method: 'POST' });
        notify('Quiz unpublished to draft', 'info');
        void refreshCommunity();
      } catch (err) {
        notify(err.message || 'Unpublish failed', 'error');
      }
    }
    else if (action === 'open-creator-analytics') {
      const id = target.dataset.creatorId || target.dataset.id;
      try {
        const { response, data } = await playerApi(`/api/community/creators/${id}/analytics`);
        if (response.ok) {
          state.communityAnalyticsModal = data;
          render();
        }
      } catch (err) {
        notify(err.message || 'Could not load analytics', 'error');
      }
    }
    else if (action === 'toggle-favorite') {
      const id = target.dataset.quizId || target.dataset.id;
      try {
        await playerApi(`/api/community/favorites/${id}`, { method: 'POST' });
        notify('Saved to favorites', 'success');
        void refreshCommunity();
      } catch (err) {
        notify(err.message || 'Failed to update favorites', 'error');
      }
    }
    else if (action === 'remove-favorite') {
      const id = target.dataset.quizId || target.dataset.id;
      try {
        await playerApi(`/api/community/favorites/${id}`, { method: 'DELETE' });
        notify('Removed from favorites', 'info');
        void refreshCommunity();
      } catch (err) {
        notify(err.message || 'Failed to remove favorite', 'error');
      }
    }
    else if (action === 'open-quiz-reviews') {
      const id = target.dataset.quizId || target.dataset.id;
      const title = target.dataset.quizTitle || target.dataset.title || 'Community Quiz';
      try {
        const { response, data } = await playerApi(`/api/community/quizzes/${id}/reviews`);
        state.communityActiveQuiz = { id, title };
        state.communityActiveReviews = data?.items || [];
        render();
      } catch (err) {
        notify(err.message || 'Failed to load reviews', 'error');
      }
    }
    else if (action === 'close-quiz-reviews') {
      state.communityActiveQuiz = null;
      state.communityActiveReviews = null;
      render();
    }
    else if (action === 'submit-quiz-review') {
      const qid = target.dataset.quizId || target.dataset.id || state.communityActiveQuiz?.id;
      const ratingVal = parseInt(document.querySelector('#comm-review-rating')?.value || document.querySelector('#review-star-rating')?.value || '5', 10);
      const reviewText = document.querySelector('#comm-review-text')?.value.trim() || document.querySelector('#review-text-input')?.value.trim() || '';
      try {
        const { response, data } = await playerApi(`/api/community/quizzes/${qid}/reviews`, {
          method: 'POST',
          body: JSON.stringify({ rating: ratingVal, review_text: reviewText })
        });
        if (!response.ok) {
          throw new Error(data?.detail || data?.message || 'Could not submit review (must play quiz first)');
        }
        notify('Review submitted successfully!', 'success');
        const res = await playerApi(`/api/community/quizzes/${qid}/reviews`);
        state.communityActiveReviews = res.data?.items || [];
        render();
      } catch (err) {
        notify(err.message || 'Could not submit review (must play quiz first)', 'error');
      }
    }
    else if (action === 'mark-review-helpful') {
      const rid = target.dataset.reviewId || target.dataset.id;
      try {
        await playerApi(`/api/community/reviews/${rid}/helpful`, { method: 'POST' });
        notify('Marked as helpful', 'success');
        if (state.communityActiveQuiz) {
          const res = await playerApi(`/api/community/quizzes/${state.communityActiveQuiz.id}/reviews`);
          state.communityActiveReviews = res.data?.items || [];
          render();
        }
      } catch (err) {
        notify(err.message || 'Vote failed', 'error');
      }
    }
    else if (action === 'delete-own-review') {
      const rid = target.dataset.reviewId || target.dataset.id;
      try {
        await playerApi(`/api/community/reviews/${rid}`, { method: 'DELETE' });
        notify('Review deleted', 'info');
        if (state.communityActiveQuiz) {
          const res = await playerApi(`/api/community/quizzes/${state.communityActiveQuiz.id}/reviews`);
          state.communityActiveReviews = res.data?.items || [];
          render();
        }
      } catch (err) {
        notify(err.message || 'Delete failed', 'error');
      }
    }
    else if (action === 'open-report-modal') {
      state.communityActiveReportTarget = {
        target_type: target.dataset.targetType,
        target_id: target.dataset.targetId
      };
      render();
    }
    else if (action === 'close-report-modal') {
      state.communityActiveReportTarget = null;
      render();
    }
    else if (action === 'submit-community-report') {
      if (!state.communityActiveReportTarget) return;
      const reason = document.querySelector('#report-reason-select')?.value || document.querySelector('#comm-report-reason')?.value || 'inappropriate';
      const details = document.querySelector('#comm-report-details')?.value.trim() || document.querySelector('#report-details-text')?.value.trim() || '';
      try {
        const { response, data } = await playerApi('/api/community/reports', {
          method: 'POST',
          body: JSON.stringify({
            target_type: state.communityActiveReportTarget.target_type,
            target_id: state.communityActiveReportTarget.target_id,
            reason,
            description: details
          })
        });
        if (!response.ok) throw new Error(data?.detail || data?.message || 'Report submission failed');
        notify('Report submitted to moderation queue', 'info');
        state.communityActiveReportTarget = null;
        render();
      } catch (err) {
        notify(err.message || 'Report failed', 'error');
      }
    }
    else if (action === 'mod-resolve') {
      const repId = target.dataset.id;
      const actionType = target.dataset.modAction;
      const notes = prompt(`Moderator notes for action "${actionType}":`) || 'Processed via Community Moderator Dashboard';
      try {
        await playerApi(`/api/community/moderation/reports/${repId}/resolve`, {
          method: 'POST',
          body: JSON.stringify({ action: actionType, notes })
        });
        notify(`Report resolved with action: ${actionType}`, 'success');
        void refreshCommunity();
      } catch (err) {
        notify(err.message || 'Moderation action failed', 'error');
      }
    }
    else if (action === 'toggle-new-collection-form') {
      state.communityCreatingCollection = !state.communityCreatingCollection;
      render();
    }
    else if (action === 'submit-create-collection') {
      const title = document.querySelector('#comm-collection-title')?.value.trim() || document.querySelector('#comm-col-title-inp')?.value.trim();
      const desc = document.querySelector('#comm-collection-desc')?.value.trim() || document.querySelector('#comm-col-desc-inp')?.value.trim();
      const vis = document.querySelector('#comm-collection-vis')?.value || 'public';
      if (!title) {
        notify('Collection title required', 'error');
        return;
      }
      try {
        const { response, data } = await playerApi('/api/community/collections', {
          method: 'POST',
          body: JSON.stringify({ title, description: desc, visibility: vis })
        });
        if (!response.ok) throw new Error(data?.detail || data?.message || 'Failed to create collection');
        notify('Collection created!', 'success');
        state.communityCreatingCollection = false;
        void refreshCommunity();
      } catch (err) {
        notify(err.message || 'Failed to create collection', 'error');
      }
    }
    else if (action === 'view-collection') {
      const id = target.dataset.colId || target.dataset.id;
      try {
        const { response, data } = await playerApi(`/api/community/collections/${id}`);
        if (response.ok) {
          state.communityActiveCollection = data;
          render();
        }
      } catch (err) {
        notify(err.message || 'Could not load collection', 'error');
      }
    }
    else if (action === 'close-active-collection') {
      state.communityActiveCollection = null;
      render();
    }
    else if (action === 'remove-quiz-from-col') {
      const cid = target.dataset.colId || target.dataset.collectionId;
      const qid = target.dataset.quizId;
      try {
        await playerApi(`/api/community/collections/${cid}/quizzes/${qid}`, { method: 'DELETE' });
        notify('Quiz removed from collection', 'info');
        const { response, data } = await playerApi(`/api/community/collections/${cid}`);
        if (response.ok) state.communityActiveCollection = data;
        render();
      } catch (err) {
        notify(err.message || 'Failed to remove quiz', 'error');
      }
    }
    else if (action === 'share-community-quiz') {
      const id = target.dataset.quizId || target.dataset.id;
      const url = `${window.location.origin}/#community/quiz/${id}`;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url);
      }
      notify('Share URL copied to clipboard!', 'success');
    }
    else if (action === 'play-community-quiz') {
      const id = target.dataset.quizId || target.dataset.id;
      try {
        await playerApi(`/api/community/quizzes/${id}/play`, { method: 'POST' });
        state.quizId = id;
        state.gameMode = 'solo';
        state.view = 'play';
        setActiveNavigation();
        render();
        notify('Starting Community Quiz...', 'info');
        send('create_room', { mode: 'solo', quiz_id: id });
      } catch (err) {
        notify(err.message || 'Could not launch quiz', 'error');
      }
    }
    else if (action === 'close-comm-modal') {
      state.communityActiveAnalytics = null;
      state.communityActiveQuiz = null;
      state.communityActiveReportTarget = null;
      state.communityActiveCollection = null;
      render();
    }
  });
  app.addEventListener('keydown', event => {
    if (event.key === 'Enter' && event.target.id === 'room-code') document.querySelector('[data-action="confirm-join"]')?.click();
    if (event.key === 'Enter' && event.target.id === 'player-name') document.querySelector('[data-action="create"]')?.click();
    if (event.key === 'Enter' && event.target.id === 'player-search-input') document.querySelector('[data-action="exec-player-search"]')?.click();
    if (event.key === 'Enter' && event.target.id === 'room-chat-input') {
      event.preventDefault();
      document.querySelector('[data-action="send-room-chat"]')?.click();
    }
    if (event.key === 'Enter' && !event.shiftKey && event.target.id === 'dm-composer-input') {
      event.preventDefault();
      if (state.editingMessageId) {
        document.querySelector('[data-action="submit-edit-dm"]')?.click();
      } else {
        document.querySelector('[data-action="send-dm"]')?.click();
      }
    }
  });
  app.addEventListener('input', event => {
    if (event.target.id === 'dm-composer-input') {
      const val = event.target.value;
      state.messageDraft = val;
      const countEl = document.querySelector('#dm-char-count');
      if (countEl) countEl.textContent = `${val.length} / 2000`;
      if (!state.typingTimer && state.activeConversationId) {
        void playerApi('/api/messages/typing', {
          method: 'POST',
          body: JSON.stringify({ conversation_id: state.activeConversationId, is_typing: true })
        });
        state.typingTimer = setTimeout(() => {
          if (state.activeConversationId) {
            void playerApi('/api/messages/typing', {
              method: 'POST',
              body: JSON.stringify({ conversation_id: state.activeConversationId, is_typing: false })
            });
          }
          state.typingTimer = null;
        }, 3500);
      }
    }
    if (event.target.id === 'dm-search-input') {
      state.messageSearchQuery = event.target.value;
      render();
    }
    if (event.target.id === 'report-reason-select') {
      state.reportingReason = event.target.value;
    }
    if (event.target.id === 'report-details-input') {
      state.reportingDetails = event.target.value;
    }
  });
  app.addEventListener('change', event => {
    if (event.target.id === 'rating-history-scope') {
      state.ratingHistoryScope = event.target.value;
      state.ratingHistoryPage = 0;
      void refreshRatingHistory();
    }
    if (event.target.id === 'filter-status') {
      state.tournamentsFilterStatus = event.target.value;
      void refreshTournaments();
    }
    if (event.target.id === 'filter-mode') {
      state.tournamentsFilterMode = event.target.value;
      void refreshTournaments();
    }
    if (event.target.id === 'filter-league-status') {
      state.leaguesFilterStatus = event.target.value;
      void refreshLeagues();
    }
    if (event.target.id === 'filter-league-mode') {
      state.leaguesFilterMode = event.target.value;
      void refreshLeagues();
    }
  });
  function openMobileDrawer() {
    const drawer = document.getElementById('mobile-nav-drawer');
    const backdrop = document.getElementById('mobile-nav-backdrop');
    const menuBtn = document.getElementById('mobile-menu-btn');
    if (drawer) {
      drawer.classList.add('open');
      drawer.setAttribute('aria-hidden', 'false');
    }
    if (backdrop) {
      backdrop.classList.add('open');
      backdrop.setAttribute('aria-hidden', 'false');
    }
    if (menuBtn) {
      menuBtn.setAttribute('aria-expanded', 'true');
    }
    const closeBtn = document.getElementById('mobile-drawer-close');
    if (closeBtn) closeBtn.focus();
  }

  function closeMobileDrawer() {
    const drawer = document.getElementById('mobile-nav-drawer');
    const backdrop = document.getElementById('mobile-nav-backdrop');
    const menuBtn = document.getElementById('mobile-menu-btn');
    if (drawer) {
      drawer.classList.remove('open');
      drawer.setAttribute('aria-hidden', 'true');
    }
    if (backdrop) {
      backdrop.classList.remove('open');
      backdrop.setAttribute('aria-hidden', 'true');
    }
    if (menuBtn) {
      menuBtn.setAttribute('aria-expanded', 'false');
    }
  }

  function toggleMobileDrawer() {
    const drawer = document.getElementById('mobile-nav-drawer');
    if (drawer && drawer.classList.contains('open')) {
      closeMobileDrawer();
    } else {
      openMobileDrawer();
    }
  }

  function switchView(viewName) {
    if (!viewName) return;
    state.view = viewName;
    state.screen = '';
    closeMobileDrawer();
    try {
      history.pushState({ view: state.view }, '', '');
    } catch (_) {}
    setActiveNavigation();
    if (state.view === 'profile') void refreshProfile();
    else if (state.view === 'rankings') void refreshRankings();
    else if (state.view === 'tournaments') void refreshTournaments();
    else if (state.view === 'leagues') void refreshLeagues();
    else if (state.view === 'social') void refreshSocial();
    else if (state.view === 'intelligence') void refreshIntelligence();
    else if (state.view === 'community') void refreshCommunity();
    else render();
  }

  function setActiveNavigation() {
    document.querySelectorAll('[data-view]').forEach(button => {
      const active = button.dataset.view === state.view;
      button.classList.toggle('active', active);
      if (active) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
  }
  document.addEventListener('click', async event => {
    const mobileMenuTrigger = event.target.closest('[data-action="toggle-mobile-menu"], #mobile-menu-btn');
    if (mobileMenuTrigger) {
      event.preventDefault();
      toggleMobileDrawer();
      return;
    }

    const mobileCloseTrigger = event.target.closest('[data-action="close-mobile-menu"], #mobile-drawer-close, #mobile-nav-backdrop');
    if (mobileCloseTrigger) {
      event.preventDefault();
      closeMobileDrawer();
      return;
    }

    const brandTrigger = event.target.closest('header .brand');
    if (brandTrigger) {
      event.preventDefault();
      switchView('play');
      return;
    }

    const bellBtn = event.target.closest('[data-action="toggle-notif-center"], #notif-bell-btn');
    if (bellBtn) {
      event.preventDefault();
      state.notifCenterOpen = !state.notifCenterOpen;
      if (state.notifCenterOpen) {
        void fetchNotifications();
      }
      renderNotificationCenter();
      return;
    }

    if (event.target.closest('[data-action="close-notif-center"]')) {
      state.notifCenterOpen = false;
      renderNotificationCenter();
      return;
    }

    const notifTabBtn = event.target.closest('[data-action="notif-tab"]');
    if (notifTabBtn) {
      state.notifTab = notifTabBtn.dataset.tab;
      void fetchNotifications(state.notifTab);
      return;
    }

    const unreadOnlyToggle = event.target.closest('[data-action="toggle-notif-unread-only"]');
    if (unreadOnlyToggle) {
      state.notifUnreadOnly = !state.notifUnreadOnly;
      void fetchNotifications(state.notifTab, state.notifUnreadOnly);
      return;
    }

    const toggleReadBtn = event.target.closest('[data-action="notif-toggle-read"]');
    if (toggleReadBtn) {
      const id = toggleReadBtn.dataset.id;
      const item = (state.notifications || []).find(n => n.id === id);
      if (item) {
        const markUnread = item.is_read;
        item.is_read = !markUnread;
        if (markUnread) {
          state.notifUnreadCount = (state.notifUnreadCount || 0) + 1;
          void playerApi(`/api/notifications/${id}/unread`, { method: 'POST' });
        } else {
          state.notifUnreadCount = Math.max(0, (state.notifUnreadCount || 0) - 1);
          void playerApi(`/api/notifications/${id}/read`, { method: 'POST' });
        }
        updateNotifBadgeUI();
        renderNotificationCenter();
      }
      return;
    }

    if (event.target.closest('[data-action="notif-mark-all-read"]')) {
      (state.notifications || []).forEach(n => { n.is_read = true; });
      state.notifUnreadCount = 0;
      updateNotifBadgeUI();
      renderNotificationCenter();
      void playerApi('/api/notifications/mark-all-read', { method: 'POST' });
      return;
    }

    const delBtn = event.target.closest('[data-action="notif-delete"]');
    if (delBtn) {
      const id = delBtn.dataset.id;
      const item = (state.notifications || []).find(n => n.id === id);
      if (item && !item.is_read) {
        state.notifUnreadCount = Math.max(0, (state.notifUnreadCount || 0) - 1);
        updateNotifBadgeUI();
      }
      state.notifications = (state.notifications || []).filter(n => n.id !== id);
      renderNotificationCenter();
      void playerApi(`/api/notifications/${id}`, { method: 'DELETE' });
      return;
    }

    if (event.target.closest('[data-action="notif-clear-read"]')) {
      state.notifications = (state.notifications || []).filter(n => !n.is_read);
      renderNotificationCenter();
      void playerApi('/api/notifications/clear-read', { method: 'DELETE' });
      return;
    }

    if (event.target.closest('[data-action="open-notif-prefs"]')) {
      void fetchNotificationPreferences();
      return;
    }

    if (event.target.matches('[data-action="close-notif-backdrop"]') || event.target.closest('[data-action="close-notif-prefs"]')) {
      state.notifPreferencesOpen = false;
      renderNotificationPreferencesModal();
      return;
    }

    if (event.target.closest('[data-action="save-notif-prefs"]')) {
      const modal = document.querySelector('#notif-modal-container');
      if (!modal) return;
      const inputs = modal.querySelectorAll('input[data-pref-key]');
      const payload = {};
      inputs.forEach(inp => {
        payload[inp.dataset.prefKey] = inp.checked;
      });
      void saveNotificationPreferences(payload);
      return;
    }

    const notifTarget = event.target.closest('[data-action="notif-navigate"]');
    if (notifTarget) {
      const id = notifTarget.dataset.id;
      const targetType = notifTarget.dataset.targetType;
      const targetId = notifTarget.dataset.targetId;
      if (id) {
        void playerApi(`/api/notifications/${id}/read`, { method: 'POST' });
        const item = (state.notifications || []).find(n => n.id === id);
        if (item && !item.is_read) {
          item.is_read = true;
          state.notifUnreadCount = Math.max(0, (state.notifUnreadCount || 0) - 1);
          updateNotifBadgeUI();
        }
      }
      state.notifCenterOpen = false;
      renderNotificationCenter();

      if (targetType === 'invitation' || targetType === 'rematch') {
        state.view = 'social';
        state.socialTab = 'invitations';
        setActiveNavigation();
        render();
        void refreshSocial();
      } else if (targetType === 'message') {
        state.view = 'social';
        state.socialTab = 'messages';
        state.activeConversationId = targetId;
        setActiveNavigation();
        render();
        void refreshActiveMessages();
      } else if (targetType === 'friend' || targetType === 'friend_request') {
        state.view = 'social';
        state.socialTab = targetType === 'friend_request' ? 'requests' : 'friends';
        setActiveNavigation();
        render();
        void refreshSocial();
      } else if (targetType === 'tournament') {
        state.view = 'tournaments';
        state.selectedTournamentId = targetId;
        setActiveNavigation();
        void refreshTournaments();
      } else if (targetType === 'league') {
        state.view = 'leagues';
        state.selectedLeagueId = targetId;
        setActiveNavigation();
        void refreshLeagues();
      } else if (targetType === 'profile') {
        state.view = 'profile';
        setActiveNavigation();
        void refreshProfile();
      } else if (targetType === 'quiz' || targetType === 'creator' || targetType === 'review' || targetType === 'collection') {
        state.view = 'community';
        setActiveNavigation();
        render();
        void refreshCommunity();
      }
      return;
    }

    if (state.notifCenterOpen) {
      const insidePanel = event.target.closest('#notif-center-panel');
      const insideBell = event.target.closest('#notif-bell-btn');
      const insideModal = event.target.closest('#notif-modal-container');
      if (!insidePanel && !insideBell && !insideModal) {
        state.notifCenterOpen = false;
        renderNotificationCenter();
      }
    }
  });

  const primaryNav = document.querySelector('#primary-navigation');
  if (primaryNav) {
    primaryNav.addEventListener('click', event => {
      const button = event.target.closest('[data-view]');
      if (!button) return;
      switchView(button.dataset.view);
    });
  }

  const mobileNavDrawer = document.querySelector('#mobile-nav-drawer');
  if (mobileNavDrawer) {
    mobileNavDrawer.addEventListener('click', event => {
      const button = event.target.closest('[data-view]');
      if (!button) return;
      switchView(button.dataset.view);
    });
  }

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      const drawer = document.getElementById('mobile-nav-drawer');
      if (drawer && drawer.classList.contains('open')) {
        closeMobileDrawer();
        const menuBtn = document.getElementById('mobile-menu-btn');
        if (menuBtn) menuBtn.focus();
      }
      if (state.notifCenterOpen) {
        state.notifCenterOpen = false;
        renderNotificationCenter();
      }
    }
  });

  window.addEventListener('popstate', event => {
    closeMobileDrawer();
    if (state.communityAuthoringQuiz || state.communityEditingProfile || state.communityCreatingCollection) {
      state.communityAuthoringQuiz = false;
      state.communityEditingProfile = false;
      state.communityCreatingCollection = false;
      render();
      return;
    }
    if (state.notifCenterOpen) {
      state.notifCenterOpen = false;
      renderNotificationCenter();
      return;
    }
    if (event.state && event.state.view) {
      state.view = event.state.view;
      setActiveNavigation();
      render();
    }
  });
  setActiveNavigation();
  if (state.profileCredential) {
    void initPresence();
    void fetchNotificationUnreadCount();
  }
  connect();
})();
