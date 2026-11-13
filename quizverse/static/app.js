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
  };
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
  const initials = name => name.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join('').toUpperCase();
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
    picker.innerHTML = `<label class="form-label" for="game-mode">Choose your arena format</label><select class="field" id="game-mode" name="game-mode"><option value="classic">Classic · 15 seconds · Momentum Shift</option><option value="speed_challenge">Speed Challenge · 10 seconds · no Momentum Shift</option></select><p class="microcopy mode-description" id="mode-description">Classic: 1,000 points plus up to 500 speed points. Momentum Shift is active.</p><details class="match-settings"><summary>Learning preferences <span>(saved on this device)</span></summary><p class="microcopy">Content suitability only. This does not verify anyone’s age.</p><label>Learning preset<select class="field" id="player-preset">${presetOptions.map(([v,l])=>`<option value="${v}">${l}</option>`).join('')}</select></label><label>Preferred difficulty<select class="field" id="player-difficulty">${diffOptions.map(v=>`<option value="${v}">${v[0].toUpperCase()+v.slice(1)}</option>`).join('')}</select></label><div id="player-topic-settings"></div></details><details class="match-settings competitive-settings"><summary>Host match filters</summary><label>Question count<input class="field" id="match-count" type="number" min="1" max="20" value="10"></label><label>Difficulty<select class="field" id="match-difficulty">${diffOptions.map(v=>`<option value="${v}">${v[0].toUpperCase()+v.slice(1)}</option>`).join('')}</select></label><label>Learning suitability<select class="field" id="match-preset">${presetOptions.map(([v,l])=>`<option value="${v}">${l}</option>`).join('')}</select></label><label>Language<input class="field" id="match-language" maxlength="35" value="en"></label><label>Content scope<select class="field" id="match-scope"><option value="built_in">Built-in question bank</option><option value="public_library">Published public quizzes</option><option value="selected_quizzes">Choose published quizzes</option></select></label><label class="quiz-source-picker" hidden>Quizzes (select up to 20)<select class="field" id="match-quiz" multiple size="5" aria-label="Selected quizzes"></select></label><label>Exam collection<select class="field" id="match-exam"><option value="">Any / not applicable</option></select></label><label>Eligible categories<select class="field" id="match-categories" multiple size="5" aria-label="Eligible categories; leave empty for all"></select></label><p class="microcopy">Leave categories empty for all. Filter summaries are shared with the room.</p></details>`;
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
    const ratingCard = myRatings ? `
      <h2>Competitive Elo Rating</h2>
      <div class="user-rating-banner">
        <div class="user-rating-value">${myRatings.rating}</div>
        <div class="user-rating-meta">
          <strong>Global Rating &nbsp;${myRatings.is_provisional ? '<span class="badge-status badge-provisional">PROVISIONAL</span>' : '<span class="badge-status badge-established">ESTABLISHED</span>'}</strong>
          <span>${myRatings.rated_match_count} rated matches · ${myRatings.wins} Wins · ${myRatings.draws} Draws · ${myRatings.losses} Losses</span>
        </div>
        ${state.myRatings?.recent_changes?.length ? `
        <div class="recent-changes-pills" title="Recent rating movements">
          ${state.myRatings.recent_changes.slice(0, 5).map(c => `<span class="delta-pill ${c.rating_delta > 0 ? 'delta-pos' : c.rating_delta < 0 ? 'delta-neg' : 'delta-zero'}">${c.rating_delta > 0 ? '+' : ''}${c.rating_delta}</span>`).join('')}
        </div>` : ''}
      </div>` : (state.profileMatches.length ? '<p class="profile-empty">Casual matches completed. Complete an eligible competitive match to establish your Elo rating.</p>' : '<p class="profile-empty">No rated competitive matches completed yet.</p>');
    const matches = state.profileMatches.map(match => `<article class="profile-match"><div><strong>${escapeHtml(match.mode.replaceAll('_', ' '))}</strong><span>${escapeHtml(new Date(match.completed_at).toLocaleString())}</span></div><div><strong>${escapeHtml(match.outcome.replaceAll('_', ' '))}</strong><span>${match.placement ? `Place ${match.placement}` : escapeHtml(match.result_kind)}</span></div><div><strong>${match.final_score ?? (match.team?.score ?? '—')}</strong><span>${match.correct_count ?? (match.team?.correct_count ?? 'Shared result')}</span></div></article>`).join('');
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
    app.innerHTML = `<section class="profile-card"><div class="section-kicker">PLAYER PROFILE</div><h1>${escapeHtml(state.profile.display_name)}</h1><p class="subcopy">Pseudonymous identity · no email or personal details</p>${state.profileNotice ? `<p class="library-message" role="status">${escapeHtml(state.profileNotice)}</p>` : ''}<form class="profile-name-form"><label class="form-label" for="profile-name-update">Display name</label><input class="field" id="profile-name-update" maxlength="24" required value="${escapeHtml(state.profile.display_name)}"><button class="btn btn-secondary" type="button" data-action="profile-update">Save name</button></form>${state.oneTimeCredential ? '<div class="profile-warning" role="status">A new credential is ready. Copy it now; it will not be shown again. If lost, it cannot be recovered.</div><button class="btn btn-secondary" type="button" data-action="profile-copy-credential">Copy one-time credential</button>' : ''}<div class="profile-actions"><button class="btn btn-secondary" type="button" data-action="profile-rotate">Rotate credential</button><button class="btn btn-quiet" type="button" data-action="profile-logout">Sign out on this tab</button><button class="btn btn-danger" type="button" data-action="profile-delete">Delete profile</button></div>${ratingCard}<h2>Rating history</h2><label class="form-label" for="rating-history-scope">Filter rating scope</label><select class="field rating-history-scope" id="rating-history-scope">${ratingScopeOptions}</select>${ratingHistoryHtml}<h2>Competitive record</h2>${stats.history_available ? `<div class="profile-stats"><article><strong>${stats.completed_matches}</strong><span>Matches</span></article><article><strong>${stats.wins}</strong><span>Wins</span></article><article><strong>${stats.draws}</strong><span>Draws</span></article><article><strong>${stats.losses}</strong><span>Losses</span></article></div>` : '<p class="profile-empty">No persisted match history yet. This is different from a zero win or loss record.</p>'}<h2>Match history</h2>${matches || '<p class="profile-empty">No profile-linked completed matches yet. Join or create a match while signed in to add one.</p>'}${state.profileHistoryTotal > state.profileMatches.length ? `<button class="btn btn-secondary" type="button" data-action="profile-more">Load more</button>` : ''}<p class="microcopy">Profile deletion revokes credentials and anonymizes this profile in retained match records. Match integrity facts may remain without identifying details.</p></section>`;
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
      const [profileResult, statsResult, matchesResult, ratingsResult] = await Promise.all([
        playerApi('/api/player-profiles/me'), playerApi('/api/player-profiles/me/competitive-stats'),
        playerApi(`/api/player-profiles/me/matches?limit=20&offset=${state.profilePage * 20}`),
        playerApi('/api/player-profiles/me/ratings'),
      ]);
      if (profileResult.response.status === 401) throw new Error('Your credential was rejected. Sign in with a valid saved credential.');
      if (!profileResult.response.ok || !statsResult.response.ok || !matchesResult.response.ok) throw new Error('Player profile storage is temporarily unavailable. Try again.');
      state.profile = profileResult.data; state.profileStats = statsResult.data;
      if (ratingsResult && ratingsResult.response.ok) {
        state.myRatings = ratingsResult.data;
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
  function render() {
    if (state.tick) clearInterval(state.tick);
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
    app.innerHTML = `${offline}${phase === 'waiting' ? lobby() : phase === 'results' ? results() : game()}`;
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
    const nickname = document.querySelector('#player-name');
    if (nickname) {
      state.name = nickname.value.trim();
      sessionStorage.setItem('qv-name', state.name);
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
      sessionStorage.removeItem(PLAYER_CREDENTIAL_KEY);
      state.profileCredential = ''; state.profile = null; state.profileStats = null; state.profileMatches = [];
      state.myRatings = null; state.ratingHistory = []; state.ratingHistoryTotal = 0; state.ratingHistoryPage = 0;
      state.oneTimeCredential = ''; state.profileNotice = 'Signed out on this tab.'; render();
    } else if (action === 'profile-delete') {
      if (!window.confirm('Delete this pseudonymous profile? Credentials will be revoked and retained match records anonymized. This cannot be undone.')) return;
      const { response, data } = await playerApi('/api/player-profiles/me', { method: 'DELETE' });
      if (!response.ok) { state.profileNotice = data?.error?.message || 'Could not delete this profile.'; render(); return; }
      sessionStorage.removeItem(PLAYER_CREDENTIAL_KEY);
      state.profileCredential = ''; state.profile = null; state.profileStats = null; state.profileMatches = [];
      state.myRatings = null; state.ratingHistory = []; state.ratingHistoryTotal = 0; state.ratingHistoryPage = 0;
      state.oneTimeCredential = ''; state.profileNotice = 'Profile deleted and identifying profile data anonymized.'; render();
    } else if (action === 'profile-more') {
      state.profilePage += 1; await refreshProfile();
    } else if (action === 'rating-history-prev') {
      if (state.ratingHistoryPage > 0) { state.ratingHistoryPage -= 1; await refreshRatingHistory(); }
    } else if (action === 'rating-history-next') {
      if ((state.ratingHistoryPage + 1) * 20 < state.ratingHistoryTotal) { state.ratingHistoryPage += 1; await refreshRatingHistory(); }
    } else if (action === 'rating-history-retry') {
      await refreshRatingHistory();
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
        send('create', {
          name: state.name, mode, ...(identityTicket ? { identity_ticket: identityTicket } : {}),
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
      const answer = document.querySelector('#bluff-answer')?.value || '';
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
  });
  app.addEventListener('keydown', event => {
    if (event.key === 'Enter' && event.target.id === 'room-code') document.querySelector('[data-action="confirm-join"]')?.click();
    if (event.key === 'Enter' && event.target.id === 'player-name') document.querySelector('[data-action="create"]')?.click();
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
  });
  function setActiveNavigation() {
    document.querySelectorAll('[data-view]').forEach(button => {
      const active = button.dataset.view === state.view;
      button.classList.toggle('active', active);
      if (active) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
  }
  document.querySelector('#primary-navigation').addEventListener('click', event => {
    const button = event.target.closest('[data-view]');
    if (!button) return;
    state.view = button.dataset.view;
    state.screen = '';
    setActiveNavigation();
    if (state.view === 'profile') void refreshProfile();
    else if (state.view === 'rankings') void refreshRankings();
    else if (state.view === 'tournaments') void refreshTournaments();
    else render();
  });
  setActiveNavigation();
  connect();
})();
