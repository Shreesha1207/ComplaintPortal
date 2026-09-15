/* Citizen intake — four channels, one pipeline.

   Voice uses the browser's SpeechRecognition where available. That is the
   demo stand-in for XVoice, the production intake layer: the browser API
   covers a few dozen major languages and needs a network round-trip, whereas
   XVoice runs on-device across a far wider set including low-resource
   languages that no browser ships. The contract is identical either way —
   audio in, text plus a language tag out — so everything downstream of this
   file is unchanged when the real engine is swapped in.
*/
import { api, apiPost, fmt, themeToggle, urgencyChip } from './viz.js';

const $ = (id) => document.getElementById(id);
const state = { pack: null, country: 'IN', district: null, lang: '', recognizing: false };

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
  themeToggle($('theme'));
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
  $('wainput').onkeydown = (e) => e.key === 'Enter' && waSend();
  $('smssend').onclick = smsSend;
  $('smsinput').onkeydown = (e) => e.key === 'Enter' && smsSend();
  $('voicetext').oninput = () => {
    const n = $('voicetext').value.trim().length;
    $('voicelen').textContent = n ? `${n} characters` : '';
  };

  setupMic();
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
function setupMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const mic = $('mic');
  if (!SR) {
    $('micstatus').textContent = 'Live speech input is not available in this browser.';
    $('micnote').innerHTML =
      'Type below instead — the pipeline is identical. In production this is handled by ' +
      '<b>XVoice</b>, which runs on-device and covers languages no browser supports.';
    mic.disabled = true; mic.style.opacity = .5; mic.style.cursor = 'not-allowed';
    return;
  }
  $('micnote').innerHTML =
    'Browser speech input — the demo stand-in for <b>XVoice</b>, which adds on-device ' +
    'recognition and far wider language coverage.';

  let rec = null;
  mic.onclick = () => {
    if (state.recognizing) { rec && rec.stop(); return; }
    rec = new SR();
    const chosen = state.lang &&
      state.pack.languages.find(l => l.code === state.lang)?.bcp47;
    rec.lang = chosen || (state.country === 'BR' ? 'pt-BR' : 'en-IN');
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
      $('micstatus').textContent =
        e.error === 'not-allowed' ? 'Microphone permission denied — type below instead.'
                                  : `Speech input error (${e.error}) — type below instead.`;
    };
    rec.onend = () => {
      state.recognizing = false; mic.classList.remove('rec');
      mic.setAttribute('aria-label', 'Start voice recording');
      if (!$('micstatus').textContent.includes('error') &&
          !$('micstatus').textContent.includes('denied')) {
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
  waBub('them', 'AGORA: Namaste 🙏 Tell us what your community needs. ' +
                'Write in any language — voice notes also work.');
}
async function waSend() {
  const v = $('wainput').value.trim();
  if (!v) return;
  waBub('me', v);
  $('wainput').value = '';
  const r = await submit(v, 'whatsapp');
  if (!r) { waBub('them', 'AGORA: Sorry, something went wrong. Please try again.'); return; }
  waBub('them',
    `AGORA: Thank you. Logged as ${r.id}.\n` +
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
  smsLine('> AGORA SMS GATEWAY  (short code 1800)');
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
  const rows = await api(`/api/requests?country=${state.country}&limit=14`);
  $('recentsub').textContent = `${rows.length} newest`;
  $('recent').innerHTML = rows.map(r => `
    <div style="padding:9px 4px;border-bottom:1px solid var(--border)">
      <div class="row xs" style="gap:6px;margin-bottom:4px">
        ${urgencyChip(r.urgency)}
        <span class="chip">${r.language.toUpperCase()}</span>
        <span class="chip">${r.channel.replace('_', ' ')}</span>
        ${r.status === 'review' ? '<span class="flag silent">review</span>' : ''}
      </div>
      <div class="small">${escapeHtml(r.text_redacted.slice(0, 110))}</div>
    </div>`).join('') || '<div class="empty">No requests yet.</div>';
}

const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

boot().catch(e => flash('Could not load: ' + e.message, true));
