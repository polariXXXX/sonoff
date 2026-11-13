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
  node.querySelector('.device-info').open = window.matchMedia('(min-width: 651px)').matches;
  node.querySelector('h3').textContent = s.name;
  node.querySelector('.remove-device').onclick = () => removeDevice(s.device_id);
  node.querySelector('.find-image').onclick = async () => {
    try {await api(`/api/sockets/${s.device_id}/image`, {}); notify('Шукаємо фото за моделлю пристрою'); await refresh();} catch(error) {notify(error.message);}
  };
  node.querySelector('.device-photo').onerror = event => {
    if (!event.target.src.endsWith('/static/models/device.svg')) event.target.src = '/static/models/device.svg';
    node.querySelector('.image-status').textContent = 'Фото не завантажилось; повторіть пошук';
  };
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
    const config = {device_id:s.device_id,name:form.elements.name.value,channel:current.channel,mode:current.mode,
      mains_present:{action:form.elements.present_action.value,delay_minutes:Number(form.elements.present_delay.value)},
      mains_absent:{action:form.elements.absent_action.value,delay_minutes:Number(form.elements.absent_delay.value)}};
    try {await api(`/api/sockets/${s.device_id}`,config,'PUT');notify('Правила збережено; таймер перезапущено');await refresh();} catch(error) {notify(error.message);}
  };
  return node;
}
async function removeDevice(deviceId) {
  try {
    await api(`/api/sockets/${deviceId}`, {}, 'DELETE');
    notify('Пристрій видалено зі списку; таймери скасовано');
    await refresh();
  } catch(error) {notify(error.message);}
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
    if (s.online !== true && !s.turn_on_scheduled) {
      node.querySelector('.turn-on-time').textContent = '—';
      node.querySelector('.turn-on-note').textContent = 'Пристрій недоступний; актуальний стан невідомий';
      node.querySelector('.timer').textContent = '';
      continue;
    }
    const seconds = s.confirmed === 'on' && s.online === true ? 0 : Math.max(0, Math.ceil(s.turn_on_remaining_seconds - elapsed));
    node.querySelector('.turn-on-time').textContent = String(Math.floor(seconds / 60)).padStart(2, '0') + ':' + String(seconds % 60).padStart(2, '0');
    node.querySelector('.turn-on-note').textContent = s.online !== true ? 'Запланована дія очікує доступності пристрою' : s.confirmed === 'on' ? 'Пристрій уже увімкнений' : s.turn_on_scheduled ? (seconds > 0 ? 'Очікуємо стабільне живлення' : 'Очікуємо виконання команди') : 'Увімкнення не заплановане';
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
    $('#sensor').textContent=latest.sensor_error || (latest.mains===null?'Сигнал датчика невідомий · автоматичне увімкнення заблоковане':latest.sensor_simulation?'Віртуальний датчик':'Цифровий датчик · LOW = мережа є');
    $('#cloud').textContent=latest.cloud_connected?'Підключено':'Немає зв’язку';
    $('#simulation').hidden=!latest.sensor_simulation;
    if(latest.cloud_error) notify(latest.cloud_error);
    const tg = latest.telegram;
    $('#telegram-status').classList.toggle('integration-disabled', !tg?.enabled);
    $('#telegram-status').textContent = tg?.enabled ? `Telegram увімкнено · у черзі ${tg.pending} · недоставлених ${tg.failed}${tg.error ? ' · ' + tg.error : ''}` : 'Telegram не налаштований';
    const container=$('#sockets');
    for (const node of [...container.children]) {
      if (!latest.sockets.some(s => s.device_id === node.dataset.id)) node.remove();
    }
    document.querySelectorAll('#devices [data-remove-id]').forEach(button => {
      button.disabled = !latest.sockets.some(s => s.device_id === button.dataset.removeId);
    });
    for(const s of latest.sockets) {
      let node=[...container.children].find(x=>x.dataset.id===s.device_id);
      if(!node){node=card(s);container.append(node);}
      const image = node.querySelector('.device-photo');
      if (image.getAttribute('src') !== s.image_url) image.src = s.image_url;
      node.querySelector('.image-status').textContent = ({searching:'Шукаємо фото…', pending:'Очікуємо модель пристрою', not_found:'Фото моделі не знайдено', error:s.image_error || 'Пошук фото тимчасово недоступний', found:''})[s.image_status] || '';
      node.querySelector('.find-image').textContent = s.image_status === 'searching' ? 'Пошук…' : s.image_is_generic ? 'Знайти фото' : 'Оновити фото';
      node.querySelector('.find-image').disabled = !s.product_model || s.image_status === 'searching';
      image.alt = s.image_is_generic ? 'Типове зображення пристрою' : `${s.brand || 'SONOFF'} ${s.product_model || ''}`;
      node.querySelector('.device-model').textContent = [s.brand, s.product_model].filter(Boolean).join(' · ') || 'Модель ще не отримано';
      node.querySelector('.device-details').textContent = [s.firmware ? 'Прошивка ' + s.firmware : '', s.uiid != null ? 'UIID ' + s.uiid : '', s.image_is_generic ? 'Типове зображення' : 'Фото моделі'].filter(Boolean).join(' · ');
      const metrics = [];
      for (const [label,value,unit] of [['Температура',s.temperature,s.temperature_unit || '°C'],['Вологість',s.humidity,'%'],['Wi-Fi',s.rssi,'dBm']]) {
        if (value !== null && value !== undefined && value !== '') { const metric = el('div','','metric'); metric.append(el('span',label),el('strong',`${value} ${unit}`)); metrics.push(metric); }
      }
      node.querySelector('.telemetry').replaceChildren(...metrics);
      const status=node.querySelector('.status');
      status.textContent=s.online===false?'ОФЛАЙН':s.online!==true?'НЕМАЄ ЗВ’ЯЗКУ':s.confirmed==='on'?'УВІМКНЕНО':s.confirmed==='off'?'ВИМКНЕНО':'НЕВІДОМО';
      status.className='badge status '+(s.online!==true?'bad':s.confirmed==='on'?'good':'');
      node.classList.toggle('unavailable',s.online!==true);
      const lastState = s.confirmed==='on'?'увімкнений':s.confirmed==='off'?'вимкнений':'невідомий';
      node.querySelector('.availability-info').textContent=s.online===true?'':`${s.online===false?'Пристрій офлайн у eWeLink':latest.cloud_connected?'Пристрій відсутній у списку порталу або ще не синхронізований':'Зв’язок із eWeLink недоступний'}. Останній відомий стан: ${lastState}. Показники можуть бути застарілими.`;
      const automatic = s.mode === 'auto';
      const modeIndicator = node.querySelector('.mode-indicator');
      modeIndicator.textContent = automatic ? 'Автоматичний' : 'Ручний';
      modeIndicator.className = 'mode-indicator ' + (automatic ? 'mode-auto' : 'mode-manual');
      modeIndicator.title = automatic ? 'Датчик керує за налаштованими правилами' : 'Датчик не керує; поверніться в автоматичний режим для застосування правил';
      node.querySelector('.connection').textContent=`${s.online===true?'онлайн':s.online===false?'офлайн':'зв’язок невідомий'}`;
      node.querySelector('.device-identifiers').textContent=`ID: ${s.device_id}${s.updated_at?' · Оновлено '+new Date(s.updated_at*1000).toLocaleString('uk-UA'):''}`;
      node.querySelector('.socket-error').textContent=s.error||'';
      const onReason = s.online !== true ? 'Керування недоступне: розетка не має підтвердженого зв’язку з eWeLink.' : '';
      node.querySelector('.control-hint').textContent = onReason;
      const onButton = node.querySelector('[data-command=on]');
      onButton.disabled = Boolean(onReason);
      onButton.title = onReason || 'Увімкнути розетку вручну';
      const offButton = node.querySelector('[data-command=off]');
      offButton.disabled = s.online !== true;
      offButton.title = s.online !== true ? 'Розетка недоступна в eWeLink' : 'Вимкнути розетку вручну';
      const autoButton = node.querySelector('[data-command=auto]');
      autoButton.disabled = false;
      autoButton.setAttribute('aria-pressed', String(automatic));
      autoButton.classList.toggle('active-mode', automatic);
    }
    renderCountdowns();
    $('#events').replaceChildren(...latest.events.map(e=>{const row=el('div','','event');row.append(el('time',new Date(e.ts*1000).toLocaleString('uk-UA')),el('span',e.message));return row;}));
  } catch(error) {stateAvailable = false; renderCountdowns(); notify('Зв’язок із сервісом втрачено: '+error.message); $('#mains').textContent='Стан недоступний';document.querySelectorAll('[data-command]').forEach(b=>b.disabled=true);}
}
function setDeviceListVisible(visible) {
  $('#devices').hidden = !visible;
  $('#toggle-devices').hidden = false;
  $('#toggle-devices').textContent = visible ? 'Сховати список' : 'Розгорнути список';
  $('#toggle-devices').setAttribute('aria-expanded', String(visible));
}
$('#toggle-devices').onclick = () => setDeviceListVisible($('#devices').hidden);
$('#discover').onclick=async()=>{try{
  const data=await api('/api/devices/discover',{}); const container=$('#devices');container.replaceChildren();setDeviceListVisible(true);
  if (!data.devices.length) container.append(el('p','Пристроїв не знайдено'));
  for(const d of data.devices){
    const row=el('div','','device');row.append(el('span',`${d.name} · ${d.device_id} · UIID ${d.uiid??'—'}`));
    const channels=d.params.switches?.map(s=>s.outlet) || ('switch' in d.params?[null]:[]);
    for(const channel of channels){const button=el('button',channel===null?'Додати':'Канал '+channel);button.onclick=async()=>{try{
      await api(`/api/sockets/${d.device_id}`,{device_id:d.device_id,name:d.name,channel,mode:'auto',mains_present:{action:'on',delay_minutes:20},mains_absent:{action:'off',delay_minutes:0}},'PUT');notify('Розетку додано: автоматичний сценарій активний');await refresh();
    }catch(error){notify(error.message);}};row.append(button);}
    const remove = el('button','Видалити','remove-device');
    remove.dataset.removeId = d.device_id;
    remove.disabled = !latest.sockets.some(s => s.device_id === d.device_id);
    remove.onclick = () => removeDevice(d.device_id);
    row.append(remove);
    if(!channels.length)row.append(el('span','Непідтримуваний тип пристрою'));container.append(row);
  }
}catch(error){notify(error.message);}};
document.querySelectorAll('[data-sim]').forEach(b=>b.onclick=async()=>{try{await api('/api/simulation',{present:JSON.parse(b.dataset.sim)});await refresh();}catch(error){notify(error.message);}});
const desktopLayout = window.matchMedia('(min-width: 651px)');
function applyLayout() {
  $('#event-log').open = desktopLayout.matches;
  document.querySelectorAll('.device-info').forEach(details => {details.open = desktopLayout.matches;});
}
desktopLayout.addEventListener('change', applyLayout);
applyLayout();
refresh();setInterval(refresh,1000);setInterval(renderCountdowns,250);
