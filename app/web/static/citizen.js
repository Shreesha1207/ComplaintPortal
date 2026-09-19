/* Citizen intake — four channels, one pipeline.

   VOICE HAS TWO PATHS, and the server decides which one this browser gets.

   1. RECORD AND UPLOAD (preferred). MediaRecorder captures audio, we POST it
      to /api/voice/transcribe, and the server transcribes it. This works in
      every browser, and the model is a deployment decision rather than
      whatever the citizen's browser happens to ship.

   2. BROWSER RECOGNITION (fallback). window.SpeechRecognition, used only when
      no server transcription is configured. It is genuinely weak for this
      project: Chrome/Safari only, secure-origin only — so it dies the moment
      you open the app from a phone at http://192.168.x.x:8000 — and its
      language coverage is worst exactly where this platform needs it most.

   When neither is usable we say so, specifically, instead of leaving a dead
   microphone button on the screen. Every failure below names its own cause.
*/
import { api, apiPost, fmt, renderNav, urgencyChip } from './viz.js';

const $ = (id) => document.getElementById(id);
const state = {
  pack: null, country: 'IN', district: null, lang: '',
  recognizing: false, serverSTT: false, voiceInfo: null,
  recorder: null, chunks: [],
};

const EXAMPLES = [
  ['hi', 'हमारे गांव में तीन महीने से पीने का पानी नहीं आ रहा है, बच्चे बीमार हो रहे हैं'],
  ['kn', 'ನಮ್ಮ ಶಾಲೆಯಲ್ಲಿ ಶಿಕ್ಷಕರಿಲ್ಲ, 200 ಕುಟುಂಬಗಳು ತೊಂದರೆಯಲ್ಲಿವೆ'],
  ['ta', 'எங்கள் கிராமத்தில் மருத்துவமனை இல்லை, அவசர நிலையில் ஆபத்து'],
  ['bn', 'আমাদের গ্রামে বিদ্যুৎ নেই, ৩০০ পরিবার ক্ষতিগ্রস্ত'],
  ['mr', 'आमच्या गावातील रस्त्यावर मोठे खड्डे आहेत'],
  ['te', 'మా గ్రామంలో సాగునీటి కాలువ పాడైంది, రైతులు నష్టపోతున్నారు'],
  ['pt', 'A falta de água na nossa comunidade já dura meses e uma criança morreu'],
  ['zu', 'Akukho amanzi ahlanzekile lapha, sicela usizo'],
  ['en', 'The road to our village has huge potholes and the bus no longer comes'],
];

/* ------------------------------------------------------------------ boot */
async function boot() {
  await renderNav($('nav'), '/citizen');
  const countries = await api('/api/countries');
  $('country').innerHTML = countries.map(c =>
    `<option value="${c.code}">${c.name}</option>`).join('');
  $('country').value = state.country;
  $('country').onchange = async (e) => { state.country = e.target.value; await loadPack(); };
  await loadPack();

  // tabs
  const tabs = [['t-voice', 'p-voice'], ['t-text', 'p-text'], ['t-wa', 'p-wa'], ['t-sms', 'p-sms']];
  for (const [tid, pid] of tabs) {
    $(tid).onclick = () => {
      for (const [t, p] of tabs) {
        const on = t === tid;
        $(t).setAttribute('aria-selected', String(on));
        $(p).hidden = !on;
      }
      if (pid === 'p-wa' && !$('wathread').childElementCount) waStart();
      if (pid === 'p-sms' && !$('smsout').textContent) smsStart();
    };
  }

  $('examples').innerHTML = EXAMPLES.map(([l, t], i) =>
    `<button class="ex" data-i="${i}">${l.toUpperCase()} · ${t.slice(0, 26)}…</button>`).join('');
  for (const b of $('examples').querySelectorAll('.ex')) {
    b.onclick = () => {
      const [l, t] = EXAMPLES[+b.dataset.i];
      $('freetext').value = t;
      if ([...$('lang').options].some(o => o.value === l)) { $('lang').value = l; state.lang = l; }
    };
  }

  $('voicesend').onclick = () => submit($('voicetext').value, 'voice');
  $('textsend').onclick = () => submit($('freetext').value, 'web');
  $('wasend').onclick = waSend;
  // Enter must not submit while an input method is still composing a word.
  // Transliteration keyboards (Gboard Hinglish -> Devanagari, Tamil, CJK, and
  // any on-device keyboard that composes) use Enter to ACCEPT the candidate.
  // Submitting on that keystroke sends the half-composed Latin text instead of
  // what the citizen actually wrote — a silent corruption, in exactly the
  // languages this platform exists to hear.
  //   e.isComposing  — the standard signal
  //   keyCode === 229 — the legacy signal browsers use mid-composition
  const composing = (e) => e.isComposing || e.keyCode === 229;
  $('wainput').onkeydown = (e) => { if (e.key === 'Enter' && !composing(e)) waSend(); };
  $('smssend').onclick = smsSend;
  $('smsinput').onkeydown = (e) => { if (e.key === 'Enter' && !composing(e)) smsSend(); };
  $('voicetext').oninput = () => {
    const n = $('voicetext').value.trim().length;
    $('voicelen').textContent = n ? `${n} characters` : '';
  };

  // Awaited: the mic must not be clickable before we know which path it takes.
  await setupMic();
  loadRecent();
}

async function loadPack() {
  state.pack = await api(`/api/countries/${state.country}`);
  const regions = state.pack.regions.slice().sort((a, b) => a.name.localeCompare(b.name));
  $('region').innerHTML = regions.map(r =>
    `<option value="${r.code}">${r.name}</option>`).join('');
  $('lang').innerHTML = '<option value="">Auto-detect</option>' + state.pack.languages.map(l =>
    `<option value="${l.code}">${l.native} (${l.name})</option>`).join('');
  $('region').onchange = fillDistricts;
  $('district').onchange = (e) => { state.district = e.target.value; };
  $('lang').onchange = (e) => { state.lang = e.target.value; };
  fillDistricts();
  loadRecent();
}

function fillDistricts() {
  const r = state.pack.regions.find(x => x.code === $('region').value);
  const ds = (r?.districts ?? []).slice().sort((a, b) => a.name.localeCompare(b.name));
  $('district').innerHTML = ds.map(d => `<option value="${d.code}">${d.name}</option>`).join('');
  state.district = ds[0]?.code ?? null;
}

/* ----------------------------------------------------------------- voice */
/* Diagnose precisely why voice is unavailable, instead of a dead button. */
function browserVoiceDiagnosis() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const secure = window.isSecureContext;
  const canRecord = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia
                       && window.MediaRecorder);
  if (!secure) {
    return { ok: false, reason:
      `This page is on an insecure origin (${location.protocol}//${location.host}), ` +
      'so browsers block microphone access entirely. Use https://, or open it as ' +
      'http://localhost. This is the usual reason voice fails when testing from a phone.' };
  }
  if (!canRecord && !SR) {
    return { ok: false, reason: 'This browser exposes neither MediaRecorder nor a speech engine.' };
  }
  if (!SR) {
    return { ok: false, reason:
      'This browser has no built-in speech engine (Firefox does not ship one). ' +
      'Configure server-side transcription and voice works here too.' };
  }
  return { ok: true, reason: null };
}

async function setupMic() {
  const mic = $('mic');

  // Ask the server which path this browser should take.
  try {
    state.voiceInfo = await api('/api/voice/status');
    state.serverSTT = !!state.voiceInfo.server_transcription;
  } catch {
    state.serverSTT = false;
  }

  if (state.serverSTT) {
    const p = state.voiceInfo.provider || {};
    $('micnote').innerHTML =
      `Recording in your browser and transcribing on the server via <b>${p.provider}</b>` +
      (p.model ? ` (${p.model})` : '') +
      '. Works in any browser, in any of the supported languages.';
    return setupRecorder(mic);
  }

  const diag = browserVoiceDiagnosis();
  if (!diag.ok) {
    $('micstatus').textContent = 'Voice input is unavailable in this browser.';
    $('micnote').innerHTML =
      `${diag.reason}<br><br>Type below instead — the pipeline is identical. ` +
      'To enable voice everywhere, configure server-side transcription ' +
      '(<code>GROQ_API_KEY</code>, or <code>XVOICE_STT_URL</code> for XVoice).';
    mic.disabled = true; mic.style.opacity = .5; mic.style.cursor = 'not-allowed';
    return;
  }
  $('micnote').innerHTML =
    'Using this browser’s own speech engine — Chrome/Safari only, and weak on ' +
    'Indic languages. Configure server-side transcription for reliable multilingual voice.';
  setupBrowserRecognition(mic);
}

/* ---- path 1: record in the browser, transcribe on the server ---------- */
function setupRecorder(mic) {
  mic.onclick = async () => {
    if (state.recognizing) {                       // stop and submit
      try { state.recorder && state.recorder.stop(); } catch { /* already stopped */ }
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      $('micstatus').textContent = e && e.name === 'NotAllowedError'
        ? 'Microphone permission denied — allow it, or type below instead.'
        : `Could not open the microphone (${e && e.name}) — type below instead.`;
      return;
    }
    // Pick a container the browser actually supports: Chrome/Firefox do webm,
    // Safari does mp4. Both are accepted by the transcription services.
    const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg']
      .find(t => window.MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t)) || '';
    const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    state.recorder = rec; state.chunks = [];

    rec.ondataavailable = (e) => { if (e.data && e.data.size) state.chunks.push(e.data); };
    rec.onstart = () => {
      state.recognizing = true; mic.classList.add('rec');
      mic.setAttribute('aria-label', 'Stop recording');
      $('micstatus').textContent = 'Recording… tap again when you have finished.';
    };
    rec.onstop = async () => {
      state.recognizing = false; mic.classList.remove('rec');
      mic.setAttribute('aria-label', 'Start voice recording');
      stream.getTracks().forEach(t => t.stop());
      const blob = new Blob(state.chunks, { type: rec.mimeType || 'audio/webm' });
      if (!blob.size) { $('micstatus').textContent = 'Nothing was recorded — try again.'; return; }

      $('micstatus').innerHTML = '<span class="spinner"></span> Transcribing…';
      try {
        const b64 = await blobToBase64(blob);
        const ext = (rec.mimeType || 'audio/webm').includes('mp4') ? 'mp4'
                  : (rec.mimeType || '').includes('ogg') ? 'ogg' : 'webm';
        const r = await apiPost('/api/voice/transcribe', {
          audio_base64: b64, filename: `audio.${ext}`,
          language: state.lang || null,          // omit → let the model detect
        });
        if (!r.text) { $('micstatus').textContent = 'No speech detected — try again.'; return; }
        const box = $('voicetext');
        box.value = (box.value ? box.value.trim() + ' ' : '') + r.text;
        box.dispatchEvent(new Event('input'));
        $('micstatus').textContent =
          `Captured${r.language ? ` (${r.language})` : ''}. Review the text, then submit.`;
      } catch (e) {
        $('micstatus').textContent = `Transcription failed: ${e.message}`;
      }
    };
    try { rec.start(); } catch (e) {
      $('micstatus').textContent = `Could not start recording (${e.message}).`;
    }
  };
}

const blobToBase64 = (blob) => new Promise((resolve, reject) => {
  const fr = new FileReader();
  fr.onerror = () => reject(new Error('Could not read the recording'));
  fr.onload = () => resolve(String(fr.result).split(',')[1]);   // strip data: prefix
  fr.readAsDataURL(blob);
});

/* ---- path 2: the browser's own engine (fallback) ---------------------- */
function setupBrowserRecognition(mic) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let rec = null;
  mic.onclick = () => {
    if (state.recognizing) { rec && rec.stop(); return; }
    // Never silently transcribe one language as another: if the citizen has
    // not chosen a language, say so rather than defaulting to English and
    // returning confident nonsense.
    const chosen = state.lang &&
      state.pack.languages.find(l => l.code === state.lang)?.bcp47;
    if (!chosen) {
      $('micstatus').textContent =
        'Choose your language above first — this browser engine cannot detect it, ' +
        'and will otherwise transcribe your speech as English.';
      return;
    }
    rec = new SR();
    rec.lang = chosen;
    rec.continuous = true; rec.interimResults = true;

    let settled = '';
    rec.onstart = () => {
      state.recognizing = true; mic.classList.add('rec');
      mic.setAttribute('aria-label', 'Stop recording');
      $('micstatus').textContent = `Listening (${rec.lang})… tap again to stop.`;
    };
    rec.onresult = (ev) => {
      let interim = '';
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const t = ev.results[i][0].transcript;
        if (ev.results[i].isFinal) settled += t + ' '; else interim += t;
      }
      $('voicetext').value = (settled + interim).trim();
      $('voicetext').dispatchEvent(new Event('input'));
    };
    rec.onerror = (e) => {
      const why = {
        'not-allowed': 'Microphone permission denied.',
        'service-not-allowed': 'The browser blocked its speech service.',
        'no-speech': 'No speech detected.',
        'network': 'This browser sends audio to its vendor for recognition, and that '
                 + 'call failed. Server-side transcription avoids this entirely.',
        'language-not-supported': `This browser cannot recognise ${rec.lang}. `
                 + 'Server-side transcription covers far more languages.',
      }[e.error] || `Speech input error (${e.error}).`;
      $('micstatus').textContent = `${why} Type below instead.`;
    };
    rec.onend = () => {
      state.recognizing = false; mic.classList.remove('rec');
      mic.setAttribute('aria-label', 'Start voice recording');
      const msg = $('micstatus').textContent;
      if (!/error|denied|blocked|cannot|failed|detected/i.test(msg)) {
        $('micstatus').textContent = $('voicetext').value.trim()
          ? 'Captured. Review the text, then submit.' : 'Tap and speak in your own language.';
      }
    };
    try { rec.start(); } catch { /* already started */ }
  };
}

/* ---------------------------------------------------------------- submit */
async function submit(text, channel) {
  text = (text || '').trim();
  if (!text) { flash('Please enter or speak your request first.', true); return; }
  if (!state.district) { flash('Please choose a district.', true); return; }
  try {
    const r = await apiPost('/api/requests', {
      text, country: state.country, district_code: state.district,
      channel, language: state.lang || null,
    });
    showResult(r);
    loadRecent();
    return r;
  } catch (e) {
    flash(e.message, true);
    return null;
  }
}

function showResult(r) {
  const box = $('result');
  box.hidden = false;
  const held = r.status === 'review';
  box.innerHTML = `
    <div class="card"><div class="card-b">
      <div class="result" style="border-left-color:${held ? 'var(--status-warning)'
                                                          : 'var(--status-good)'}">
        <div class="spread" style="margin-bottom:9px">
          <b>${held ? 'Received — held for human review' : 'Received and classified'}</b>
          <span class="mono xs muted">${r.id}</span>
        </div>
        <div class="row" style="gap:7px;margin-bottom:10px">
          <span class="chip">${r.language.toUpperCase()}</span>
          <span class="chip">${r.sector}</span>
          ${urgencyChip(r.urgency)}
          <span class="chip">${fmt.n(r.affected_population)} people</span>
          <span class="chip">AI ${fmt.pct(r.ai_confidence * 100)}</span>
          ${r.pii_types.length ? `<span class="chip">🔒 ${r.pii_types.join(', ')} redacted</span>` : ''}
        </div>
        <p class="xs sec" style="margin:0 0 8px"><b>Routed to:</b>
          ${r.district_name}, ${r.region_name}</p>
        <p class="xs muted" style="margin:0">${escapeHtml(r.ai_rationale)}</p>
        ${held ? '<p class="xs sec" style="margin:9px 0 0">This request will not influence a '
               + 'funding recommendation until a person confirms it. That is by design.</p>' : ''}
      </div>
    </div></div>`;
  box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function flash(msg, bad) {
  const box = $('result');
  box.hidden = false;
  box.innerHTML = `<div class="card"><div class="card-b"><div class="result"
    style="border-left-color:${bad ? 'var(--status-critical)' : 'var(--status-good)'}">
    ${escapeHtml(msg)}</div></div></div>`;
}

/* -------------------------------------------------------------- WhatsApp */
const waBub = (who, text) => {
  const d = document.createElement('div');
  d.className = `bub ${who}`; d.textContent = text;
  $('wathread').appendChild(d);
  $('wathread').scrollTop = $('wathread').scrollHeight;
};
function waStart() {
  $('wathread').innerHTML = '';
  waBub('them', 'Helpline: Namaste 🙏 Tell us what your community needs. ' +
                'Write in any language — voice notes also work.');
}
async function waSend() {
  const v = $('wainput').value.trim();
  if (!v) return;
  waBub('me', v);
  $('wainput').value = '';
  const r = await submit(v, 'whatsapp');
  if (!r) { waBub('them', 'Helpline: Sorry, something went wrong. Please try again.'); return; }
  waBub('them',
    `Helpline: Thank you. Logged as ${r.id}.\n` +
    `Category: ${r.sector} · Urgency: ${r.urgency}\n` +
    `Area: ${r.district_name}\n` +
    (r.status === 'review'
      ? 'A officer will confirm the details shortly.'
      : 'This has been added to your district\'s development priorities.'));
}

/* ------------------------------------------------------------- SMS / IVR */
let smsStep = 0;
const smsLine = (t) => { $('smsout').textContent += t + '\n'; };
function smsStart() {
  smsStep = 0;
  $('smsout').textContent = '';
  smsLine('> DEVELOPMENT REQUEST SMS GATEWAY  (short code 1800)');
  smsLine('');
  smsLine('IN : Reply with your request in any language.');
  smsLine('     Example: PANI NAHI AA RAHA 3 MAHINE SE');
}
async function smsSend() {
  const v = $('smsinput').value.trim();
  if (!v) return;
  $('smsinput').value = '';
  smsLine('');
  smsLine('OUT: ' + v);
  if (smsStep === 0) {
    const r = await submit(v, 'sms');
    if (!r) { smsLine('IN : ERROR - please resend.'); return; }
    smsLine('');
    smsLine(`IN : LOGGED ${r.id}`);
    smsLine(`     CATEGORY ${r.sector.toUpperCase()} / ${r.urgency.toUpperCase()}`);
    smsLine(`     AREA ${r.district_name.toUpperCase()}`);
    smsLine(`     ${r.status === 'review' ? 'PENDING OFFICER REVIEW' : 'ADDED TO DISTRICT PRIORITIES'}`);
    smsLine('');
    smsLine('IN : Reply 1 to add detail, or send a new request.');
    smsStep = 1;
  } else {
    smsLine('');
    smsLine('IN : Noted. Send a new request any time.');
    smsStep = 0;
  }
}

/* --------------------------------------------------------------- recent */
async function loadRecent() {
  // The public feed, not /api/requests — that one is staff-only now. This is a
  // narrow projection: no ids, no reviewer notes, no AI internals.
  let d;
  try {
    d = await api(`/api/requests/public?country=${state.country}&limit=14`);
  } catch {
    $('recent').innerHTML = '<div class="empty">Could not load recent requests.</div>';
    return;
  }
  if (!d.enabled) {
    $('recentsub').textContent = '';
    $('recent').innerHTML =
      '<div class="empty">The public feed is switched off in this deployment.</div>';
    return;
  }
  $('recentsub').textContent = `${d.count} newest`;
  $('recent').innerHTML = d.items.map(r => `
    <div style="padding:9px 4px;border-bottom:1px solid var(--border)">
      <div class="row xs" style="gap:6px;margin-bottom:4px">
        ${urgencyChip(r.urgency)}
        <span class="chip">${r.language.toUpperCase()}</span>
        <span class="chip">${r.channel.replace('_', ' ')}</span>
        ${r.district_name ? `<span class="chip">${escapeHtml(r.district_name)}</span>` : ''}
      </div>
      <div class="small">${escapeHtml(r.text.slice(0, 110))}</div>
      ${r.translated && r.text_en
        ? `<div class="xs muted" style="margin-top:3px">${escapeHtml(r.text_en.slice(0, 110))}</div>`
        : ''}
    </div>`).join('') || '<div class="empty">No requests yet.</div>';
}

const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

boot().catch(e => flash('Could not load: ' + e.message, true));
