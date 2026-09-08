// Weboum Chatbot Frontend Client
const API_BASE = (window.location.origin && window.location.origin !== 'null' && window.location.protocol.startsWith('http'))
  ? window.location.origin
  : 'http://localhost:8000';

let currentSessionId = sessionStorage.getItem('weboum_session_id') || null;
let currentMode = 'initial';
let currentStep = null;
let isCompleted = false;
let isWaiting = false;

// DOM Elements
const messagesContainer = document.getElementById('chat-messages');
const messageInput = document.getElementById('chat-input');
const sendButton = document.getElementById('send-btn');
const typingIndicator = document.getElementById('typing-indicator');
const sessionBadge = document.getElementById('session-badge');
const sessionIdDisplay = document.getElementById('session-id-display');
const trackerBar = document.getElementById('enquiry-tracker');
const trackerStepTag = document.getElementById('tracker-step-tag');
const trackerProgressFill = document.getElementById('tracker-progress-fill');
const newChatBtn = document.getElementById('new-chat-btn');
const inspectBtn = document.getElementById('inspect-btn');
const sessionsModal = document.getElementById('sessions-modal');
const closeModalBtn = document.getElementById('close-modal-btn');
const refreshSessionsBtn = document.getElementById('refresh-sessions-btn');
const sessionsContent = document.getElementById('sessions-content');

const ENQUIRY_STEPS = [
  'biggest_operational_challenge',
  'ai_capability',
  'primary_industry',
  'business_size',
  'full_name',
  'company_name',
  'work_email',
  'phone_number',
  'current_technology_stack'
];

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
  initChat();
  setupEventListeners();
});

function setupEventListeners() {
  sendButton.addEventListener('click', handleSend);
  messageInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  });

  newChatBtn.addEventListener('click', resetChat);
  inspectBtn.addEventListener('click', openSessionsModal);
  closeModalBtn.addEventListener('click', closeSessionsModal);
  refreshSessionsBtn.addEventListener('click', loadSessionsData);
  
  sessionsModal.addEventListener('click', (e) => {
    if (e.target === sessionsModal) closeSessionsModal();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeSessionsModal();
  });

  sessionBadge.addEventListener('click', copySessionId);
}

// Start or resume chat
async function initChat() {
  setLoading(true);
  try {
    const payload = currentSessionId ? { session_id: currentSessionId, message: "" } : {};
    const res = await fetch(`${API_BASE}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      throw new Error(`Server returned ${res.status}`);
    }

    const data = await res.json();
    handleBotResponse(data);
  } catch (err) {
    console.error('Error initializing chat:', err);
    appendBotMessage(
      "⚠️ Unable to connect to the chatbot server. Please ensure the backend is running at " + API_BASE,
      []
    );
  } finally {
    setLoading(false);
  }
}

function handleSend() {
  const text = messageInput.value.trim();
  if (!text || isWaiting) return;

  appendUserMessage(text);
  messageInput.value = '';
  disableActiveChips();
  sendMessage(text);
}

async function sendMessage(text) {
  setLoading(true);
  let activeBotGroup = null;
  let bubbleTextSpan = null;
  let cursorSpan = null;
  let accumulatedText = '';

  const isGeneralQuery = (currentMode === 'general' && text !== 'Anything Else?' && text !== 'Enquire Now');

  // 1. Show the AI message container immediately for general questions
  if (isGeneralQuery) {
    typingIndicator.classList.remove('active');
    activeBotGroup = document.createElement('div');
    activeBotGroup.className = 'message-group bot';
    activeBotGroup.innerHTML = `
      <div class="msg-avatar">AI</div>
      <div class="msg-content-wrapper">
        <div class="message-bubble"><span class="bubble-text"></span><span class="streaming-cursor"></span></div>
        <div class="msg-timestamp">${formatTime(new Date())}</div>
      </div>
    `;
    messagesContainer.appendChild(activeBotGroup);
    bubbleTextSpan = activeBotGroup.querySelector('.bubble-text');
    cursorSpan = activeBotGroup.querySelector('.streaming-cursor');
    scrollToBottom();
  }

  try {
    const payload = {
      session_id: currentSessionId,
      message: text
    };

    const res = await fetch(`${API_BASE}/api/chat?stream=true`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
        'x-stream': 'true'
      },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      if (activeBotGroup) activeBotGroup.remove();
      const errData = await res.json().catch(() => ({}));
      appendBotMessage(`⚠️ Error: ${errData.detail || 'Something went wrong.'}`, []);
      return;
    }

    const contentType = res.headers.get('content-type') || '';

    // If response is standard JSON (business enquiry flow, initial options, etc.)
    if (contentType.includes('application/json') || !res.body) {
      if (activeBotGroup) activeBotGroup.remove();
      const data = await res.json();
      handleBotResponse(data);
      return;
    }

    // 2. Start reading the response stream
    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n\n');
      buffer = lines.pop();

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith('data:')) continue;
        const jsonStr = trimmed.replace(/^data:\s*/, '');
        if (!jsonStr) continue;

        try {
          const event = JSON.parse(jsonStr);

          if (event.type === 'chunk' || event.type === 'delta') {
            // 3. As each text chunk arrives from the server: append text and scroll
            if (!activeBotGroup) {
              typingIndicator.classList.remove('active');
              activeBotGroup = document.createElement('div');
              activeBotGroup.className = 'message-group bot';
              activeBotGroup.innerHTML = `
                <div class="msg-avatar">AI</div>
                <div class="msg-content-wrapper">
                  <div class="message-bubble"><span class="bubble-text"></span><span class="streaming-cursor"></span></div>
                  <div class="msg-timestamp">${formatTime(new Date())}</div>
                </div>
              `;
              messagesContainer.appendChild(activeBotGroup);
              bubbleTextSpan = activeBotGroup.querySelector('.bubble-text');
              cursorSpan = activeBotGroup.querySelector('.streaming-cursor');
            }
            accumulatedText += (event.content || '');
            bubbleTextSpan.textContent = accumulatedText;
            scrollToBottom();
          } else if (event.type === 'done') {
            // 4. When streaming completes: finalize bubble, show suggestions, re-enable input
            if (cursorSpan) cursorSpan.remove();

            if (event.session_id) {
              currentSessionId = event.session_id;
              sessionStorage.setItem('weboum_session_id', currentSessionId);
              sessionIdDisplay.textContent = currentSessionId.substring(0, 8) + '...';
            }

            currentMode = event.mode || 'general';
            currentStep = event.step || null;
            isCompleted = !!event.completed;

            updateEnquiryTracker();
            updateInputPlaceholder('text', currentStep);

            const suggestions = event.suggestions || ['Anything Else?', 'Enquire Now'];
            if (suggestions.length > 0 && activeBotGroup) {
              const wrapper = activeBotGroup.querySelector('.msg-content-wrapper');
              const timestampEl = activeBotGroup.querySelector('.msg-timestamp');
              const suggestionsEl = document.createElement('div');
              suggestionsEl.className = 'suggestions-container';
              suggestionsEl.innerHTML = suggestions.map(s =>
                `<button class="chip-btn" onclick="selectChip('${escapeHtml(s)}')">${escapeHtml(s)}</button>`
              ).join('');
              wrapper.insertBefore(suggestionsEl, timestampEl);
            }
            scrollToBottom();
          } else if (event.type === 'error') {
            if (cursorSpan) cursorSpan.remove();
            if (bubbleTextSpan) {
              bubbleTextSpan.textContent = `⚠️ ${event.message || 'An error occurred during generation.'}`;
            } else {
              appendBotMessage(`⚠️ ${event.message || 'An error occurred during generation.'}`, []);
            }
          }
        } catch (parseErr) {
          console.error('SSE JSON parse error:', parseErr, jsonStr);
        }
      }
    }

    // Safety fallback if connection closed without explicit done event
    if (cursorSpan) cursorSpan.remove();
    if (activeBotGroup && accumulatedText && !activeBotGroup.querySelector('.suggestions-container')) {
      const wrapper = activeBotGroup.querySelector('.msg-content-wrapper');
      const timestampEl = activeBotGroup.querySelector('.msg-timestamp');
      const suggestionsEl = document.createElement('div');
      suggestionsEl.className = 'suggestions-container';
      suggestionsEl.innerHTML = ['Anything Else?', 'Enquire Now'].map(s =>
        `<button class="chip-btn" onclick="selectChip('${escapeHtml(s)}')">${escapeHtml(s)}</button>`
      ).join('');
      wrapper.insertBefore(suggestionsEl, timestampEl);
      scrollToBottom();
    }
  } catch (err) {
    console.error('Chat error:', err);
    if (cursorSpan) cursorSpan.remove();
    if (!activeBotGroup) {
      appendBotMessage("⚠️ Connection error. Please verify the backend server.", []);
    }
  } finally {
    if (cursorSpan) cursorSpan.remove();
    setLoading(false);
  }
}

function handleBotResponse(data) {
  if (data.session_id) {
    currentSessionId = data.session_id;
    sessionStorage.setItem('weboum_session_id', currentSessionId);
    sessionIdDisplay.textContent = currentSessionId.substring(0, 8) + '...';
  }

  currentMode = data.mode || 'initial';
  currentStep = data.step || null;
  isCompleted = !!data.completed;

  updateEnquiryTracker();
  updateInputPlaceholder(data.type, currentStep);

  appendBotMessage(data.message, data.suggestions || []);
}

function updateEnquiryTracker() {
  if (currentMode === 'enquiry') {
    trackerBar.classList.add('active');
    if (isCompleted) {
      trackerStepTag.textContent = 'Enquiry Complete 🎉';
      trackerStepTag.style.color = 'var(--success)';
      trackerProgressFill.style.width = '100%';
    } else if (currentStep) {
      const stepIdx = ENQUIRY_STEPS.indexOf(currentStep);
      const stepNum = stepIdx !== -1 ? stepIdx + 1 : 1;
      const progress = Math.round((stepNum / ENQUIRY_STEPS.length) * 100);
      const formattedStep = currentStep.replace(/_/g, ' ');
      trackerStepTag.textContent = `Step ${stepNum}/${ENQUIRY_STEPS.length}: ${formattedStep}`;
      trackerStepTag.style.color = 'var(--accent)';
      trackerProgressFill.style.width = `${progress}%`;
    }
  } else {
    trackerBar.classList.remove('active');
  }
}

function updateInputPlaceholder(type, step) {
  messageInput.type = 'text';

  if (type === 'phone' || step === 'phone_number') {
    messageInput.placeholder = 'Enter phone number (e.g. +44 7700 900123 or +1 555-123-4567)...';
    messageInput.type = 'tel';
  } else if (type === 'email' || step === 'work_email') {
    messageInput.placeholder = 'Enter work email (e.g. name@company.com)...';
    messageInput.type = 'email';
  } else if (step === 'full_name') {
    messageInput.placeholder = 'Enter full name (e.g. Ada Lovelace)...';
  } else if (step === 'company_name') {
    messageInput.placeholder = 'Enter company or organization name...';
  } else if (step === 'current_technology_stack') {
    messageInput.placeholder = 'e.g. WhatsApp, Salesforce, Excel, Python...';
  } else if (type === 'options') {
    if (currentMode === 'initial') {
      messageInput.placeholder = 'Choose "Business Enquiry" or "Website / General Question" above...';
    } else {
      messageInput.placeholder = 'Click an option above or type your reply...';
    }
  } else {
    messageInput.placeholder = 'Type your question or message...';
  }
  messageInput.focus();
}

function appendUserMessage(text) {
  const group = document.createElement('div');
  group.className = 'message-group user';
  group.innerHTML = `
    <div class="msg-avatar">You</div>
    <div class="msg-content-wrapper">
      <div class="message-bubble">${escapeHtml(text)}</div>
      <div class="msg-timestamp">${formatTime(new Date())}</div>
    </div>
  `;
  messagesContainer.appendChild(group);
  scrollToBottom();
}

function appendBotMessage(text, suggestions) {
  const group = document.createElement('div');
  group.className = 'message-group bot';

  let suggestionsHtml = '';
  if (suggestions && suggestions.length > 0) {
    suggestionsHtml = `
      <div class="suggestions-container">
        ${suggestions.map(s => `<button class="chip-btn" onclick="selectChip('${escapeHtml(s)}')">${escapeHtml(s)}</button>`).join('')}
      </div>
    `;
  }

  group.innerHTML = `
    <div class="msg-avatar">AI</div>
    <div class="msg-content-wrapper">
      <div class="message-bubble">${escapeHtml(text)}</div>
      ${suggestionsHtml}
      <div class="msg-timestamp">${formatTime(new Date())}</div>
    </div>
  `;
  messagesContainer.appendChild(group);
  scrollToBottom();
}

window.selectChip = function(optionText) {
  if (isWaiting) return;
  appendUserMessage(optionText);
  disableActiveChips();
  sendMessage(optionText);
};

function disableActiveChips() {
  const chips = document.querySelectorAll('.chip-btn:not(:disabled)');
  chips.forEach(c => c.disabled = true);
}

function setLoading(loading) {
  isWaiting = loading;
  sendButton.disabled = loading;
  if (loading) {
    if (currentMode !== 'general') {
      typingIndicator.classList.add('active');
    }
    scrollToBottom();
  } else {
    typingIndicator.classList.remove('active');
    messageInput.focus();
  }
}

function resetChat() {
  currentSessionId = null;
  sessionStorage.removeItem('weboum_session_id');
  sessionIdDisplay.textContent = 'Generating...';
  messagesContainer.innerHTML = '';
  trackerBar.classList.remove('active');
  initChat();
}

function copySessionId() {
  if (!currentSessionId) return;
  navigator.clipboard.writeText(currentSessionId).then(() => {
    const original = sessionIdDisplay.textContent;
    sessionIdDisplay.textContent = 'Copied!';
    setTimeout(() => {
      sessionIdDisplay.textContent = original;
    }, 1500);
  });
}

// Sessions Inspector Modal
async function openSessionsModal() {
  sessionsModal.classList.add('open');
  await loadSessionsData();
}

function closeSessionsModal() {
  sessionsModal.classList.remove('open');
}

async function loadSessionsData() {
  sessionsContent.innerHTML = '<p style="color: var(--text-dim);">Loading active sessions from backend...</p>';
  try {
    const res = await fetch(`${API_BASE}/api/all-sessions`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    const sessionKeys = Object.keys(data.sessions || {});
    if (sessionKeys.length === 0) {
      sessionsContent.innerHTML = '<p style="color: var(--text-dim); padding: 20px 0;">No active sessions in server memory yet. Start chatting to create one!</p>';
      return;
    }

    let html = `
      <div style="display: flex; gap: 12px; margin-bottom: 12px;">
        <span class="badge-tag">Total Sessions: ${data.total_sessions}</span>
        <span class="badge-tag" style="background: rgba(16, 185, 129, 0.15); color: #6ee7b7; border-color: rgba(16, 185, 129, 0.3);">
          Completed Enquiries: ${(data.completed_enquiries || []).length}
        </span>
      </div>
    `;

    sessionKeys.reverse().forEach(id => {
      const s = data.sessions[id];
      const isCurrent = id === currentSessionId;
      html += `
        <div class="session-card" style="${isCurrent ? 'border-color: var(--primary);' : ''}">
          <div class="session-card-header">
            <div>
              <span class="session-id-text">${id}</span>
              ${isCurrent ? '<span class="badge-tag" style="margin-left: 8px;">Active Chat</span>' : ''}
            </div>
            <div style="display: flex; gap: 8px;">
              <span class="badge-tag" style="text-transform: uppercase;">${s.mode}</span>
              <span class="badge-tag" style="${s.completed ? 'background: rgba(16,185,129,0.2); color: #34d399;' : ''}">
                ${s.completed ? 'Completed' : (s.current_step || 'In Progress')}
              </span>
            </div>
          </div>
          <div class="session-field-grid">
            ${Object.entries(s.data || {}).map(([k, v]) => `
              <div class="session-field-item">
                <span class="field-key">${k.replace(/_/g, ' ')}</span>
                <span class="field-val ${!v ? 'empty' : ''}" title="${v || 'None'}">${v || 'Not provided yet'}</span>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    });

    sessionsContent.innerHTML = html;
  } catch (err) {
    sessionsContent.innerHTML = `<p style="color: var(--error);">Error fetching sessions: ${err.message}</p>`;
  }
}

function scrollToBottom() {
  setTimeout(() => {
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }, 30);
}

function escapeHtml(str) {
  if (!str) return '';
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function formatTime(date) {
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
