/** 智能客服聊天挂件 — 跨页面上下文 + 流式输出 + 思维链 */

const HISTORY_KEY = 'chat_history_v1';
const OPEN_KEY = 'chat_open_v1';
let chatHistory = [];
let pendingConfirm = null;
let isOpen = false;

loadHistory();
restoreMessages();

// -- Toggle --

window.toggleChat = function() {
  const panel = document.getElementById('chatPanel');
  const badge = document.getElementById('chatBadge');
  isOpen = !isOpen;
  panel.style.display = isOpen ? 'flex' : 'none';
  localStorage.setItem(OPEN_KEY, isOpen ? '1' : '0');
  if (isOpen) {
    badge.style.display = 'none';
    document.getElementById('chatInput').focus();
  }
};

// -- Send (streaming) --

window.sendChat = async function() {
  const input = document.getElementById('chatInput');
  const btn = document.getElementById('chatSendBtn');
  const message = input.value.trim();
  if (!message || btn.disabled) return;

  btn.disabled = true;
  input.value = '';
  input.style.height = 'auto';

  appendMessage('user', message);
  chatHistory.push({ role: 'user', content: message });
  saveHistory();

  // 创建一个 AI 消息气泡用于流式填充
  const msgId = createStreamingBubble();

  try {
    const r = await fetch('/chat/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, history: chatHistory }),
    });

    if (!r.ok) {
      finishStreamingBubble(msgId, '抱歉，服务暂不可用（HTTP ' + r.status + '）。');
      return;
    }

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let contentText = '';
    let thinkingText = '';
    let toolCalls = [];
    let confirmData = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      let eventType = '';
      for (const line of lines) {
        if (line.startsWith('event: ')) {
          eventType = line.slice(7).trim();
        } else if (line.startsWith('data: ') && eventType) {
          try {
            const data = JSON.parse(line.slice(6));
            handleEvent(msgId, eventType, data);

            if (eventType === 'content') contentText += data.text || '';
            if (eventType === 'thinking') thinkingText += data.text || '';
            if (eventType === 'done') {
              toolCalls = data.tool_calls || [];
              confirmData = data.confirm || null;
            }
          } catch(e) {}
          eventType = '';
        }
      }
    }

    // 完成
    finishStreaming(msgId, contentText, thinkingText, confirmData);
    chatHistory.push({ role: 'assistant', content: contentText || '(空)' });
    if (confirmData) chatHistory.push({ role: 'system', content: '等待用户确认' });
    saveHistory();

    if (!isOpen) {
      document.getElementById('chatBadge').style.display = 'flex';
      document.getElementById('chatBadge').textContent = '1';
    }
  } catch (e) {
    finishStreamingBubble(msgId, '网络请求失败: ' + e.message);
  } finally {
    btn.disabled = false;
  }
};

function handleEvent(msgId, type, data) {
  const bubble = document.getElementById(msgId);
  if (!bubble) return;

  if (type === 'tool_start') {
    const el = bubble.querySelector('.chat-tools');
    if (el) {
      const span = document.createElement('span');
      span.className = 'chat-tool-badge';
      span.textContent = '🔧 ' + data.name;
      el.appendChild(span);
    }
  } else if (type === 'thinking') {
    let el = bubble.querySelector('.chat-thinking');
    if (!el) {
      el = document.createElement('div');
      el.className = 'chat-thinking';
      el.innerHTML = '<div class="chat-thinking-header" onclick="this.parentElement.classList.toggle(\'hidden\')">🧠 思考中… <span class="chat-thinking-toggle"></span></div><div class="chat-thinking-body"></div>';
      bubble.insertBefore(el, bubble.querySelector('.chat-content'));
    }
    const body = el.querySelector('.chat-thinking-body');
    if (body) {
      body.textContent += data.text || '';
      const header = el.querySelector('.chat-thinking-header');
      if (header) header.childNodes[0].textContent = '🧠 思考中… (' + body.textContent.length + '字) ';
    }
    scrollToBottom();
  } else if (type === 'content') {
    const el = bubble.querySelector('.chat-content');
    if (el) {
      if (el.querySelector('.chat-typing')) el.innerHTML = '';
      el.innerHTML += formatContent(data.text || '');
    }
    scrollToBottom();
  } else if (type === 'tool_result') {
    const el = bubble.querySelector('.chat-tools');
    if (el) {
      const r = data.result;
      const ok = r && r.found !== undefined ? (r.found ? '✓' : '✗') : '';
      el.lastChild && (el.lastChild.textContent += ' ' + ok);
    }
  } else if (type === 'error') {
    bubble.querySelector('.chat-content').innerHTML = '<span style="color:#f87171">错误: ' + esc(data.message || '') + '</span>';
  }
}

// -- Confirm (keep non-streaming) --

window.confirmAction = async function() {
  if (!pendingConfirm) return;
  try {
    const r = await fetch('/chat/api/chat/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: pendingConfirm }),
    });
    const data = await r.json();
    const cards = document.querySelectorAll('.chat-confirm-card');
    const card = cards[cards.length - 1];
    if (card) {
      const bubble = card.closest('.chat-msg-bubble');
      const ok = data.status === 'confirmed';
      const div = document.createElement('div');
      div.style.cssText = 'margin-top:8px;font-weight:600;' + (ok ? 'color:#4ade80' : 'color:#f87171');
      div.textContent = ok ? '✓ ' + (data.message || '已确认') : '✗ ' + (data.message || '操作失败');
      bubble.appendChild(div);
      card.querySelector('.chat-confirm-actions')?.remove();
    }
    chatHistory.push({ role: 'system', content: data.status === 'confirmed' ? '已确认:' + data.message : '失败:' + (data.message || '') });
    saveHistory();
    pendingConfirm = null;
  } catch (e) {
    appendMessage('assistant', '确认失败: ' + e.message);
  }
};

window.cancelConfirm = function() {
  const cards = document.querySelectorAll('.chat-confirm-card');
  const card = cards[cards.length - 1];
  if (card) {
    const bubble = card.closest('.chat-msg-bubble');
    const div = document.createElement('div');
    div.style.cssText = 'margin-top:8px;color:rgba(255,255,255,0.4)';
    div.textContent = '已取消';
    bubble.appendChild(div);
    card.querySelector('.chat-confirm-actions')?.remove();
  }
  chatHistory.push({ role: 'system', content: '用户取消' });
  saveHistory();
  pendingConfirm = null;
};

// -- Clear --

window.clearChat = function() {
  if (chatHistory.length > 0 && !confirm('确定开始新对话？之前的上下文将被清除。')) return;
  chatHistory = [];
  pendingConfirm = null;
  saveHistory();
  document.getElementById('chatMessages').innerHTML =
    '<div class="chat-msg chat-msg-assistant"><div class="chat-msg-bubble">' +
    '<div class="chat-content">对话已清空。有什么可以帮你的？</div></div></div>';
};

// -- Streaming helpers --

function createStreamingBubble() {
  const id = 'msg-' + Date.now();
  const el = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.id = id;
  div.className = 'chat-msg chat-msg-assistant';
  div.innerHTML =
    '<div class="chat-msg-bubble">' +
      '<div class="chat-tools"></div>' +
      '<div class="chat-thinking hidden">' +
        '<div class="chat-thinking-header" onclick="this.parentElement.classList.toggle(\'hidden\')">🧠 思考中… <span class="chat-thinking-toggle"></span></div>' +
        '<div class="chat-thinking-body"></div>' +
      '</div>' +
      '<div class="chat-content"><div class="chat-typing"><span></span><span></span><span></span></div></div>' +
    '</div>';
  el.appendChild(div);
  scrollToBottom();
  return id;
}

function finishStreaming(msgId, content, thinking, confirm) {
  const bubble = document.getElementById(msgId);
  if (!bubble) return;

  // 隐藏空思维链
  if (!thinking && bubble.querySelector('.chat-thinking')) {
    bubble.querySelector('.chat-thinking').remove();
  } else if (thinking) {
    const body = bubble.querySelector(".chat-thinking-body");
    const header = bubble.querySelector(".chat-thinking-header");
    if (header && body) header.childNodes[0].textContent = "🧠 思考过程 (" + body.textContent.length + "字) ";
  }

  // 确保内容可见
  const contentEl = bubble.querySelector('.chat-content');
  if (!content.trim() && contentEl) {
    contentEl.innerHTML = '(无内容)';
  }

  // 确认卡片
  if (confirm && contentEl) {
    const card = document.createElement('div');
    card.className = 'chat-confirm-card';
    card.innerHTML =
      '<div class="confirm-title">确认发送此邮件？</div>' +
      '<div class="chat-confirm-actions">' +
        '<button class="chat-confirm-ok" onclick="window.confirmAction()">✓ 确认</button>' +
        '<button class="chat-confirm-cancel" onclick="window.cancelConfirm()">取消</button>' +
      '</div>';
    contentEl.appendChild(card);
    pendingConfirm = confirm;
  }
}

function finishStreamingBubble(msgId, text) {
  const bubble = document.getElementById(msgId);
  if (bubble) {
    bubble.querySelector('.chat-content').innerHTML = formatContent(text);
  }
}

// -- Helpers --

function appendMessage(role, content) {
  const el = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = 'chat-msg chat-msg-' + role;
  div.innerHTML = '<div class="chat-msg-bubble"><div class="chat-content">' + formatContent(content) + '</div></div>';
  el.appendChild(div);
  scrollToBottom();
}

function scrollToBottom() {
  const el = document.getElementById('chatMessages');
  el.scrollTop = el.scrollHeight;
}

function formatContent(text) {
  if (!text) return '';
  let out = esc(text);
  out = out.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/\n/g, '<br>');
  return out;
}

function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function loadHistory() {
  try { chatHistory = JSON.parse(localStorage.getItem(HISTORY_KEY)) || []; }
  catch { chatHistory = []; }
  isOpen = localStorage.getItem(OPEN_KEY) === '1';
  if (isOpen) document.getElementById('chatPanel').style.display = 'flex';
}

function restoreMessages() {
  if (!chatHistory.length) return;
  const container = document.getElementById('chatMessages');
  for (const h of chatHistory) {
    if (h.role === 'user') {
      appendMessage('user', h.content);
    } else if (h.role === 'assistant' && h.content) {
      appendMessage('assistant', h.content);
    }
  }
}

function saveHistory() {
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(chatHistory.slice(-30))); }
  catch {}
}
