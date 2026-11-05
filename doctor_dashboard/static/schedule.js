(() => {
  'use strict';
  const names = JSON.parse(document.getElementById('consultation-names').textContent);
  const grid = document.getElementById('calendar-grid');
  const notice = document.getElementById('calendar-notice');
  const range = document.getElementById('calendar-range');
  const datePicker = document.getElementById('calendar-date');
  const dialog = document.getElementById('slot-dialog');
  const cache = new Map();
  const today = iso(new Date());
  let anchor = parseDay(datePicker.value);
  let view = 'workweek';
  let generation = 0;
  const START_HOUR = 0, END_HOUR = 24, HOUR_PX = 76;
  function iso(d) { return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; }
  function parseDay(s) { const [y,m,d] = s.split('-').map(Number); return new Date(y,m-1,d); }
  function addDays(d,n) { const x = new Date(d); x.setDate(x.getDate()+n); return x; }
  function monday(d) { return addDays(d,-(d.getDay()+6)%7); }
  function monthStart(d) { return new Date(d.getFullYear(),d.getMonth(),1); }
  function human(d,opts) { return d.toLocaleDateString('en-IN',opts); }
  function time12(hm) { const [h,m]=hm.split(':').map(Number); return `${h%12||12}:${String(m).padStart(2,'0')} ${h<12?'AM':'PM'}`; }
  function minute(hm) { const [h,m]=hm.split(':').map(Number); return h*60+m; }
  function kind(s) { return s.bookedCount>=s.capacity && s.capacity>0 ? 'booked' : s.bookedCount>0 || s.heldCount>0 ? 'partial' : 'available'; }
  function typeName(s) { return names[s.consultationTypeId] || 'Consultation'; }
  function element(tag, cls, text) { const e=document.createElement(tag); if(cls)e.className=cls; if(text!==undefined)e.textContent=text; return e; }
  function datesForView() {
    if(view==='day') return [anchor];
    if(view==='week'||view==='workweek') {
      const first=monday(anchor); return Array.from({length:view==='workweek'?5:7},(_,i)=>addDays(first,i));
    }
    const first=monday(monthStart(anchor));
    const last=new Date(anchor.getFullYear(),anchor.getMonth()+1,0);
    const weeks=Math.ceil(((last-first)/86400000+1)/7);
    return Array.from({length:weeks*7},(_,i)=>addDays(first,i));
  }
  function updateRange(days) {
    if(view==='month') range.textContent=human(anchor,{month:'long',year:'numeric'});
    else if(view==='day') range.textContent=human(anchor,{weekday:'long',day:'numeric',month:'long',year:'numeric'});
    else {
      const a=days[0],b=days[days.length-1];
      range.textContent=a.getMonth()===b.getMonth() ? `${human(a,{month:'long',day:'numeric'})} – ${b.getDate()}, ${b.getFullYear()}` : `${human(a,{month:'short',day:'numeric'})} – ${human(b,{month:'short',day:'numeric',year:'numeric'})}`;
    }
    datePicker.value=iso(anchor);
  }
  async function loadDay(day) {
    if(cache.has(day)) return cache.get(day);
    const response=await fetch(`${window.location.pathname}?date=${encodeURIComponent(day)}&format=json`,{credentials:'same-origin',headers:{Accept:'application/json'}});
    if(!response.ok) throw new Error(`Could not load ${day} (${response.status})`);
    const payload=await response.json();
    if(!Array.isArray(payload.slots)) throw new Error(`Invalid schedule response for ${day}`);
    cache.set(day,payload.slots); return payload.slots;
  }
  async function fetchAll(days,gen) {
    const results={}; let next=0; const errors=[];
    async function worker() { while(next<days.length) { const d=iso(days[next++]); try {results[d]=await loadDay(d);} catch(e){results[d]=[];errors.push(e.message);} if(gen!==generation)return; } }
    await Promise.all(Array.from({length:Math.min(5,days.length)},worker));
    return {results,errors};
  }
  function showDetails(s,day) {
    document.getElementById('slot-dialog-title').textContent=typeName(s);
    const content=document.getElementById('slot-dialog-content'); content.replaceChildren();
    for(const [label,value] of [['Date',human(parseDay(day),{weekday:'long',day:'numeric',month:'long',year:'numeric'})],['Time (IST)',`${time12(s.start)} – ${time12(s.end)}`],['Capacity',String(s.capacity)],['Booked',String(s.bookedCount)],['Held',String(s.heldCount)],['Status',s.status]]) {
      const p=element('p'); const strong=element('strong','',`${label}: `); p.append(strong,document.createTextNode(value));content.append(p);
    }
    if(s.appointments.length){const h=element('strong','','Patients');const ul=element('ul');s.appointments.forEach(a=>ul.append(element('li','',`${a.patient_name} (${a.status})`)));content.append(h,ul);}
    dialog.showModal();
  }
  function renderHours(days,results) {
    const cols=days.length;
    grid.className='time-grid'; grid.style.gridTemplateColumns=`70px repeat(${cols}, minmax(125px,1fr))`;
    grid.replaceChildren();
    const corner=element('div','time-corner');corner.style.gridColumn='1';corner.style.gridRow='1';grid.append(corner);
    days.forEach((d,i)=>{const head=element('div','day-header'+(iso(d)===today?' today':''));head.style.gridColumn=String(i+2);head.style.gridRow='1';head.append(element('strong','',String(d.getDate())),element('small','',human(d,{weekday:'short'})));grid.append(head);});
    for(let h=START_HOUR;h<END_HOUR;h++){
      const row=h-START_HOUR+2;const time=element('div','time-label',`${h%12||12} ${h<12?'AM':'PM'}`);time.style.gridRow=String(row);time.style.gridColumn='1';grid.append(time);
      days.forEach((d,i)=>{const cell=element('div','hour-cell'+(iso(d)===today?' today':''));cell.style.gridColumn=String(i+2);cell.style.gridRow=String(row);grid.append(cell);});
    }
    // Position events relative to the grid, below the sticky day header.
    const minHeight=74+(END_HOUR-START_HOUR)*HOUR_PX;
    grid.style.minHeight=`${minHeight}px`;
    days.forEach((d,i)=>{
      const day=iso(d),slots=results[day]||[];
      slots.forEach(s=>{
        const start=minute(s.start),end=minute(s.end);
        if(end<=START_HOUR*60||start>=END_HOUR*60||end<=start)return;
        const event=element('button',`slot-event ${kind(s)}`);
        event.type='button';event.append(element('strong','',typeName(s)),element('span','',`${time12(s.start)} – ${time12(s.end)}`));
        const clipped=Math.max(start,START_HOUR*60);
        event.style.top=`${74+(clipped-START_HOUR*60)/60*HOUR_PX+2}px`;
        event.style.height=`${Math.max(20,Math.min(end,END_HOUR*60)-clipped)/60*HOUR_PX-4}px`;
        event.style.left=`calc(70px + (100% - 70px) * ${i} / ${cols} + 5px)`;
        event.style.width=`calc((100% - 70px) / ${cols} - 10px)`;
        event.title=`${typeName(s)} • ${time12(s.start)}–${time12(s.end)} • ${s.bookedCount}/${s.capacity} booked`;
        event.addEventListener('click',()=>showDetails(s,day));grid.append(event);
      });
    });
  }
  function renderMonth(days,results) {
    grid.className='month-grid';grid.style.gridTemplateColumns='';grid.style.minHeight='';grid.replaceChildren();
    ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].forEach(n=>grid.append(element('div','month-head',n)));
    days.forEach(d=>{
      const day=iso(d),cell=element('div','month-cell'+(d.getMonth()!==anchor.getMonth()?' outside':'')+(day===today?' today':''));
      cell.append(element('span','month-date',String(d.getDate())));
      (results[day]||[]).forEach(s=>{const event=element('button',`month-event ${kind(s)}`,`${s.start} ${typeName(s)}`);event.type='button';event.addEventListener('click',()=>showDetails(s,day));cell.append(event);});grid.append(cell);
    });
  }
  async function render() {
    const gen=++generation,days=datesForView();updateRange(days);
    document.querySelectorAll('[data-view]').forEach(b=>{b.classList.toggle('selected',b.dataset.view===view);b.setAttribute('aria-pressed',String(b.dataset.view===view));});
    notice.textContent='Loading schedule…';grid.replaceChildren();
    const {results,errors}=await fetchAll(days,gen);
    if(gen!==generation)return;
    notice.textContent=errors.length?`${errors.length} date(s) could not be loaded. Try refreshing.`:'';
    if(view==='month')renderMonth(days,results);else renderHours(days,results);
    if(view!=='month') {
      const scroll=document.getElementById('calendar-scroll');
      scroll.scrollTop=74+8*HOUR_PX;
    }
  }
  document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>{view=b.dataset.view;render();}));
  document.getElementById('calendar-prev').addEventListener('click',()=>{anchor=view==='month'?new Date(anchor.getFullYear(),anchor.getMonth()-1,Math.min(anchor.getDate(),28)):addDays(anchor,view==='day'?-1:-7);render();});
  document.getElementById('calendar-next').addEventListener('click',()=>{anchor=view==='month'?new Date(anchor.getFullYear(),anchor.getMonth()+1,Math.min(anchor.getDate(),28)):addDays(anchor,view==='day'?1:7);render();});
  document.getElementById('calendar-today').addEventListener('click',()=>{anchor=parseDay(today);render();});
  datePicker.addEventListener('change',()=>{if(datePicker.value){anchor=parseDay(datePicker.value);render();}});
  document.getElementById('slot-dialog-close').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('click',e=>{if(e.target===dialog)dialog.close();});
  render();
})();
