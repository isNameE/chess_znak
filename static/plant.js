'use strict';

const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = n => new Intl.NumberFormat('ru-RU').format(n || 0);
const today = () => new Date().toLocaleDateString('en-CA',{timeZone:'Europe/Moscow'});
const when = value => value ? new Date(value).toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Moscow'}) : '—';
const lineName = id => id === 'LINE-2' ? 'Линия 2' : 'Линия 1';
const productName = id => id === '5L' ? 'Масло растительное · 5 л' : 'Масло растительное · 1 л';
const titles = {overview:'Обзор производства',orders:'Задания на агрегацию',station:'Рабочее место',registry:'Прослеживаемость',printing:'Печать этикеток',exchange:'Обмен с ГИС МТ',incidents:'Инциденты',reports:'Отчёты и аналитика',journal:'Журнал действий',settings:'Настройки и True API',shifts:'Рабочие смены'};
const aliases = {operator:'station',pallet:'station',queue:'exchange',integration:'settings'};
const app = {user:null,summary:null,station:null,catalog:null,integration:null,rows:[],next:null,cursor:null,history:[],filters:{},busy:false,pending:null,detail:null,challenge:null,day:today()};
let toastTimer;
const page = () => { const p=location.hash.slice(1); return aliases[p] || (titles[p] ? p : 'overview'); };
const management = () => ['supervisor','admin'].includes(app.user?.role);

function btn(label,action,id='',style='secondary small',disabled=false) {
  return `<button type="button" class="button ${style}" data-action="${action}" data-id="${esc(id)}" ${disabled?'disabled':''}>${label}</button>`;
}
function badge(value) {
  const labels = {planned:['Запланировано','neutral'],running:['В работе',''],paused:['Пауза','warning'],completed:['Завершено','blue'],cancelled:['Отменено','neutral'],open:['Открыто','warning'],closed:['Сформировано',''],quarantined:['Карантин','error'],disassembled:['Разобрано','neutral'],queued:['Ожидает','warning'],accepted:['Имитатор: принято',''],rejected:['Отклонено','error'],unknown:['Уточнить результат','warning'],ready:['К печати','neutral'],preview_opened:['Ожидает контроля','warning'],verified:['Контроль пройден',''],failed:['Сбой печати','error'],acknowledged:['В работе','blue'],resolved:['Устранено',''],info:['Действие','neutral'],warning:['Предупреждение','warning'],error:['Ошибка','error']};
  const pair=labels[value]||[value||'—','neutral'];
  return `<span class="pill ${pair[1]}">${esc(pair[0])}</span>`;
}
function empty(text,icon='▧') { return `<div class="empty"><span>${icon}</span>${text}</div>`; }
function heading(eyebrow,title,subtitle,actions='') {
  return `<div class="page-head"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p class="subtitle">${subtitle}</p></div><div class="head-actions">${actions}</div></div>`;
}
function toast(message,error=false) {
  clearTimeout(toastTimer); $('#toast').textContent=message; $('#toast').className='visible'+(error?' error':'');
  toastTimer=setTimeout(()=>$('#toast').className='',6500);
}
async function api(url,body) {
  const response=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result=await response.json();
  if(!response.ok) {
    if(response.status===401&&url.startsWith('/api/plant')) {app.user=null;renderLogin();}
    const error=new Error(typeof result.detail==='string'?result.detail:result.error||'Не удалось выполнить запрос. Проверьте данные.');
    error.status=response.status; error.result=result; throw error;
  }
  return result;
}
async function run(action,data={}) {
  if(app.busy) return null;
  app.busy=true;
  const fingerprint=JSON.stringify([action,data]);
  if(!app.pending || app.pending.fingerprint!==fingerprint) app.pending={fingerprint,body:{requestId:crypto.randomUUID(),revision:app.station.revision,data}};
  const disabledBefore=[...document.querySelectorAll('main button,dialog button[type="submit"]')].map(b=>[b,b.disabled]);
  disabledBefore.forEach(([b])=>b.disabled=true);
  toast('Сохраняем операцию…');
  try {
    const result=await api('/api/plant/action/'+action,app.pending.body);
    app.pending=null; app.station=result.station;
    await loadPage();
    toast(result.result.processed!==undefined?`Обработано документов: ${result.result.processed}. Результаты учебные.`:'Действие сохранено');
    return result.result;
  } catch(error) {
    if(error.result) app.pending=null;
    if(error.result?.station) app.station=error.result.station;
    try { await loadPage(); } catch(_) { /* Keep the last valid view while the server is unreachable. */ }
    toast(error.message||'Нет связи с сервером. Повтор сохранит идентификатор запроса.',true);
    return null;
  } finally {
    app.busy=false;
    disabledBefore.forEach(([b,disabled])=>{if(b.isConnected)b.disabled=disabled;});
  }
}
function modal(html) {
  $('#modal-body').innerHTML=html;
  if(!$('#modal').open) $('#modal').showModal();
}
function closeModal() { $('#modal').close(); }
function resetPagination() { app.cursor=null;app.next=null;app.history=[]; }
function filters() { return app.filters[page()] || {}; }

async function initialize() {
  try {
    app.user=(await api('/api/access/me')).user;
    if(!app.user) { renderLogin(); return; }
    [app.catalog,app.integration]=await Promise.all([api('/api/plant/catalog'),api('/api/integration/status')]);
    await loadPage();
  } catch(error) {
    $('#main').innerHTML=heading('ПОДКЛЮЧЕНИЕ','Сервер недоступен','Проверьте, что запущены приложение и PostgreSQL.')+`<div class="hint warning">${esc(error.message)}</div>`;
  }
}
async function loadPage() {
  const current=page();
  const views={orders:'orders',registry:'aggregates',printing:'printing',exchange:'exchange',incidents:'incidents',journal:'audit',shifts:'shifts'};
  const requests=[api('/api/plant/overview?day='+app.day)];
  if(views[current]) {
    const params=new URLSearchParams({...filters(),limit:'25'});
    if(app.cursor) params.set('cursor',app.cursor);
    requests.push(api('/api/plant/list/'+views[current]+'?'+params));
  } else if(current==='reports') {
    const f=filters();const last=f.last||today();const first=f.first||last.slice(0,8)+'01';
    requests.push(api('/api/plant/reports?'+new URLSearchParams({first,last})));
  }
  const [summary,result]=await Promise.all(requests);
  if(current!==page()) return;
  app.summary=summary;app.station=summary.station;
  if(views[current]) {app.rows=result.items;app.next=result.next;}
  if(current==='reports') app.report=result;
  render();
}
function renderHeader() {
  $('#page-title').textContent=titles[page()];
  document.querySelectorAll('nav a').forEach(a=>a.classList.toggle('active',a.dataset.page===page()));
  $('#user-name').textContent=app.user.display_name;
  $('#user-role').textContent=app.user.mode==='demo'?'Локальный демо доступ':({operator:'Оператор',supervisor:'Мастер смены',admin:'Администратор'}[app.user.role]);
  const q=app.summary?.queues||{};
  $('#print-count').textContent=q.printing||'';$('#exchange-count').textContent=q.exchange||'';$('#incident-count').textContent=q.incidents||'';
  $('#data-time').textContent='Обновлено '+new Date().toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit',timeZone:'Europe/Moscow'});
}
function render() {
  renderHeader();
  const pages={overview,orders,station,registry,printing,exchange,incidents,reports,journal,settings,shifts};
  $('#main').innerHTML=pages[page()]();
  if(page()==='overview') {
    const c=app.catalog.capacity,rate=c.target_pallets*app.catalog.palletSize/c.shift_hours;
    $('#main .side-panels').insertAdjacentHTML('beforeend',`<div class="hint warning"><strong>Расчёт темпа общего стола</strong>${c.target_pallets} паллет за ${c.shift_hours} ч → ${rate.toFixed(1).replace('.',',')} кор./ч.<br>На полный цикл короба — ${(3600/rate).toFixed(1).replace('.',',')} с без перерывов. Этот темп нужно подтвердить на оборудовании.</div>`);
  }
  if(page()==='settings') {
    $('#recipe-form .form-grid').insertAdjacentHTML('beforeend',`<label><span>Рабочих часов в смене</span><input type="number" name="shiftHours" min="1" max="24" value="${app.catalog.capacity.shift_hours}" required ${!management()?'disabled':''}></label><label><span>Расчётный ориентир, паллет за смену</span><input type="number" name="targetPallets" min="1" max="100" value="${app.catalog.capacity.target_pallets}" required ${!management()?'disabled':''}></label>`);
  }
}
function shiftBar() {
  const shift=app.station.shift;
  return `<div class="shift-bar"><i class="live-dot"></i><strong>${shift?esc(shift.name):'Рабочая смена не открыта'}</strong><span>${shift?'Открыта '+when(shift.opened_at):'Откройте смену, чтобы запускать задания'}</span><a href="#shifts">${shift?'Управление сменой':'Открыть смену'} →</a></div>`;
}
function overview() {
  const s=app.summary,t=s.totals;
  const capacity=app.catalog.capacity,boxesPerHour=capacity.target_pallets*app.catalog.palletSize/capacity.shift_hours;
  const cards=[['Сформировано паллет',number(t.pallets),`Ориентир: ${capacity.target_pallets} за ${capacity.shift_hours} часов`,'▤'],['Закрыто коробов',number(t.boxes),'Факт по двум линиям','◇'],['Бутылок в коробах',number(t.bottles),'По сохранённому составу','▥'],['Открытых инцидентов',number(s.queues.incidents),'Требуют внимания','△']];
  const chartDays=[];
  for(let n=6;n>=0;n--) {const d=new Date(s.day+'T12:00:00+03:00');d.setUTCDate(d.getUTCDate()-n);const key=d.toISOString().slice(0,10);chartDays.push({day:key,value:Number(s.daily.find(x=>x.day===key)?.pallets||0)});}
  const highest=Math.max(5,...chartDays.map(x=>x.value));
  return heading('ПРОИЗВОДСТВЕННЫЙ КОНТУР','Обзор производства','Общий стол агрегации · две линии розлива · факт за выбранный день',`<input aria-label="День обзора" type="date" id="overview-day" value="${app.day}" class="compact-date">${management()?btn('+ Создать задание','new-order','',''):''}`)+
    `<div class="kpis">${cards.map(c=>`<div class="kpi"><div class="kpi-caption">${c[0]}<span>${c[3]}</span></div><div class="kpi-value">${c[1]}</div><div class="kpi-note">${c[2]}</div></div>`).join('')}</div>`+shiftBar()+
    `<div class="grid-main"><div><div class="grid-two">${s.lines.map((line,i)=>{
      const order=s.orders.find(o=>o.line_id===line.id&&['running','paused'].includes(o.state))||s.orders.find(o=>o.line_id===line.id);
      const percent=order?Math.min(100,order.good_boxes/order.target_boxes*100):0;
      return `<section class="panel line-card"><div class="line-title"><div class="line-name"><span class="line-icon">▥</span>${esc(line.name)}</div>${order?badge(order.state):'<span class="pill neutral">Нет активного задания</span>'}</div><div class="line-numbers"><div><strong>${number(line.boxes)}</strong><small>коробов за день</small></div><div><strong>${number(line.bottles)}</strong><small>бутылок</small></div><div><strong>${number(line.pallets)}</strong><small>паллет</small></div></div><div class="progress ${i?'blue':''}"><span style="width:${percent}%"></span></div><div class="progress-meta"><span>${order?'План текущего задания':'План задаётся в задании'}</span><span>${order?number(order.good_boxes)+' / '+number(order.target_boxes)+' кор.':'—'}</span></div><div class="line-task"><span>${order?esc(order.id):'Линия готова к выбору задания'}</span><span>${order?'Партия '+esc(order.batch):'Работа через общий стол'}</span></div></section>`;
    }).join('')}</div><section class="panel"><div class="panel-head"><div><h2>Задания на агрегацию</h2><p>План и факт по текущим производственным заданиям</p></div><a class="button link" href="#orders">Все задания →</a></div>${s.orders.length?orderTable(s.orders):empty('Создайте задание с линией, партией и планом.<br>Оператор получит готовый рецепт сборки.','▤')}</section><section class="panel"><div class="panel-head"><h2>Факт формирования паллет за 7 дней</h2><span class="pill neutral">Сохранённые данные</span></div><div class="panel-body"><div class="spark-chart">${chartDays.map(d=>`<div class="spark-day"><span class="spark-value">${d.value}</span><div class="spark-bar" style="height:${d.value/highest*85}%"></div></div>`).join('')}</div><div class="spark-labels">${chartDays.map(d=>`<span>${d.day.slice(8)}.${d.day.slice(5,7)}</span>`).join('')}</div></div></section></div><aside class="side-panels"><section class="panel"><div class="panel-head"><h2>Состояние контуров</h2><span class="pill neutral">Общий стол</span></div><div class="panel-body">${deviceRow('▦','Групповое чтение','Камера агрегационного стола','Имитатор','warning')}${deviceRow('▱','Печать этикеток',s.queues.printing+' заданий ожидают контроля','Демо / PDF','neutral')}${deviceRow('⇄','Обмен с ГИС МТ','Доступ к True API настраивается отдельно',app.integration.hasToken?'Sandbox':app.integration.enabled?'Нужна подпись':'Не настроен','warning')}${deviceRow('▧','Хранилище данных','Собственная PostgreSQL','Доступно','')}</div></section><section class="panel"><div class="panel-head"><h2>Требует внимания</h2><a class="button link" href="#incidents">Все →</a></div><div class="panel-body">${s.incidents.length?s.incidents.map(i=>`<div class="incident-item"><i class="incident-dot"></i><div><strong>${esc(i.message)}</strong><small>${when(i.created_at)} · ${esc(i.reference||'Общий стол')}</small></div></div>`).join(''):empty('Открытых инцидентов нет','✓')}</div></section><div class="hint"><strong>От коробки до задания</strong>Линия, партия, смена и вложенные коды сохраняются вместе с упаковкой. Поиск находит паллету по коду бутылки.</div></aside></div>`;
}
function deviceRow(icon,title,description,status,style) {return `<div class="equipment-row"><span class="device-icon">${icon}</span><div><strong>${title}</strong><small>${description}</small></div><span class="pill ${style}">${status}</span></div>`;}
function orderTable(rows) {
  return `<div class="table-wrap"><table><thead><tr><th>ЗАДАНИЕ / ПАРТИЯ</th><th>ЛИНИЯ И ПРОДУКЦИЯ</th><th>ПЛАН / ФАКТ, КОРОБА</th><th>СТАТУС</th><th></th></tr></thead><tbody>${rows.map(o=>`<tr><td><strong class="mono">${esc(o.id)}</strong><small>Партия ${esc(o.batch)}</small></td><td>${lineName(o.line_id)}<small>${productName(o.product_id)}</small></td><td><strong>${number(o.good_boxes)}</strong> / ${number(o.target_boxes)}<div class="progress table-progress"><span style="width:${Math.min(100,o.good_boxes/o.target_boxes*100)}%"></span></div></td><td>${badge(o.state)}</td><td><div class="table-actions">${['planned','paused'].includes(o.state)&&management()?btn('Запустить →','order-start',o.id,'ghost small',!app.station.shift||!!app.station.order&&app.station.order.state==='running'):o.state==='running'?'<a class="button ghost small" href="#station">К столу →</a>':''}${management()&&!['completed','cancelled'].includes(o.state)?btn('План','order-plan',o.id):''}${management()&&o.state==='planned'?btn('Отменить','order-cancel',o.id):''}</div></td></tr>`).join('')}</tbody></table></div>`;
}
function pagination() {
  return `<div class="pagination"><span>На странице: ${app.rows.length} · загрузка по 25 записей</span><div class="actions">${btn('← Назад','previous','','secondary small',!app.history.length)}${btn('Далее →','next','','secondary small',!app.next)}</div></div>`;
}
function selectField(name,placeholder,options) {
  const value=filters()[name]||'';
  return `<select name="${name}" aria-label="${placeholder}"><option value="">${placeholder}</option>${options.map(([v,t])=>`<option value="${v}" ${value===v?'selected':''}>${t}</option>`).join('')}</select>`;
}
function listFilter(extra='',search=false) {
  return `<form class="filters" id="filter-form">${search?`<input class="search" name="q" value="${esc(filters().q||'')}" placeholder="${page()==='registry'?'Полный код бутылки или начало идентификатора упаковки':'Идентификатор задания или точный номер партии'}" aria-label="Поиск">`:''}${extra}<button class="button secondary">Применить</button>${btn('Сбросить','clear-filters')}</form>`;
}
function orders() {
  return heading('ПЛАНИРОВАНИЕ','Задания на агрегацию','Каждое задание фиксирует линию, партию, рецепт и план выпуска.',management()?btn('+ Создать задание','new-order','',''):'')+
    listFilter(selectField('state','Все статусы',[['planned','Запланировано'],['running','В работе'],['paused','На паузе'],['completed','Завершено'],['cancelled','Отменено']])+selectField('line','Обе линии',[['LINE-1','Линия 1'],['LINE-2','Линия 2']]),true)+
    `<section class="panel">${app.rows.length?orderTable(app.rows):empty('Подходящих заданий нет.<br>Создайте задание или измените фильтры.','▤')}${pagination()}</section>`;
}
function station() {
  const s=app.station,o=s.order;const running=o?.state==='running';const target=o?.recipe_quantity||0;
  const context=o?`<section class="panel order-context"><span class="line-icon">▤</span><div class="order-meta"><span class="mono">${esc(o.id)} · ${lineName(o.line_id)}</span><h2>${productName(o.product_id)}</h2><p>Партия ${esc(o.batch)} · ${o.recipe_quantity} шт. / короб · ${o.pallet_quantity} коробов / паллета</p></div><div class="goal">${number(o.good_boxes)} <small>/ ${number(o.target_boxes)} кор.</small></div>${badge(o.state)}</section>`:
    `<div class="hint warning"><strong>Выберите производственное задание</strong>Мастер открывает смену и запускает задание. Линия, партия и рецепт автоматически передаются на общий стол. <a href="#orders">Перейти к заданиям →</a></div>`;
  return heading('ОБЩИЙ СТОЛ АГРЕГАЦИИ','Рабочее место оператора','Групповое чтение → короб → контроль этикетки → паллета.',o?`<div class="actions">${o.state==='running'?btn('Ⅱ Пауза','order-pause'):btn('▶ Продолжить','order-resume','','ghost')}${management()?btn('Завершить задание','order-complete'):''}</div>`:'')+shiftBar()+context+
    `<div class="station-grid"><div><section class="panel"><div class="panel-head"><h2>Сборка короба</h2><span class="pill ${s.draft.length===target&&target?'':'neutral'}">${target&&s.draft.length===target?'Состав полный':'Групповое считывание'}</span></div><div class="panel-body"><div class="scanner-stage"><div class="scanner-corners"><span>КАМЕРА СТОЛА</span><span>УЧЕБНЫЙ КАДР</span></div><div class="code-slots">${Array.from({length:target||15},(_,i)=>`<span class="code-slot ${i<s.draft.length?'filled':''}">${i<s.draft.length?'✓':String(i+1).padStart(2,'0')}</span>`).join('')}</div><div class="scan-footer"><span>Фактически полученные коды</span><strong>${s.draft.length} <small>/ ${target||'—'}</small></strong></div></div><div class="scanner-actions">${btn('▦ Считать учебный кадр','camera','','',!running||s.draft.length===target)}${btn('Закрыть короб →','box-close','','ghost',!running||!target||s.draft.length!==target)}</div><form class="scan-form" id="scan-form"><input name="code" autocomplete="off" placeholder="Ручной ввод DEMO-${o?.product_id||'1L'}-…" aria-label="Код бутылки" required ${!running?'disabled':''}><button class="button secondary" ${!running?'disabled':''}>Считать ↵</button></form><div class="codes-list">${s.draft.map((c,i)=>`<div><span>${String(i+1).padStart(2,'0')} &nbsp; ${esc(c)}</span><span class="positive">✓</span></div>`).join('')}</div><details class="test-scenarios"><summary>Сценарии проверки и сброс</summary><div class="actions">${btn('Неполный кадр','camera-missing','','secondary small',!running)}${btn('Дубль в кадре','camera-duplicate','','secondary small',!running)}${btn('Другой товар','camera-wrong','','secondary small',!running)}${btn('Сбросить состав','draft-clear','','danger small',!s.draft.length)}</div></details></div></section><section class="panel"><div class="panel-head"><h2>Последние короба задания</h2><span class="pill neutral">До 8 записей</span></div>${s.recent.length?`<div class="table-wrap"><table><thead><tr><th>КОРОБ</th><th>СОСТАВ</th><th>ЭТИКЕТКА</th><th></th></tr></thead><tbody>${s.recent.map(b=>`<tr><td><button class="link-id" data-action="details" data-id="${b.id}">${b.id}</button></td><td>${b.unit_count} шт.</td><td>${badge(b.print_status)}</td><td>${btn('Макет / контроль','label',b.id)}</td></tr>`).join('')}</tbody></table></div>`:empty('Здесь появятся короба текущего задания.')}</section></div><div><section class="panel"><div class="panel-head"><h2>Текущая паллета</h2><span class="pill neutral">По конкретным коробам</span></div><div class="panel-body"><div class="pallet-counter"><span>Коробов в составе</span><strong>${s.pallet.length}<small> / ${o?.pallet_quantity||'—'}</small></strong></div><div class="progress"><span style="width:${o?s.pallet.length/o.pallet_quantity*100:0}%"></span></div><form class="scan-form" id="pallet-form"><input name="id" placeholder="DEMO-BOX-…" aria-label="Код короба" required ${!running?'disabled':''}><button class="button secondary" ${!running?'disabled':''}>Добавить</button></form><div class="box-list">${s.pallet.map(id=>`<div class="box-item"><span>${id}</span>${btn('Убрать','pallet-remove',id,'secondary small',!running)}</div>`).join('')}</div><div class="actions" style="margin-top:18px">${btn('Закрыть паллету →','pallet-close','','',!running||s.pallet.length!==o?.pallet_quantity)}</div><p class="form-hint">Добавляются только короба текущего задания с пройденным контролем учебной этикетки.</p></div></section><section class="panel"><div class="panel-head"><h2>Доступные короба</h2><span class="pill neutral">До 50</span></div><div class="panel-body">${s.available.length?`<div class="box-list">${s.available.map(b=>`<div class="box-item"><span>${b.id}</span>${btn('+','pallet-add',b.id,'ghost small',!running||s.pallet.length===o?.pallet_quantity)}</div>`).join('')}</div>`:empty('Сначала закройте короб и проверьте этикетку.','▧')}</div></section>${management()?`<div class="hint"><strong>Массовый учебный сценарий</strong>Генератор создаст недостающие короба с отдельными кодами и сымитирует контроль этикеток. Это не чтение оборудования.<div style="margin-top:12px">${btn('Смоделировать паллету','demo-pallet','','secondary small',!running||s.pallet.length===o?.pallet_quantity)}</div></div>`:''}</div></div>`;
}
function registry() {
  return heading('ПРОСЛЕЖИВАЕМОСТЬ','Реестр упаковок','Поиск на сервере по коробу, паллете или вложенному коду бутылки.')+
    listFilter(selectField('kind','Все упаковки',[['box','Короба'],['pallet','Паллеты']])+selectField('state','Все состояния',[['closed','Сформировано'],['quarantined','Карантин'],['disassembled','Разобрано']])+selectField('line','Обе линии',[['LINE-1','Линия 1'],['LINE-2','Линия 2']]),true)+
    `<section class="panel">${app.rows.length?`<div class="table-wrap"><table><thead><tr><th>УПАКОВКА</th><th>ЛИНИЯ / ПАРТИЯ</th><th>СОСТАВ</th><th>СОСТОЯНИЕ</th><th>ОБМЕН</th><th>СОЗДАНА</th><th></th></tr></thead><tbody>${app.rows.map(a=>`<tr><td><button class="link-id" data-action="details" data-id="${a.id}">${a.id}</button><small>${a.kind==='box'?'Короб':'Паллета'} · ${a.product_id==='1L'?'1 л':'5 л'}</small></td><td>${lineName(a.line_id)}<small>${esc(a.batch)}</small></td><td>${a.unit_count} ${a.kind==='box'?'шт.':'кор.'}</td><td>${badge(a.lifecycle)}</td><td>${badge(a.exchange_status)}</td><td>${when(a.created_at)}</td><td>${btn('Открыть','details',a.id)}</td></tr>`).join('')}</tbody></table></div>`:empty('По вашему запросу упаковки не найдены.')}${pagination()}</section>`;
}
function printing() {
  return heading('КОНТРОЛЬ УПАКОВКИ','Печать и проверка этикеток','Готовность макета и контрольное считывание учитываются отдельно от факта закрытия короба.')+
    listFilter(selectField('state','Все задания',[['ready','К печати'],['preview_opened','Ожидает контроля'],['failed','Сбой'],['verified','Контроль пройден'],['cancelled','Отменено']]))+
    `<section class="panel">${app.rows.length?`<div class="table-wrap"><table><thead><tr><th>УПАКОВКА</th><th>СТАТУС ЭТИКЕТКИ</th><th>ОТКРЫТИЙ МАКЕТА</th><th>СОЗДАНО</th><th>ДЕЙСТВИЯ</th></tr></thead><tbody>${app.rows.map(j=>`<tr><td><button class="link-id" data-action="details" data-id="${j.aggregate_id}">${j.aggregate_id}</button><small>${j.kind==='box'?'Короб':'Паллета'}</small></td><td>${badge(j.status)}</td><td>${j.print_opens}</td><td>${when(j.created_at)}</td><td>${j.status!=='cancelled'?`<div class="table-actions">${btn(j.status==='failed'?'Повторить':'Макет / контроль','label',j.aggregate_id,'ghost small')}${btn('Имитировать сбой','print-fail',j.aggregate_id)}</div>`:'—'}</td></tr>`).join('')}</tbody></table></div>`:empty('Задания печати создаются вместе с упаковкой.')}${pagination()}</section><div class="hint warning"><strong>Принтер ещё не подключён</strong>Макеты текстовые, идентификаторы учебные. Контрольное считывание в демо проверяет совпадение введённого идентификатора; физическое качество печати не измеряется.</div>`;
}
function exchange() {
  return heading('ИНТЕГРАЦИОННЫЙ КОНТУР','Обмен с ГИС МТ','Здесь работает учебная очередь. Доступ к настоящему True API настраивается отдельно.',btn('Обработать до 100 доступных','exchange-run','','',!app.station.online))+
    `<div class="shift-bar"><i class="environment-dot"></i><strong>${app.station.online?'Имитатор связи доступен':'Имитатор связи отключён'}</strong><span>${app.summary.exchangeService?.active?'Фоновый учебный обработчик активен':'Ручная обработка · фоновый сервис не запущен'}</span><span class="right">${btn(app.station.online?'Отключить связь':'Восстановить связь','connection')}</span></div>`+
    listFilter(selectField('state','Все документы',[['queued','В очереди'],['rejected','Отклонено'],['unknown','Неопределённый результат'],['accepted','Принято имитатором'],['cancelled','Отменено']]))+
    `<section class="panel">${app.rows.length?`<div class="table-wrap"><table><thead><tr><th>ДОКУМЕНТ</th><th>РЕЗУЛЬТАТ</th><th>ПОПЫТОК</th><th>СЦЕНАРИЙ ПРОВЕРКИ</th></tr></thead><tbody>${app.rows.map(d=>`<tr><td><strong class="mono">${d.id}</strong><small>${d.aggregate_id}</small></td><td>${badge(d.status)}<small>${esc(d.message)}</small></td><td>${d.attempts}</td><td><div class="table-actions">${['queued','rejected'].includes(d.status)?btn('Принять','send-accepted',d.id,'ghost small',!app.station.online)+btn('Отказать','send-rejected',d.id,'secondary small',!app.station.online)+btn('Тайм-аут','send-unknown',d.id,'secondary small',!app.station.online):d.status==='unknown'?btn('Уточнить результат','exchange-reconcile',d.id,'ghost small',!app.station.online):'<span class="muted">—</span>'}</div></td></tr>`).join('')}</tbody></table></div>`:empty('Документы появятся после формирования упаковок.')}${pagination()}</section><div class="hint"><strong>Проверки перед обработкой</strong>Нужны контроль этикетки и отсутствие карантина. Паллета обрабатывается после вложенных коробов. При тайм-ауте повторная отправка запрещена до уточнения результата.</div>`;
}
function incidents() {
  return heading('КАЧЕСТВО И НЕШТАТНЫЕ СИТУАЦИИ','Инциденты','Ошибка, ответственный, результат устранения — с сохранением истории.')+
    listFilter(selectField('state','Все состояния',[['open','Открыто'],['acknowledged','В работе'],['resolved','Устранено']]))+
    `<section class="panel">${app.rows.length?`<div class="table-wrap"><table><thead><tr><th>СОБЫТИЕ</th><th>ПРИВЯЗКА</th><th>СОСТОЯНИЕ</th><th>ОТВЕТСТВЕННЫЙ</th><th>ДЕЙСТВИЯ</th></tr></thead><tbody>${app.rows.map(i=>`<tr><td><strong>${esc(i.message)}</strong><small>${when(i.created_at)} · ${esc(i.category)}</small>${i.resolution?`<small>Решение: ${esc(i.resolution)}</small>`:''}</td><td class="mono">${esc(i.reference||'Общий стол')}<small>${esc(i.order_id||'Без задания')}</small></td><td>${badge(i.state)}</td><td>${esc(i.assigned_name||'Не назначен')}</td><td><div class="table-actions">${i.state!=='resolved'?btn('Взять в работу','incident-ack',i.id,'ghost small'):''}${i.state!=='resolved'&&management()?btn('Устранено','incident-resolve',i.id):''}</div></td></tr>`).join('')}</tbody></table></div>`:empty('Инцидентов с выбранным состоянием нет.','✓')}${pagination()}</section>`;
}
function reports() {
  const r=app.report;const totals=r.items.reduce((sum,row)=>({boxes:sum.boxes+Number(row.boxes),bottles:sum.bottles+Number(row.bottles),pallets:sum.pallets+Number(row.pallets),held:sum.held+Number(row.held)}),{boxes:0,bottles:0,pallets:0,held:0});
  return heading('ПРОИЗВОДСТВЕННАЯ АНАЛИТИКА','Планирование по фактическому выпуску','Отчёт по датам, линиям и продукции. Период до 31 дня.',`<a class="button secondary" href="/api/plant/reports.csv?${new URLSearchParams({first:r.first,last:r.last})}">Выгрузить CSV ↓</a>`)+
    `<form class="filters" id="report-form"><label>С даты <input type="date" name="first" value="${r.first}" required></label><label>По дату <input type="date" name="last" value="${r.last}" required></label><button class="button secondary">Сформировать</button></form><section class="panel"><div class="report-totals">${[['pallets','Паллет'],['boxes','Коробов'],['bottles','Бутылок'],['held','На карантине']].map(([k,l])=>`<div><strong>${number(totals[k])}</strong><small>${l}</small></div>`).join('')}</div>${r.items.length?`<div class="table-wrap"><table><thead><tr><th>ДАТА</th><th>ЛИНИЯ</th><th>ПРОДУКЦИЯ</th><th>ПАЛЛЕТ</th><th>КОРОБОВ</th><th>БУТЫЛОК</th><th>КАРАНТИН</th></tr></thead><tbody>${r.items.map(x=>`<tr><td>${x.day}</td><td>${lineName(x.line_id)}</td><td>${productName(x.product_id)}</td><td>${number(x.pallets)}</td><td>${number(x.boxes)}</td><td>${number(x.bottles)}</td><td>${number(x.held)}</td></tr>`).join('')}</tbody></table></div>`:empty('В выбранном периоде упаковки не формировались.')}</section><p class="form-hint">${esc(r.basis)} Это показатели агрегационного стола, а не всей линии розлива.</p>`;
}
function journal() {
  return heading('АУДИТ','Журнал действий','Действие, время и учётная запись пользователя. История загружается по страницам.')+
    listFilter(selectField('state','Все события',[['info','Действия'],['warning','Предупреждения'],['error','Ошибки']]))+
    `<section class="panel">${app.rows.length?`<div class="table-wrap"><table><thead><tr><th>ДАТА И ВРЕМЯ</th><th>УРОВЕНЬ</th><th>ДЕЙСТВИЕ</th><th>ПОЛЬЗОВАТЕЛЬ</th></tr></thead><tbody>${app.rows.map(e=>`<tr><td class="mono">${when(e.created_at)}</td><td>${badge(e.level)}</td><td>${esc(e.message)}</td><td>${esc(e.display_name)}<small>${esc(e.actor)}</small></td></tr>`).join('')}</tbody></table></div>`:empty('История пока пуста.')}${pagination()}</section>`;
}
function shifts() {
  return heading('РАБОТА ПО СМЕНАМ','Рабочие смены','Формирование упаковок привязано к смене и учётной записи пользователя.',management()?btn(app.station.shift?'Закрыть смену':'Открыть смену',app.station.shift?'shift-close':'new-shift','',''):'' )+
    shiftBar()+`<section class="panel">${app.rows.length?`<div class="table-wrap"><table><thead><tr><th>СМЕНА</th><th>СОСТОЯНИЕ</th><th>ОТКРЫТА</th><th>ЗАКРЫТА</th><th>ОТКРЫЛ</th></tr></thead><tbody>${app.rows.map(s=>`<tr><td><strong>${esc(s.name)}</strong><small class="mono">${s.id}</small></td><td>${s.state==='open'?'<span class="pill">Открыта</span>':'<span class="pill neutral">Закрыта</span>'}</td><td>${when(s.opened_at)}</td><td>${when(s.closed_at)}</td><td>${esc(s.opened_by)}</td></tr>`).join('')}</tbody></table></div>`:empty('Смены ещё не открывались.','◷')}${pagination()}</section><div class="hint"><strong>Закрытие без потери незавершённого состава</strong>Смена не закрывается, пока остался незавершённый короб или паллета. Задание можно поставить на паузу и продолжить в следующей смене после завершения текущей упаковки.</div>`;
}
function settings() {
  const i=app.integration,c=app.catalog;
  return heading('КОНФИГУРАЦИЯ ПЛОЩАДКИ','Настройки и интеграции','Параметры новых заданий, доступ и состояние внешнего контура.')+
    `<div class="status-grid"><div class="status-card"><span>ХРАНИЛИЩЕ</span><strong>PostgreSQL</strong><p>Индексированные реестры и отдельные таблицы. Интерфейс не загружает полный архив кодов.</p></div><div class="status-card"><span>ДОСТУП</span><strong>${app.user.mode==='demo'?'Локальный демо режим':'Персональная сессия'}</strong><p>${app.user.mode==='demo'?'Административный доступ только с этого компьютера. Для персонального входа отключите ARM_DEMO_MODE.':'Роль проверяется сервером. Сессия действует до 12 часов, пароль хранится в виде хеша.'}</p></div><div class="status-card"><span>TRUE API</span><strong>${i.hasToken?'Авторизован в sandbox':i.enabled?'Ожидает подпись':'Не настроен'}</strong><p>Реальные документы не отправляются учебной очередью. Промышленный контур отключён.</p></div></div>`+
    `<section class="panel"><div class="panel-head"><h2>Рецепты новых заданий</h2><span class="pill neutral">Текущие задания не меняются</span></div><form class="panel-body" id="recipe-form"><div class="form-grid">${[['one','Бутылок 1 л в коробе','1L'],['five','Бутылок 5 л в коробе','5L']].map(([name,label,id])=>`<label><span>${label}</span><input type="number" min="1" max="100" name="${name}" value="${c.products.find(p=>p.id===id).quantity}" required ${!management()?'disabled':''}></label>`).join('')}<label><span>Коробов в паллете</span><input type="number" name="pallet" min="1" max="100" value="${c.palletSize}" required ${!management()?'disabled':''}></label></div><div class="form-foot"><button class="button secondary" ${!management()?'disabled':''}>Сохранить рецепт</button><span class="form-hint">Параметры фиксируются при создании каждого задания.</span></div></form></section>`+
    `<section class="panel"><div class="panel-head"><h2>Подключение тестового True API</h2><span class="pill warning">Sandbox</span></div><div class="panel-body"><p class="subtitle">${esc(i.host)} · токен ${i.hasToken?'получен':'отсутствует'}</p><p class="form-hint">Тестовый кабинет и сертификат настраиваются ИТ-службой. Закрытый ключ и PIN здесь не запрашиваются.</p>${btn('Получить запрос авторизации','auth-challenge','','secondary',!i.enabled||app.user.role!=='admin')}${app.challenge?`<pre>UUID: ${esc(app.challenge.uuid)}\nДанные: ${esc(app.challenge.data)}</pre><form id="auth-form"><label><span>Присоединённая CMS-подпись в base64</span><textarea name="signature" required></textarea></label><div class="form-foot"><button class="button">Авторизоваться</button></div></form>`:''}<div class="divider"></div><form id="true-code-form"><label><span>Настоящие КИ тестового контура, по одному на строке</span><textarea name="codes" required placeholder="Идентификаторы DEMO отправлять запрещено"></textarea></label><div class="form-foot"><button class="button secondary" ${!i.hasToken||app.user.role!=='admin'?'disabled':''}>Запросить сведения</button></div></form><pre id="true-code-result"></pre></div></section><div class="hint warning"><strong>Подготовка к производственному запуску</strong>Ещё нужны испытания камеры и освещения, промышленная печать и идентификаторы упаковок, реальный тестовый кабинет, проверка нагрузки на заводском сервере и программа приёмочных испытаний.</div>`;
}
function orderForm() {
  modal(`<div class="eyebrow">ПЛАНИРОВАНИЕ</div><h2>Новое задание на агрегацию</h2><p class="subtitle">Рецепт и размер паллеты будут зафиксированы в задании.</p><form id="order-form"><div class="form-grid"><label><span>Источник продукции</span><select name="line"><option value="LINE-1">Линия розлива 1</option><option value="LINE-2">Линия розлива 2</option></select></label><label><span>Продукция</span><select name="product">${app.catalog.products.map(p=>`<option value="${p.id}">${p.name} · ${p.quantity} шт./кор.</option>`).join('')}</select></label><label><span>Номер партии</span><input name="batch" maxlength="80" required placeholder="Например, ПАРТИЯ-21-10-А"></label><label><span>План, полных паллет</span><input type="number" name="targetPallets" min="1" max="1000" value="10" required></label><label class="full"><span>Примечание</span><textarea name="note" maxlength="500" placeholder="Указания для смены"></textarea></label></div><p class="form-hint">В одной паллете ${app.catalog.palletSize} коробов. Задание появится в плане; запуск выполняется отдельно после открытия смены.</p><div class="form-foot"><button type="submit" class="button">Создать задание</button></div></form>`);
}
function reasonForm(action,id,title,description,extra='') {
  modal(`<div class="eyebrow">КОНТРОЛИРУЕМОЕ ДЕЙСТВИЕ</div><h2>${title}</h2><p class="subtitle">${description}</p><form id="reason-form" data-command="${action}" data-reference="${esc(id)}">${extra}<label><span>Причина / результат</span><textarea name="reason" minlength="5" maxlength="500" required placeholder="Что произошло и что сделано"></textarea></label><div class="form-foot"><button type="submit" class="button">Подтвердить действие</button></div></form>`);
}
async function details(id) {
  try {
    const d=await api('/api/plant/aggregate/'+encodeURIComponent(id));app.detail=d;
    const a=d.aggregate;
    modal(`<div class="eyebrow">КАРТОЧКА УПАКОВКИ</div><h2 class="mono">${a.id}</h2><div class="actions">${badge(a.lifecycle)}${d.document?badge(d.document.status):''}${d.printJob?badge(d.printJob.status):''}</div><div class="detail-meta">${[['Продукция',productName(a.product_id)],['Линия',lineName(a.line_id)],['Партия',a.batch],['Задание',a.order_id||'Перенос из версии 0.2'],['Смена',a.shift_id||'До учёта смен'],['Создано',when(a.created_at)]].map(([l,v])=>`<div><small>${l}</small><strong>${esc(v)}</strong></div>`).join('')}</div><div class="table-wrap"><table><thead><tr><th>№</th><th>${a.kind==='box'?'КОД БУТЫЛКИ':'КОД КОРОБА'}</th><th></th></tr></thead><tbody>${d.children.map((c,i)=>`<tr><td>${i+1}</td><td class="mono">${esc(c.code)}</td><td>${a.kind==='pallet'?btn('Состав','details',c.code):''}</td></tr>`).join('')}</tbody></table></div><div class="actions" style="margin-top:20px">${a.lifecycle!=='disassembled'?btn('Макет / контроль этикетки','label',a.id,'ghost'):''}${management()&&a.lifecycle==='closed'?btn('На карантин','quarantine',a.id):''}${management()&&a.lifecycle==='quarantined'?btn('Снять карантин','release',a.id):''}${management()&&a.kind==='box'&&a.lifecycle!=='disassembled'?btn('Разобрать короб','disassemble',a.id,'danger small'):''}</div><p class="form-hint">Разборка сохраняет историю и разрешена только до принятия или неопределённой отправки документа, если короб не включён в паллету.</p>`);
  } catch(error) {toast(error.message,true);}
}
function labelMarkup(a) {
  return `<div class="label-preview"><small>ДЕМОНСТРАЦИОННАЯ ЭТИКЕТКА</small><h2>${a.kind==='box'?'КОРОБ':'ПАЛЛЕТА'}</h2><div class="label-id">${a.id}</div><p>${productName(a.product_id)} · ${a.unit_count} ${a.kind==='box'?'шт.':'коробов'}<br>${lineName(a.line_id)} · партия ${esc(a.batch)}<br>${esc(a.order_id||'Историческое демо')}<br>${when(a.created_at)}</p><div class="label-warning">УЧЕБНЫЙ ОБРАЗЕЦ · НЕ ДЛЯ ОБОРОТА<br>Идентификатор DEMO не является КИГУ, КИТУ или SSCC. Макет не подтверждает физическую печать.</div></div>`;
}
async function showLabel(id,open=true) {
  if(open && await run('print-open',{id})===null) return;
  try {
    const d=await api('/api/plant/aggregate/'+encodeURIComponent(id));app.detail=d;
    modal(`<div class="eyebrow">ПЕЧАТЬ И КОНТРОЛЬ</div><h2>Этикетка ${d.aggregate.kind==='box'?'короба':'паллеты'}</h2><p class="subtitle">Сначала макет, затем контрольное считывание точного идентификатора.</p>${badge(d.printJob?.status)}${labelMarkup(d.aggregate)}<div class="actions">${btn('Печать / сохранить PDF','print-pdf',id,'secondary')}${btn('Имитировать сбой','print-fail',id)}</div><div class="divider"></div><form id="label-form" data-reference="${id}"><label><span>Контрольный код этикетки</span><input style="width:100%" name="code" required autocomplete="off" placeholder="Считайте код или введите идентификатор DEMO"></label><div class="form-foot"><button type="submit" class="button">Проверить совпадение</button>${btn('Сымитировать считывание','label-simulate',id,'ghost small')}</div></form><p class="form-hint">В демо используется текстовый идентификатор. Промышленное качество печати проверяется на оборудовании.</p>`);
  } catch(error) {toast(error.message,true);}
}
function renderLogin() {
  $('#main').innerHTML=`<section class="panel login-panel"><div class="panel-body"><div class="eyebrow">КОНТУР ПРОИЗВОДСТВА</div><h1>Вход в рабочее место</h1><p class="subtitle">Персональная учётная запись оператора, мастера или администратора.</p><form id="login-form"><label><span>Логин</span><input name="username" autocomplete="username" required></label><label><span>Пароль</span><input type="password" name="password" autocomplete="current-password" required></label><button class="button" type="submit">Войти →</button></form></div></section>`;
}
function account() {
  modal(`<h2>Учётная запись</h2><p class="subtitle">${esc(app.user.display_name)} · ${esc(app.user.role)}</p>${app.user.mode==='demo'?'<div class="hint warning">Сейчас включён явный локальный демо доступ с правами администратора. Персональные роли начинают действовать при входе под созданной учётной записью; перед приёмкой отключите ARM_DEMO_MODE.</div>':''}<form id="login-form"><div class="form-grid" style="margin-top:20px"><label><span>Персональный логин</span><input name="username" autocomplete="username" required></label><label><span>Пароль</span><input type="password" name="password" autocomplete="current-password" required></label></div><div class="form-foot"><button type="submit" class="button">Войти</button>${app.user.mode==='personal'?btn('Выйти','logout'):''}</div></form>`);
}
function walkthrough() {
  modal('<div class="eyebrow">СКВОЗНОЙ ПРОЦЕСС</div><h2>Проверка производственного сценария</h2><ol class="form-hint"><li>Откройте смену и создайте задание на одну или несколько полных паллет.</li><li>Запустите задание нужной линии. На рабочем месте считайте учебный кадр и закройте короб.</li><li>Откройте этикетку и выполните контрольное считывание. После этого короб доступен для паллеты.</li><li>Добавьте короб в паллету. Для полного примера мастер может сгенерировать недостающие короба учебным сценарием.</li><li>Закройте паллету, проверьте её этикетку. Обработайте документы коробов и затем паллеты в учебной очереди.</li><li>Проверьте сбой печати, карантин, тайм-аут и журнал инцидентов. Для устранения сохраняется причина и ответственный.</li><li>Выполните план, завершите задание и закройте смену. Посмотрите отчёт и CSV.</li></ol><p class="form-hint">Реальные камера, принтер и кабинет ГИС МТ требуют отдельного подключения и испытаний. Учебные результаты не подтверждают производственную готовность.</p>');
}

document.addEventListener('click',async event=>{
  const element=event.target.closest('[data-action]');if(!element||element.disabled) return;
  const action=element.dataset.action,id=element.dataset.id;
  if(action==='next') {app.history.push(app.cursor);app.cursor=app.next;return loadPage().catch(e=>toast(e.message,true));}
  if(action==='previous') {app.cursor=app.history.pop()||null;return loadPage().catch(e=>toast(e.message,true));}
  if(action==='clear-filters') {app.filters[page()]={};resetPagination();return loadPage().catch(e=>toast(e.message,true));}
  if(action==='new-order') return orderForm();
  if(action==='new-shift') return modal(`<div class="eyebrow">УПРАВЛЕНИЕ СМЕНОЙ</div><h2>Открыть рабочую смену</h2><form id="shift-form"><label><span>Название смены</span><input name="name" minlength="3" maxlength="80" required value="Смена ${new Date().toLocaleDateString('ru-RU')}"></label><div class="form-foot"><button type="submit" class="button">Открыть смену</button></div></form>`);
  if(action==='details') return details(id);
  if(action==='label') return showLabel(id);
  if(action==='print-pdf') {
    const win=window.open('','_blank','width=700,height=700');
    if(!win) return toast('Разрешите всплывающее окно для печати.',true);
    win.document.write(`<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Этикетка ${esc(id)}</title><style>body{font-family:Arial;padding:25px}.label-preview{border:2px solid black;padding:25px}.label-id{font:24px monospace;margin:22px 0}.label-warning{font-size:10px;border-top:1px dashed;padding-top:15px}p{font-size:12px;line-height:1.8}@page{margin:12mm}</style></head><body>${labelMarkup(app.detail.aggregate)}</body></html>`);
    win.document.close();win.focus();win.print();return;
  }
  if(['order-pause','order-complete','draft-clear','quarantine','release','disassemble','incident-resolve','order-plan','order-cancel'].includes(action)) {
    const labels={'order-pause':'Приостановить задание','order-complete':'Завершить задание','draft-clear':'Сбросить состав короба','quarantine':'Поместить упаковку на карантин','release':'Снять карантин','disassemble':'Разобрать короб','incident-resolve':'Зафиксировать устранение','order-plan':'Изменить план задания','order-cancel':'Отменить задание'};
    const descriptions={'disassemble':'История состава сохранится. Принятые или неопределённо отправленные документы и короб в паллете блокируют разборку.','quarantine':'Упаковка исключается из учебной обработки. Карантин короба распространяется на его паллету.','order-complete':'Незавершённый состав и короба вне паллеты блокируют завершение. Для досрочного закрытия требуется причина.'};
    const extra=action==='order-plan'?'<label style="margin-bottom:15px"><span>Новый план, коробов</span><input type="number" name="targetBoxes" min="1" max="100000" required></label>':'';
    return reasonForm(action,id,labels[action],descriptions[action]||'Действие и комментарий будут записаны в журнал.',extra);
  }
  if(action==='auth-challenge') {
    try {app.challenge=await api('/api/integration/challenge',{});render();toast('Запрос получен. Подпишите точную строку присоединённой подписью.');}catch(e){toast(e.message,true);}return;
  }
  if(action==='logout') {await api('/api/access/logout',{});closeModal();return initialize();}
  if(action==='camera'||action.startsWith('camera-')) return run('camera',{scenario:action==='camera'?'valid':action.slice(7)});
  if(action.startsWith('send-')) return run('exchange-run',{id,outcome:action.slice(5)});
  if(action==='connection') return run(action,{online:!app.station.online});
  if(action==='label-simulate') {
    if(await run('label-verify',{id,code:id})!==null) {closeModal();toast('Имитировано контрольное считывание этикетки');}return;
  }
  const result=await run(action,id?{id}:{});
  if(result?.aggregate) return showLabel(result.aggregate);
  if(result!==null&&action==='print-fail'&&$('#modal').open) return showLabel(id,false);
});
document.addEventListener('submit',async event=>{
  const form=event.target;if(!form.id) return;
  event.preventDefault();const data=Object.fromEntries(new FormData(form));
  try {
    if(form.id==='login-form') {await api('/api/access/login',data);closeModal();app.pending=null;return initialize();}
    if(form.id==='filter-form') {app.filters[page()]=data;resetPagination();return await loadPage();}
    if(form.id==='report-form') {app.filters.reports=data;return await loadPage();}
    if(form.id==='true-code-form') {const result=await api('/api/integration/codes',{codes:data.codes.split(/\r?\n/).filter(Boolean)});$('#true-code-result').textContent=JSON.stringify(result.result,null,2);return;}
    if(form.id==='auth-form') {await api('/api/integration/authenticate',{uuid:app.challenge.uuid,signature:data.signature.trim()});app.challenge=null;app.integration=await api('/api/integration/status');render();return;}
    let result=null;
    if(form.id==='order-form') result=await run('order-create',{line:data.line,product:data.product,batch:data.batch,targetBoxes:Number(data.targetPallets)*app.catalog.palletSize,note:data.note});
    if(form.id==='shift-form') result=await run('shift-open',data);
    if(form.id==='reason-form') result=await run(form.dataset.command,{...data,id:form.dataset.reference,...(data.targetBoxes?{targetBoxes:Number(data.targetBoxes)}:{})});
    if(form.id==='scan-form') {await run('scan-code',data);return;}
    if(form.id==='pallet-form') {await run('pallet-add',{id:data.id.trim()});return;}
    if(form.id==='label-form') result=await run('label-verify',{id:form.dataset.reference,code:data.code.trim()});
    if(form.id==='recipe-form') {result=await run('settings',Object.fromEntries(Object.entries(data).map(([k,v])=>[k,Number(v)])));app.catalog=await api('/api/plant/catalog');render();}
    if(result!==null) closeModal();
  } catch(error) {toast(error.message,true);}
});
$('#modal-close').onclick=closeModal;
$('#help-button').onclick=walkthrough;
$('#account-button').onclick=()=>app.user?account():renderLogin();
$('#account-top').onclick=()=>app.user?account():renderLogin();
$('#refresh').onclick=()=>loadPage().catch(e=>toast(e.message,true));
document.addEventListener('change',event=>{if(event.target.id==='overview-day'){app.day=event.target.value;loadPage().catch(e=>toast(e.message,true));}});
window.addEventListener('hashchange',()=>{resetPagination();if(app.user) loadPage().catch(e=>toast(e.message,true));});
function clockTick() {$('#clock').textContent=new Date().toLocaleString('ru-RU',{day:'numeric',month:'long',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Moscow'});}
clockTick();setInterval(clockTick,30000);
setInterval(async()=>{
  if(app.busy||!app.user||$('#modal').open||document.hidden||['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)) return;
  try {
    if(page()==='overview') await loadPage();
    else if(page()==='station') {const s=await api('/api/plant/station');if(s.revision!==app.station.revision){app.station=s;render();}}
  } catch(_) {$('#data-time').textContent='Связь с сервером прервана';}
},15000);
initialize();
