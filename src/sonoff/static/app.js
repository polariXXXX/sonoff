let token = '', latest = null, receivedAt = 0, stateAvailable = false;
const $ = s => document.querySelector(s);
function notify(message) { $('#notice').textContent = message; }
async function api(path, body, method = 'POST') {
  const response = await fetch(path, {method, credentials:'same-origin', headers:{'Content-Type':'application/json','X-Control-Token':token}, body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Перевірте введені параметри');
  return data;
}
function el(tag, text, className) { const node = document.createElement(tag); node.textContent = text; if (className) node.className=className; return node; }
function card(s) {
  const node = $('#socket-template').content.firstElementChild.cloneNode(true);
  node.dataset.id = s.device_id;
  node.querySelector('h3').textContent = s.name;
  const form = node.querySelector('form');
  form.elements.name.value = s.name;
  for (const [prefix,rule] of [['present',s.mains_present],['absent',s.mains_absent]]) {
    form.elements[prefix+'_action'].value = rule.action;
    form.elements[prefix+'_delay'].value = rule.delay_minutes;
  }
  node.querySelectorAll('[data-command]').forEach(button => button.onclick = async () => {
    try { await api(`/api/sockets/${s.device_id}/control`, {action:button.dataset.command}); notify('Команду прийнято'); await refresh(); } catch(error) {notify(error.message);}
  });
  form.onsubmit = async event => {
    event.preventDefault();
    const current = latest.sockets.find(x=>x.device_id===s.device_id);
    const config = {device_id:s.device_id,name:form.elements.name.value,channel:s.channel,mode:current.mode,
      mains_present:{action:form.elements.present_action.value,delay_minutes:Number(form.elements.present_delay.value)},
      mains_absent:{action:form.elements.absent_action.value,delay_minutes:Number(form.elements.absent_delay.value)}};
    try {await api(`/api/sockets/${s.device_id}`,config,'PUT');notify('Правила збережено; таймер перезапущено');await refresh();} catch(error) {notify(error.message);}
  };
  return node;
}
function renderCountdowns() {
  if (!latest) return;
  const elapsed = (performance.now() - receivedAt) / 1000;
  for (const s of latest.sockets) {
    const node = [...$('#sockets').children].find(x => x.dataset.id === s.device_id);
    if (!node) continue;
    if (!stateAvailable) {
      node.querySelector('.turn-on-time').textContent = '—';
      node.querySelector('.turn-on-note').textContent = 'Немає зв’язку із сервісом';
      continue;
    }
    const seconds = s.confirmed === 'on' ? 0 : Math.max(0, Math.ceil(s.turn_on_remaining_seconds - elapsed));
    node.querySelector('.turn-on-time').textContent = String(Math.floor(seconds / 60)).padStart(2, '0') + ':' + String(seconds % 60).padStart(2, '0');
    node.querySelector('.turn-on-note').textContent = s.confirmed === 'on' ? 'Пристрій уже увімкнений' : s.turn_on_scheduled ? (seconds > 0 ? 'Очікуємо стабільне живлення' : 'Очікуємо виконання команди') : 'Увімкнення не заплановане';
    const remaining = Math.max(0, Math.ceil((s.remaining_seconds || 0) - elapsed));
    node.querySelector('.timer').textContent = s.desired === 'off' && s.remaining_seconds !== null ? (remaining > 0 ? 'Вимкнення через ' + Math.floor(remaining / 60) + ':' + String(remaining % 60).padStart(2, '0') : 'Вимкнення очікує виконання') : '';
  }
}
async function refresh() {
  try {
    const response = await fetch('/api/state'); if (!response.ok) throw new Error('Потрібна авторизація');
    latest = await response.json(); receivedAt = performance.now(); stateAvailable = true; token=latest.control_token;
    $('#mode').textContent=latest.simulation?'СИМУЛЯЦІЯ':'LIVE';
    $('#mains').textContent=latest.mains===true?'Живлення стабільне':latest.mains===false?'Живлення відсутнє':'Стан невідомий';
    $('#mains').className=latest.mains===true?'good':'bad';
    $('#sensor').textContent=latest.sensor_error || (latest.sensor_simulation?'Віртуальний датчик':'Цифровий датчик · LOW = мережа є');
    $('#cloud').textContent=latest.cloud_connected?'Підключено':'Немає зв’язку';
    $('#simulation').hidden=!latest.sensor_simulation;
    $('#authorize').hidden=latest.simulation;
    if(latest.cloud_error) notify(latest.cloud_error);
    const container=$('#sockets');
    for(const s of latest.sockets) {
      let node=[...container.children].find(x=>x.dataset.id===s.device_id);
      if(!node){node=card(s);container.append(node);}
      const status=node.querySelector('.status');status.textContent=s.confirmed==='on'?'УВІМКНЕНО':s.confirmed==='off'?'ВИМКНЕНО':'НЕВІДОМО';
      status.className='badge status '+(s.confirmed==='on'?'good':'');
      node.querySelector('.connection').textContent=`${s.mode==='auto'?'Автоматичний режим':'Ручний режим'} · ${s.online===true?'онлайн':s.online===false?'офлайн':'зв’язок невідомий'} · ${s.device_id}${s.updated_at?' · '+new Date(s.updated_at*1000).toLocaleTimeString('uk-UA'):''}`;
      node.querySelector('.socket-error').textContent=s.error||'';
      node.querySelector('[data-command=on]').disabled=latest.mains!==true;
    }
    renderCountdowns();
    $('#events').replaceChildren(...latest.events.map(e=>{const row=el('div','','event');row.append(el('time',new Date(e.ts*1000).toLocaleString('uk-UA')),el('span',e.message));return row;}));
  } catch(error) {stateAvailable = false; renderCountdowns(); notify('Зв’язок із сервісом втрачено: '+error.message); $('#mains').textContent='Стан недоступний';document.querySelectorAll('[data-command]').forEach(b=>b.disabled=true);}
}
$('#authorize').onclick=async()=>{try{const data=await api('/api/oauth/start',{});window.location.assign(data.url);}catch(error){notify(error.message);}};
$('#discover').onclick=async()=>{try{
  const data=await api('/api/devices/discover',{}); const container=$('#devices');container.replaceChildren();
  for(const d of data.devices){
    const row=el('div','','device');row.append(el('span',`${d.name} · ${d.device_id} · UIID ${d.uiid??'—'}`));
    const channels=d.params.switches?.map(s=>s.outlet) || ('switch' in d.params?[null]:[]);
    for(const channel of channels){const button=el('button',channel===null?'Додати':'Канал '+channel);button.onclick=async()=>{try{
      await api(`/api/sockets/${d.device_id}`,{device_id:d.device_id,name:d.name,channel,mode:'auto',mains_present:{action:'on',delay_minutes:20},mains_absent:{action:'off',delay_minutes:0}},'PUT');notify('Розетку додано: автоматичний сценарій активний');await refresh();
    }catch(error){notify(error.message);}};row.append(button);}
    if(!channels.length)row.append(el('span','Непідтримуваний тип пристрою'));container.append(row);
  }
}catch(error){notify(error.message);}};
document.querySelectorAll('[data-sim]').forEach(b=>b.onclick=async()=>{try{await api('/api/simulation',{present:JSON.parse(b.dataset.sim)});await refresh();}catch(error){notify(error.message);}});
refresh();setInterval(refresh,1000);setInterval(renderCountdowns,250);
