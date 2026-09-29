/* SymbioLink AI assistant: chat panel with voice input and spoken replies.
 *
 * Voice uses the browser's built-in Web Speech API (free, no key):
 *   - SpeechRecognition for asking by voice (Chrome/Edge/Safari; the mic
 *     button is hidden where it isn't supported, e.g. Firefox)
 *   - speechSynthesis for reading replies aloud
 * Language follows the app's EN/हिंदी switch (en-IN / hi-IN).
 */
(function () {
  'use strict';

  var root = document.getElementById('sl-assistant');
  if (!root) { return; }

  var endpoint = root.getAttribute('data-endpoint');
  var lang = root.getAttribute('data-lang') === 'hi' ? 'hi' : 'en';
  var speechLang = lang === 'hi' ? 'hi-IN' : 'en-IN';
  var hi = lang === 'hi';

  var launcher = root.querySelector('.asst-launcher');
  var panel = root.querySelector('.asst-panel');
  var list = root.querySelector('.asst-messages');
  var form = root.querySelector('.asst-form');
  var input = root.querySelector('.asst-input');
  var sendBtn = root.querySelector('.asst-send');
  var micBtn = root.querySelector('.asst-mic');
  var speakBtn = root.querySelector('.asst-speak-toggle');
  var clearBtn = root.querySelector('.asst-clear');
  var closeBtn = root.querySelector('.asst-close');
  var suggestions = root.querySelector('.asst-suggestions');

  /* History is stored per user, so a different account signed in later on
     the same browser tab never sees someone else's conversation. */
  var HISTORY_PREFIX = 'sl-assistant-history';
  var STORE_KEY = HISTORY_PREFIX + ':' + (root.getAttribute('data-user-id') || 'anon');
  var SPEAK_KEY = 'sl-assistant-speak';
  var history = [];
  var busy = false;
  var speakAlways = false;

  function store(key, value) {
    try { sessionStorage.setItem(key, value); } catch (e) { /* private mode etc. */ }
  }
  function load(key) {
    try { return sessionStorage.getItem(key); } catch (e) { return null; }
  }

  /* Drop every stored conversation except (optionally) this user's. */
  function forgetHistories(keepKey) {
    try {
      for (var i = sessionStorage.length - 1; i >= 0; i--) {
        var k = sessionStorage.key(i);
        if (k && k.indexOf(HISTORY_PREFIX) === 0 && k !== keepKey) { sessionStorage.removeItem(k); }
      }
    } catch (e) { /* storage unavailable */ }
  }

  /* Signing out wipes the conversation from this tab entirely. */
  Array.prototype.forEach.call(document.querySelectorAll('a[href$="/logout"]'), function (a) {
    a.addEventListener('click', function () {
      forgetHistories(null);
      if ('speechSynthesis' in window) { window.speechSynthesis.cancel(); }
    });
  });

  /* ---------- rendering ---------- */

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* Tiny, safe markdown: escape everything first, then allow **bold**,
     [text](/same-origin-path) links, bullet lines and line breaks. Links are
     restricted to paths on this site so a reply can never point elsewhere. */
  function renderMarkdown(text) {
    var html = escapeHtml(text);
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\[([^\]]+)\]\((\/[^)\s]*)\)/g, function (_m, label, href) {
      return '<a href="' + href + '">' + label + '</a>';
    });
    var lines = html.split('\n');
    var out = [];
    var inList = false;
    lines.forEach(function (line) {
      var m = line.match(/^\s*[-*•]\s+(.*)$/);
      if (m) {
        if (!inList) { out.push('<ul>'); inList = true; }
        out.push('<li>' + m[1] + '</li>');
      } else {
        if (inList) { out.push('</ul>'); inList = false; }
        if (line.trim()) { out.push('<p>' + line + '</p>'); }
      }
    });
    if (inList) { out.push('</ul>'); }
    return out.join('');
  }

  function plainForSpeech(text) {
    return String(text)
      .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
      .replace(/\*\*/g, '')
      .replace(/^\s*[-*•]\s+/gm, '')
      .replace(/Rs\s?/g, hi ? 'रुपये ' : 'rupees ')
      .replace(/₹/g, hi ? 'रुपये ' : 'rupees ')
      .replace(/\bkg\b/g, hi ? 'किलो' : 'kilograms');
  }

  function addBubble(role, text, mode) {
    var el = document.createElement('div');
    el.className = 'asst-msg asst-' + role;
    if (role === 'assistant') {
      el.innerHTML = renderMarkdown(text);
      if (mode === 'basic') {
        var tag = document.createElement('span');
        tag.className = 'asst-mode';
        tag.textContent = hi ? 'बेसिक मोड' : 'Basic mode';
        tag.title = hi ? 'पूरे एआई जवाबों के लिए GEMINI_API_KEY सेट करें'
                       : 'Full AI answers need GEMINI_API_KEY to be set on the server';
        el.appendChild(tag);
      }
      var play = document.createElement('button');
      play.type = 'button';
      play.className = 'asst-play';
      play.title = hi ? 'सुनें' : 'Listen';
      play.setAttribute('aria-label', play.title);
      play.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path></svg>';
      play.addEventListener('click', function () { speak(text); });
      if ('speechSynthesis' in window) { el.appendChild(play); }
    } else {
      el.textContent = text;
    }
    list.appendChild(el);
    list.scrollTop = list.scrollHeight;
    return el;
  }

  function greet() {
    var name = root.getAttribute('data-user') || '';
    addBubble('assistant', hi
      ? 'नमस्ते ' + name + '! मैं आपका SymbioLink सहायक हूं। मैं आपके कचरे के खरीदार, ज़रूरी सामग्री के विक्रेता, भाव और आपके ऑर्डर की जानकारी दे सकता हूं। लिखें या 🎤 दबाकर बोलें।'
      : 'Hi ' + name + '! I\'m your SymbioLink assistant. I can find buyers for your waste, suppliers for what you need, compare prices and explain your orders. Type, or tap 🎤 and speak.');
  }

  function renderHistory() {
    list.innerHTML = '';
    greet();
    history.forEach(function (m) { addBubble(m.role, m.text, m.mode); });
    suggestions.hidden = history.length > 0;
  }

  /* ---------- open / close ---------- */

  function setOpen(open) {
    panel.hidden = !open;
    root.classList.toggle('open', open);
    launcher.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) {
      setTimeout(function () { input.focus(); }, 50);
      list.scrollTop = list.scrollHeight;
    } else {
      stopListening();
      if ('speechSynthesis' in window) { window.speechSynthesis.cancel(); }
    }
  }
  launcher.addEventListener('click', function () { setOpen(panel.hidden); });
  closeBtn.addEventListener('click', function () { setOpen(false); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !panel.hidden) { setOpen(false); }
  });

  clearBtn.addEventListener('click', function () {
    history = [];
    store(STORE_KEY, '[]');
    if ('speechSynthesis' in window) { window.speechSynthesis.cancel(); }
    renderHistory();
  });

  /* ---------- speech output ---------- */

  var voices = [];
  function refreshVoices() {
    if ('speechSynthesis' in window) { voices = window.speechSynthesis.getVoices() || []; }
  }
  if ('speechSynthesis' in window) {
    refreshVoices();
    window.speechSynthesis.onvoiceschanged = refreshVoices;
  } else {
    speakBtn.hidden = true;
  }

  function pickVoice() {
    var exact = voices.filter(function (v) { return v.lang === speechLang; });
    if (exact.length) {
      var google = exact.filter(function (v) { return /google/i.test(v.name); });
      return (google[0] || exact[0]);
    }
    var prefix = voices.filter(function (v) { return v.lang && v.lang.indexOf(lang) === 0; });
    return prefix[0] || null;
  }

  function speak(text) {
    if (!('speechSynthesis' in window)) { return; }
    window.speechSynthesis.cancel();
    var u = new SpeechSynthesisUtterance(plainForSpeech(text));
    u.lang = speechLang;
    var v = pickVoice();
    if (v) { u.voice = v; }
    u.rate = 1;
    window.speechSynthesis.speak(u);
  }

  function setSpeakAlways(on) {
    speakAlways = on;
    speakBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    speakBtn.classList.toggle('on', on);
    store(SPEAK_KEY, on ? '1' : '0');
    if (!on && 'speechSynthesis' in window) { window.speechSynthesis.cancel(); }
  }
  speakBtn.addEventListener('click', function () { setSpeakAlways(!speakAlways); });

  /* ---------- speech input ---------- */

  var Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  var recognizer = null;
  var listening = false;
  var askedByVoice = false;

  if (!Recognition) {
    micBtn.hidden = true;
  }

  function stopListening() {
    if (recognizer && listening) {
      try { recognizer.stop(); } catch (e) { /* already stopped */ }
    }
  }

  function startListening() {
    if (!Recognition || busy) { return; }
    if ('speechSynthesis' in window) { window.speechSynthesis.cancel(); }
    recognizer = new Recognition();
    recognizer.lang = speechLang;
    recognizer.interimResults = true;
    recognizer.continuous = false;
    recognizer.maxAlternatives = 1;

    var finalText = '';
    recognizer.onstart = function () {
      listening = true;
      micBtn.classList.add('listening');
      micBtn.setAttribute('aria-pressed', 'true');
      input.placeholder = hi ? 'सुन रहा हूं… बोलिए' : 'Listening… speak now';
    };
    recognizer.onresult = function (event) {
      var interim = '';
      for (var i = event.resultIndex; i < event.results.length; i++) {
        var chunk = event.results[i][0].transcript;
        if (event.results[i].isFinal) { finalText += chunk; } else { interim += chunk; }
      }
      input.value = (finalText + interim).trim();
      autosize();
    };
    recognizer.onerror = function (event) {
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        addBubble('assistant', hi
          ? 'माइक्रोफ़ोन की अनुमति नहीं मिली। ब्राउज़र के एड्रेस बार में माइक आइकन से अनुमति दें।'
          : 'Microphone access was blocked. Allow it from the mic icon in your browser\'s address bar.');
      } else if (event.error === 'no-speech') {
        input.placeholder = hi ? 'कुछ सुनाई नहीं दिया, फिर से कोशिश करें' : 'Didn\'t catch that, try again';
      }
    };
    recognizer.onend = function () {
      listening = false;
      micBtn.classList.remove('listening');
      micBtn.setAttribute('aria-pressed', 'false');
      input.placeholder = hi ? 'अपना सवाल लिखें…' : 'Ask about buyers, prices, orders…';
      var text = input.value.trim();
      if (text) {
        askedByVoice = true;
        send(text);
      }
    };
    try { recognizer.start(); } catch (e) { /* double start */ }
  }

  micBtn.addEventListener('click', function () {
    if (listening) { stopListening(); } else { startListening(); }
  });

  /* ---------- sending ---------- */

  function autosize() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 110) + 'px';
  }
  input.addEventListener('input', autosize);
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event('submit'));
    }
  });

  function setBusy(on) {
    busy = on;
    sendBtn.disabled = on;
    micBtn.disabled = on;
    root.classList.toggle('busy', on);
  }

  function send(text) {
    text = String(text || '').trim();
    if (!text || busy) { return; }
    var voice = askedByVoice;
    askedByVoice = false;

    history.push({ role: 'user', text: text });
    addBubble('user', text);
    suggestions.hidden = true;
    input.value = '';
    autosize();
    setBusy(true);

    var typing = document.createElement('div');
    typing.className = 'asst-msg asst-assistant asst-typing';
    typing.innerHTML = '<span></span><span></span><span></span>';
    list.appendChild(typing);
    list.scrollTop = list.scrollHeight;

    fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({
        lang: lang,
        messages: history.slice(-12).map(function (m) { return { role: m.role, text: m.text }; })
      })
    })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (body) {
          if (!r.ok && !body.reply) { throw new Error('HTTP ' + r.status); }
          return body;
        });
      })
      .then(function (body) {
        var reply = body.reply || (hi ? 'माफ़ कीजिए, जवाब नहीं मिल सका।' : 'Sorry, I could not get an answer.');
        history.push({ role: 'assistant', text: reply, mode: body.mode });
        typing.remove();
        addBubble('assistant', reply, body.mode);
        if (voice || speakAlways) { speak(reply); }
      })
      .catch(function () {
        typing.remove();
        addBubble('assistant', hi
          ? 'सर्वर से जुड़ नहीं पाया। कृपया दोबारा कोशिश करें।'
          : 'I couldn\'t reach the server. Please try again.');
        history.pop();
      })
      .then(function () {
        setBusy(false);
        store(STORE_KEY, JSON.stringify(history.slice(-30)));
      });
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    send(input.value);
  });

  Array.prototype.forEach.call(suggestions.querySelectorAll('button'), function (b) {
    b.addEventListener('click', function () { send(b.textContent); });
  });

  /* ---------- init ---------- */

  forgetHistories(STORE_KEY);  // also clears the old shared, un-keyed history

  try {
    var saved = JSON.parse(load(STORE_KEY) || '[]');
    if (Array.isArray(saved)) { history = saved.filter(function (m) { return m && m.text; }); }
  } catch (e) { history = []; }
  setSpeakAlways(load(SPEAK_KEY) === '1');
  renderHistory();
})();
