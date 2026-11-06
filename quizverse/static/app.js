(() => {
  'use strict';

  const app = document.querySelector('#app');
  const toast = document.querySelector('#toast');
  const connectionStatus = document.querySelector('#connection-status');
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
    soloReplayPending: false,
    reconnectDelay: 800,
    reconnectTimer: null,
    toastTimer: null,
    tick: null,
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
    picker.innerHTML = '<label class="form-label" for="game-mode">Choose your arena format</label><select class="field" id="game-mode" name="game-mode"><option value="classic">Classic · 15 seconds · Momentum Shift</option><option value="speed_challenge">Speed Challenge · 10 seconds · no Momentum Shift</option></select><p class="microcopy mode-description" id="mode-description">Classic: 1,000 points plus up to 500 speed points. Momentum Shift is active.</p>';
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
    const soloOption = document.createElement('option');
    soloOption.value = 'solo_practice';
    soloOption.textContent = 'Solo Practice - just you';
    select.append(soloOption);
    select.value = state.selectedMode;
    const updateSelection = () => {
      state.selectedMode = select.value;
      picker.querySelector('#mode-description').textContent = select.value === 'speed_challenge'
        ? 'Speed Challenge: 1,000 points plus up to 500 speed points scaled to 10 seconds. Momentum Shift is off.'
        : select.value === 'elimination'
          ? 'Elimination: wrong or unanswered means out. Survive all 10 rounds or be the last player standing.'
          : select.value === 'solo_practice'
            ? 'Solo Practice: one real player, ten questions, personal scoring, and no leaderboard or Momentum Shift.'
          : select.value === 'team_battle'
          ? 'Team Battle: 4–8 real players, with an even roster. Teams alternate by join order; individual points combine for the team. Momentum Shift is off.'
          : 'Classic: 1,000 points plus up to 500 speed points. Momentum Shift is active.';
      const timePill = app.querySelector('.format-row .format-pill:last-child b');
      if (timePill) timePill.textContent = select.value === 'speed_challenge' ? '10s' : '15s';
      const createButton = app.querySelector('[data-action="create"]');
      if (createButton) createButton.textContent = select.value === 'solo_practice' ? 'Create Solo Practice' : '✦  Create Arena';
      const homeCopy = app.querySelector('.home-layout > .hero-copy > p');
      if (homeCopy) homeCopy.textContent = select.value === 'solo_practice'
        ? 'Ten questions, your own pace, and a score that is yours alone. Step into the arena whenever you are ready.'
        : 'Ten quick-fire questions. Your favorite people. One gloriously debatable winner. Your next great game night starts here.';
      const joinArt = app.querySelector('.art-label.bottom strong');
      if (joinArt) joinArt.textContent = select.value === 'solo_practice' ? 'YOUR OWN PRACTICE ARENA' : 'YOUR CREW, YOUR ARENA';
      const homeFoot = app.querySelector('.play-card > .microcopy');
      if (homeFoot) homeFoot.textContent = select.value === 'solo_practice'
        ? 'No account. No downloads. No room code or opponent needed.'
        : 'No account. No downloads. Just a code and a little friendly rivalry.';
    };
    select.addEventListener('change', updateSelection);
    updateSelection();
  }
  function updateModePresentation() {
    const room = state.room;
    if (!room) return;
    const speedMode = room.mode === 'speed_challenge';
    const teamMode = room.mode === 'team_battle';
    const eliminationMode = room.mode === 'elimination';
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
    if (modeRow) modeRow.textContent = speedMode ? 'Speed Challenge' : teamMode ? 'Team Battle' : eliminationMode ? 'Elimination' : soloMode ? 'Solo Practice' : 'Classic';
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
      if (playerRule) playerRule.querySelector('strong').textContent = '2-8';
      const roomNote = app.querySelector('.room-note');
      if (roomNote) roomNote.textContent = 'Wrong or unanswered answers eliminate a player at reveal. Disconnected players can reconnect until the question deadline.';
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
        badge.textContent = player.eliminated ? `OUT - ${player.elimination_reason || 'eliminated'}` : 'IN';
        (row.querySelector('.player-info, .leader-name') || row).after(badge);
      }
      const current = room.players.find(player => player.id === state.playerId);
      if (current?.eliminated && room.phase !== 'waiting' && room.phase !== 'results') {
        const answerGrid = app.querySelector('.answer-grid');
        if (answerGrid) answerGrid.outerHTML = '<div class="eliminated-watching" role="status"><strong>You are out.</strong><span>Watch the finish unfold. Reconnect remains available until each question closes.</span></div>';
      }
      const revealBanner = app.querySelector('.reveal-banner');
      if (revealBanner && current?.eliminated) revealBanner.textContent = current.elimination_reason === 'timeout' ? 'Time ran out. You are eliminated; watch the remaining players.' : 'That answer was incorrect. You are eliminated; watch the remaining players.';
      if (room.phase === 'results' && room.results) renderEliminationResults(room.results);
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
  function renderEliminationResults(results) {
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
        : 'Ten rounds complete. Survivors rank by points, correct answers, and correct response time.';
    const podium = app.querySelector('.podium');
    if (podium) podium.hidden = true;
    const list = app.querySelector('.final-list');
    if (list) list.innerHTML = rows.map(player => {
      const suffix = player.placement === 1 ? 'st' : player.placement === 2 ? 'nd' : player.placement === 3 ? 'rd' : 'th';
      const place = `${player.shared_placement ? 'Shared ' : ''}${player.placement}${suffix}`;
      const status = player.eliminated ? `Out in round ${player.eliminated_round} (${player.elimination_reason})` : 'Survived';
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
    return `<section class="room-layout"><div class="room-card"><button class="back-button" type="button" data-action="leave">← &nbsp;Leave arena</button><div class="section-kicker">YOUR PRIVATE GAME ROOM</div><h2>Gather your people.</h2><div class="room-code-row"><div class="room-code" aria-label="Room code ${escapeHtml(room.code)}">${escapeHtml(room.code)}</div><button class="btn btn-secondary" type="button" data-action="copy">▣ &nbsp;Copy code</button></div><p class="room-note">Share this code with your crew. Once two players are here, the host can launch the quiz.</p><div class="crew-heading"><h3>The crew</h3><span class="capacity">${room.players.length} / 8 players</span></div><div class="player-list" aria-live="polite">${room.players.map(playerRow).join('')}</div>${host ? `<button class="btn btn-primary btn-full" type="button" data-action="start" ${room.can_start ? '' : 'disabled'}>Launch the quiz &nbsp;✦</button><p class="microcopy">${room.can_start ? 'Everyone ready? Let the brain battle begin.' : 'Waiting for at least one more connected player.'}</p>` : `<div class="waiting-note"><span class="pulse-dot"></span>Waiting for ${escapeHtml(hostName)} to launch…</div>`}</div><aside class="side-stack"><section class="rules-card"><h3>Tonight's format</h3><div class="rule-line"><span>Players</span><strong>2–8</strong></div><div class="rule-line"><span>Questions</span><strong>10 rounds</strong></div><div class="rule-line"><span>Answer time</span><strong>15 seconds</strong></div></section><section class="rules-card"><h3>How scoring works</h3><div class="rule-line"><span>Correct answer</span><strong>1,000 pts</strong></div><div class="rule-line"><span>Speed bonus</span><strong>Up to 500</strong></div><p class="microcopy">The server records answer time. Faster correct answers earn a bigger bonus.</p></section><section class="rules-card momentum-card"><h3>Momentum Shift</h3><p class="momentum-copy">At the start of a round, players trailing the leader by 1–1,500 points qualify. A correct answer in the first 5 seconds earns a published <strong>+250 points</strong>. Nobody loses points.</p></section></aside></section>`;
  }
  function game() {
    const room = state.room;
    const question = room.question;
    const feedback = room.answer_feedback;
    const countdown = room.phase === 'countdown';
    const labels = ['A', 'B', 'C', 'D'];
    const momentum = room.momentum;
    return `<section class="game-layout"><div class="quiz-card"><div class="quiz-head"><div class="quiz-tags"><span class="round-tag">ROUND ${String(Math.min(room.round, 10)).padStart(2, '0')} / 10</span>${question ? `<span class="category-tag">${escapeHtml(question.category)}</span>` : ''}</div><div class="timer-ring" id="timer-ring" role="timer" aria-label="Time remaining"><span class="timer-number" id="timer-number">${countdown ? '3' : room.question_seconds}</span></div></div>${countdown ? `<h1 class="question-text">Get ready.<br>The arena opens in a moment.</h1><div class="waiting-note">Find your focus. First question incoming.</div>` : `<h1 class="question-text">${escapeHtml(question?.prompt || 'Loading the next question…')}</h1><div class="answer-grid">${(question?.options || []).map((option, index) => { const reveal = room.phase === 'reveal'; const correct = reveal && feedback?.correct === index; const selected = feedback?.selected === index; const classes = ['answer-button', correct ? 'correct' : '', selected ? 'selected' : '', reveal && selected && !correct ? 'incorrect' : ''].filter(Boolean).join(' '); return `<button class="${classes}" type="button" data-action="answer" data-choice="${index}" ${room.phase !== 'question' || room.answered ? 'disabled' : ''}><span class="answer-letter">${labels[index]}</span><span>${escapeHtml(option)}</span>${correct ? '<span aria-label="Correct answer">✓</span>' : ''}</button>`; }).join('')}</div>${feedback ? `<div class="reveal-banner ${feedback.was_correct ? '' : 'wrong'}" role="status"><span>${feedback.was_correct ? '✦' : '↻'}</span><span>${feedback.was_correct ? `Brilliant. That one is yours. +${feedback.speed_bonus.toLocaleString()} speed${feedback.momentum_bonus ? ` · +${feedback.momentum_bonus} Momentum Shift` : ''}.` : 'The right answer is highlighted. Next round soon.'}</span></div>` : ''}<div class="quiz-foot"><span>${room.answered ? 'Answer locked in. Eyes on the reveal.' : 'Choose your answer before time runs out.'}</span><span class="answer-status"><span class="mini-dot"></span>${room.players.filter(player => player.answered).length} / ${room.players.length} answered</span></div><p class="momentum-note">${momentum?.eligible ? 'Momentum Shift active: you qualify this round. Correct in 5 seconds for +250.' : 'Momentum Shift: trailing by 1–1,500 points can earn +250 for a correct answer in the first 5 seconds.'}</p>`}</div>${leaderboard()}</section>`;
  }
  function results() {
    const players = ranking();
    const winner = players[0];
    const podium = players.slice(0, 3);
    const podiumOrder = podium.length === 3 ? [podium[1], podium[0], podium[2]] : podium;
    const momentumAwards = state.room.results?.momentum_adjustments || [];
    return `<section class="results-card"><div class="winner-crown" aria-hidden="true">♛</div><div class="section-kicker" style="justify-content:center">TEN ROUNDS. ONE ARENA.</div><h2>${winner ? `${escapeHtml(winner.name)} takes the crown.` : 'The arena is yours.'}</h2><p class="results-copy">${winner ? `${winner.score.toLocaleString()} points · ${winner.correct} correct answers` : 'Thanks for playing together.'}</p><div class="podium" aria-label="Top players">${podiumOrder.map(player => { const place = players.indexOf(player) + 1; return `<div class="podium-place ${place === 1 ? 'first' : ''}"><div class="podium-medal">${['♛', '✧', '✦'][place - 1]}</div><div class="podium-name">${escapeHtml(player.name)}</div><div class="podium-score">${player.score.toLocaleString()}</div></div>`; }).join('')}</div><div class="final-list" aria-label="Final rankings">${players.map((player, index) => `<div class="final-row"><span class="rank ${index < 3 ? ['gold', 'silver', 'bronze'][index] : ''}">${index + 1}</span><span class="leader-name">${escapeHtml(player.name)}</span><span class="final-stat">${player.correct} correct · +${player.momentum_points} Momentum</span><strong class="leader-score">${player.score.toLocaleString()}</strong></div>`).join('')}</div>${momentumAwards.length ? `<p class="momentum-log">Momentum Shift awards: ${momentumAwards.map(adjustment => `${escapeHtml(adjustment.name)} +${adjustment.points} (round ${adjustment.round})`).join(' · ')}</p>` : '<p class="momentum-log">No Momentum Shift awards this match.</p>'}${state.playerId === state.room.host_id ? `<div class="results-actions"><button class="btn btn-primary" type="button" data-action="rematch">Run it back &nbsp;↻</button><button class="btn btn-secondary" type="button" data-action="lobby">Return to lobby</button></div>` : `<div class="waiting-note">The host can start a rematch whenever the crew is ready.</div>`}</section>`;
  }
  function render() {
    if (state.tick) clearInterval(state.tick);
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
    updateModePresentation();
    if (phase === 'question' || phase === 'countdown' || phase === 'reveal') {
      state.tick = setInterval(updateTimer, 100);
      updateTimer();
    }
  }
  function updateTimer() {
    const number = document.querySelector('#timer-number');
    const ring = document.querySelector('#timer-ring');
    if (!number || !ring || !state.room?.deadline) return;
    const left = Math.max(0, (state.room.deadline - (Date.now() + (state.clockOffsetMs || 0))) / 1000);
    const duration = state.room.phase === 'countdown' ? 3 : state.room.phase === 'reveal' ? 3 : Number(state.room.question_seconds || 15);
    number.textContent = Math.ceil(left).toString();
    ring.style.setProperty('--progress', Math.min(1, left / duration).toString());
    ring.classList.toggle('warning', left <= 5 && state.room.phase === 'question');
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
    if (action === 'quiz-library') {
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
      if (action === 'create') send('create', { name: state.name, mode: document.querySelector('#game-mode')?.value || 'classic' });
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
      send('join', { name: state.name, code });
    } else if (action === 'copy') {
      try { await navigator.clipboard.writeText(state.room.code); notify('Arena code copied. Rally your crew!'); }
      catch { notify(`Your arena code is ${state.room.code}`); }
    } else if (action === 'start') send('start');
    else if (action === 'answer') send('answer', { choice: Number(target.dataset.choice) });
    else if (action === 'rematch') {
      if (state.room?.mode === 'solo_practice') state.soloReplayPending = true;
      send('rematch');
    }
    else if (action === 'lobby') send('lobby');
    else if (action === 'leave') leave();
  });
  app.addEventListener('keydown', event => {
    if (event.key === 'Enter' && event.target.id === 'room-code') document.querySelector('[data-action="confirm-join"]')?.click();
    if (event.key === 'Enter' && event.target.id === 'player-name') document.querySelector('[data-action="create"]')?.click();
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
    render();
  });
  setActiveNavigation();
  connect();
})();
