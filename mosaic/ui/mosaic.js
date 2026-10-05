'use strict';
const $ = (s, root=document) => root.querySelector(s);
const $$ = (s, root=document) => [...root.querySelectorAll(s)];
let state = {project:null, job:{state:'idle'}}, page='import', view='composite';
let pendingEdits=null, saving=false, saveFailed=false, saveTimer, previewVersion=0, geometry=null;
let scale=1, offset={x:0,y:0}, fitted=true, detailTimer, toastTimer, polling=false, importing=false;
let loadedProject=null;
let recentVersion=0, exportVersion=0;
const stages=['Check panels','Find coarse features','Discover overlaps','Solve panel layout','Refine alignment','Match brightness','Select contributing panels','Blend seams','Finish & save'];
const defaultEdits={saturation:1,smoothing:0,strength:1,highlights:0,contrast:0,rotation:0,coverage:'full',mirror:false,wavelets:[0,0,0,0],wavelet_kernel:'linear',wavelet_denoise:2,usm_amount:0,usm_radius:1,usm_threshold:.01,l_visible:true,rgb_visible:true};
const points={L:[],RGB:[]};
function message(text) { $('#toast').textContent=text; $('#toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').hidden=true,8000); }
async function api(path, data) {
  const options=data===undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)};
  const r=await fetch('/api/'+path,options); const o=await r.json();
  if(!r.ok) throw new Error(o.error||'The operation failed.');
  return o;
}
function payload(data={}) { return {id:state.project?.id,...data}; }
async function action(name, data={}) {
  try { await flushEdits(); state=await api(name,payload(data)); render(); return true; }
  catch(e){message(e.message); return false;}
}
function showPage(next) {
  page=next; const editing=page==='edit'&&!!state.project;
  document.body.scrollTop=0; document.documentElement.scrollTop=0;
  document.body.classList.toggle('editing',editing);
  $('#setup').hidden=editing; $('#workspace').hidden=!editing;
  $('#welcome').hidden=!!state.project&&page!=='projects';
  $('#close-project').hidden=!state.project;
  $('#import-page').hidden=!state.project||page!=='import';
  $('#build-page').hidden=!state.project||page!=='build';
  $$('.mast nav button').forEach(b=>{b.classList.toggle('active',b.dataset.page===page); b.disabled=!state.project;});
  if(editing) refreshPreview();
  if(page==='projects'||!state.project)loadRecent();
}
function landingPage(){
  if(state.job.state==='running')return 'build';
  if(Object.values(state.project?.sets||{}).some(s=>['interrupted','failed','missing'].includes(s.state)))return 'build';
  return state.project?.sets.L.mosaic?'edit':'import';
}
function fileSize(bytes){return bytes>=1024**3?`${(bytes/1024**3).toFixed(2)} GiB`:`${(bytes/1024**2).toFixed(1)} MiB`;}
async function loadRecent(){
  const ticket=++recentVersion;
  try{
    const info=await api('recent');if(ticket!==recentVersion)return;
    $('#recent-projects').hidden=!info.enabled||!!state.project&&page!=='projects';
    $('#recent-warning').textContent=info.warning||'';
    const list=$('#recent-list');list.replaceChildren();
    if(!info.projects.length){const li=document.createElement('li');li.className='hint';li.textContent='Your saved mosaics will appear here after you create or open a project.';list.append(li);}
    for(const project of info.projects){
      const li=document.createElement('li'),button=document.createElement('button');button.className='recent-open';
      const name=document.createElement('strong'),note=document.createElement('small'),path=document.createElement('small');
      name.textContent=project.name;note.textContent=[project.state,project.panels?`${project.panels.L} L · ${project.panels.RGB} RGB`:null,project.bytes?`${fileSize(project.bytes)} native mosaics`:null].filter(Boolean).join(' · ');path.textContent=project.folder;
      button.append(name,note,path);button.disabled=!project.available||state.job.state==='running';
      button.onclick=async()=>{if(await action('open',{folder:project.folder}))showPage(landingPage());};li.append(button);list.append(li);
    }
  }catch(e){$('#recent-warning').textContent=e.message;}
}
function render() {
  const p=state.project, job=state.job, busy=job.state==='running';
  $('#saved').textContent=saveFailed&&pendingEdits?'Not saved · adjust again to retry':saving||pendingEdits?'Saving…':p?'Saved locally':'Local · private';
  $('#export-open').disabled=!p?.sets.L.mosaic||busy;
  if(!p){loadedProject=null;document.body.classList.remove('editing');showVisibility();return;}
  if(loadedProject!==p.id){
    loadedProject=p.id;
    $('#match-popover').hidden=true;
    for(const key of ['L','RGB']){const card=$(`.set[data-set="${key}"]`),o=p.sets[key].optics;
      $('.pixel',card).value=o.pixel_um||'';$('.focal',card).value=o.focal_mm||'';$('.barlow',card).value=o.barlow||1;}
  }
  $('#project-title').textContent=p.name; $('#project-location').textContent=p.folder;
  $('#canvas-project').textContent=p.name.toUpperCase();
  for(const key of ['L','RGB']) {
    const s=p.sets[key], card=$(`.set[data-set="${key}"]`);
    $('.state',card).textContent=s.state==='empty'?(key==='RGB'?'Optional':'Empty'):s.state;
    const headers=s.headers?` · ${s.headers.min_width}–${s.headers.max_width} × ${s.headers.min_height}–${s.headers.max_height} px · ${s.headers.depths.join(', ')}`:'';
    $('.set-summary',card).textContent=s.mosaic?`${s.inputs.length} panels · ${s.width.toLocaleString()} × ${s.height.toLocaleString()} · native mosaic kept`:`${s.inputs.length} panels selected${headers}`;
    $('.set-error',card).textContent=s.error||'';
    const ul=$('.files ul',card); ul.replaceChildren();
    for(const path of s.inputs) {const li=document.createElement('li'); li.textContent=path.split(/[\\/]/).pop(); ul.append(li);}
    for(const skip of s.skipped||[]){const li=document.createElement('li');li.className='set-error';li.textContent=`Skipped ${skip.name}: ${skip.reason}`;ul.append(li);}
    if(s.skipped?.length)$('.set-error',card).textContent+=`${s.error?'\n':''}${s.skipped.length} unreadable or unsuitable panels left out; see Panel list.`;
    $$('input,button',card).forEach(el=>el.disabled=busy||!!s.mosaic||importing);
  }
  $('#build-both').disabled=busy||importing||!p.sets.L.inputs.length||Object.values(p.sets).every(s=>!s.inputs.length||s.mosaic);
  $('#import-note').textContent=p.sets.RGB.inputs.length?'Each set keeps its native scale. Colour will be matched after the build.':'Colour is optional. You can add it after building luminance.';
  const sampling=['L','RGB'].map(k=>{const o=p.sets[k].optics;return o.pixel_um&&o.focal_mm?o.pixel_um/(o.focal_mm*(o.barlow||1)):null;});
  if(sampling.every(v=>v>0))$('#import-note').textContent+=` Optics estimate: colour sampling is ${(sampling[1]/sampling[0]).toFixed(2)}× luminance sampling. The match still uses lunar detail.`;
  const recovery=Object.entries(p.sets).filter(([,s])=>['interrupted','failed','missing'].includes(s.state));
  $('#recovery').hidden=!recovery.length;
  $('#recovery-summary').textContent=Object.entries(p.sets).map(([k,s])=>{const name=k==='L'?'Luminance':'Colour';return s.mosaic?`${name}: finished and saved${s.duration?` (${Math.round(s.duration)} seconds of completed processing)`:''}.`:['interrupted','failed','missing'].includes(s.state)?`${name}: ${s.state}${s.last_stage?` during stage ${s.last_stage}`:''}. Restart this set from the beginning; intermediate stages are not cached.`:`${name}: ${s.inputs.length?'ready to build':'no panels selected'}.`;}).join(' ');
  const builds=$('#build-cards'); builds.replaceChildren();
  for(const key of ['L','RGB']) {
    const s=p.sets[key], card=document.createElement('article'); card.className='card build-card';
    const title=document.createElement('h2'); title.textContent=key==='L'?'Luminance':'Colour'; card.append(title);
    const elapsed=busy&&job.set===key&&s.started?Math.floor(Date.now()/1000-s.started):s.duration?Math.round(s.duration):null;
    const note=document.createElement('p'); note.className='hint'; note.textContent=(s.mosaic?`${s.width.toLocaleString()} × ${s.height.toLocaleString()} · saved`:s.inputs.length?`${s.inputs.length} panels · ${s.state}`:'No panels selected')+(elapsed!==null?` · ${Math.max(0,elapsed)} s`:''); card.append(note);
    if(s.build_facts&&s.mosaic){const facts=s.build_facts,summary=document.createElement('p');summary.className='hint';summary.textContent=`${facts.placed} of ${s.inputs.length} panels placed · ${facts.contributing} contribute${facts.rms_px!==undefined?` · ${facts.rms_px.toFixed(2)} px RMS`:''}`;card.append(summary);
      if(facts.not_placed.length){const detail=document.createElement('details'),heading=document.createElement('summary'),list=document.createElement('ul');heading.textContent=`${facts.not_placed.length} panels left out`;detail.append(heading);for(const panel of facts.not_placed){const item=document.createElement('li');item.textContent=`${panel.id}: ${panel.reason}`;list.append(item);}detail.append(list);card.append(detail);}}
    const ol=document.createElement('ol');
    const actualStages=s.inputs.length===1?['Check panel','Retain native 16-bit TIFF']:stages;
    actualStages.forEach((text,i)=>{const li=document.createElement('li'), marker=document.createElement('span'); const active=busy&&job.set===key;
      const done=!!s.mosaic||(active&&job.stage>i+1); li.className=done?'done':active&&job.stage===i+1?'running':''; marker.textContent=done?'✓':String(i+1).padStart(2,'0');li.append(marker,document.createTextNode(text));ol.append(li);}); card.append(ol);
    if(s.error){const error=document.createElement('p');error.className='set-error';error.textContent=s.error;card.append(error);}
    if(!s.mosaic&&s.inputs.length){const b=document.createElement('button');b.className='btn';b.textContent=['failed','interrupted'].includes(s.state)?'Restart this set':'Build this set';b.disabled=busy;b.onclick=()=>action('build',{sets:[key]});card.append(b);}
    builds.append(card);
  }
  $('#job-title').textContent=job.state==='idle'?'Ready to build':job.title||job.action;
  $('#job-progress').value=job.progress||0; $('#job-log').textContent=(job.log||[]).join('\n');
  $('#cancel-job').hidden=!busy; $('#status-job').textContent=busy?`${job.title} · ${Math.round((job.progress||0)*100)}%`:job.state==='failed'?job.error||'Operation failed':'';
  $('#job-result').textContent=job.error||job.result?.path||(job.state==='done'?Object.entries(job.result||{}).filter(([k])=>['L','RGB'].includes(k)).map(([k,v])=>`${k}: ${v}`).join(' · '):'');
  $('#go-edit').disabled=!p.sets.L.mosaic;
  $('#l-layer-state').textContent=p.sets.L.mosaic?`${p.sets.L.width.toLocaleString()} × ${p.sets.L.height.toLocaleString()}`:'Not built';
  $('#rgb-layer-state').textContent=p.match?'Matched to luminance':p.sets.RGB.mosaic?'Ready to match':'Add a colour set';
  const both=!!p.sets.L.mosaic&&!!p.sets.RGB.mosaic;
  for(const key of ['L','RGB']){
    const s=p.sets[key],img=$(`[data-thumbnail="${key}"]`),badge=img.parentElement,stamp=`${p.id}:${s.mosaic}:${s.started||0}`;
    if(!s.mosaic){img.hidden=true;$('span',badge).hidden=false;img.dataset.stamp='';}
    else if(img.dataset.stamp!==stamp){img.hidden=true;$('span',badge).hidden=false;img.dataset.stamp=stamp;img.onload=()=>{img.hidden=false;$('span',badge).hidden=true;};img.onerror=()=>{img.hidden=true;$('span',badge).hidden=false;};img.src=`/api/layer-thumbnail?id=${p.id}&set=${key}&v=${s.started||0}`;}
  }
  $('[data-visible="rgb_visible"]').disabled=!p.match;
  $('[data-visible="l_visible"]').disabled=!p.sets.L.mosaic;
  $('#layer-visibility-info').textContent=!p.edits.l_visible&&(!p.edits.rgb_visible||!p.match)?'No visible matched layer. Enable luminance or match and show colour to export the combined image.':!p.edits.l_visible?'Colour supplies lightness and chroma while luminance is hidden.':!p.edits.rgb_visible?'Luminance only; colour adjustments are kept.':'L supplies lightness; RGB supplies chroma. Switches affect the combined image and export.';
  $('#match').disabled=!both||busy; $('#manual-open').disabled=!both||busy;
  $('#check-match').disabled=!p.match||busy;$('#north-up').disabled=!p.sets.L.mosaic||busy;
  $('#rotation').disabled=busy&&job.action==='Find lunar north';$('#mirror').disabled=$('#rotation').disabled;
  $('#coverage').disabled=!p.match;
  $('#match-description').textContent=p.match?`Scale ${p.match.scale.toFixed(3)}× · rotation ${p.match.rotation.toFixed(2)}°`:both?'Different cameras or a Barlow? Match using the lunar detail.':'Build a colour mosaic to add colour.';
  const checks=p.match?.diagnostics;
  $('#match-chip').textContent=checks?.measured?`Checked ${checks.median.toFixed(2)} px · worst ${checks.worst.toFixed(2)} px`:p.match?`Fit ${p.match.median.toFixed(2)} px · local check ${checks?'unknown':'pending'}`:'Match not measured';
  $('#match-metrics').textContent=p.match?`${p.match.method==='manual'?`Manual fit · ${p.match.candidates} control-point pairs`:`Feature fit · ${p.match.inliers}/${p.match.candidates} inliers`}\nMedian ${p.match.median.toFixed(2)} L px\n95% below ${p.match.p95.toFixed(2)} L px\nWorst ${p.match.worst.toFixed(2)} L px${p.match.scale_prior?`\nOptics estimate ${p.match.scale_prior.toFixed(3)}×`:''}\n${p.match.warning}`:'No match yet.';
  if(checks)$('#match-metrics').textContent+=`\n\nIndependent local checks\n${checks.measured} measured · ${checks.unknown} unknown${checks.measured?`\nMedian ${checks.median.toFixed(2)} L px\n95% below ${checks.p95.toFixed(2)} L px\nWorst ${checks.worst.toFixed(2)} L px`:''}\n${checks.message}`;
  $('#match-explanation').textContent=checks?.measured?`${checks.measured} overlapping patches were checked independently of the fitted points. Median offset ${checks.median.toFixed(2)} L pixels; worst ${checks.worst.toFixed(2)}. ${checks.unknown} patches could not be measured. Locations outside these patches remain unmeasured. Inspect the overlay at 1:1.`:p.match?'The transform fits the selected feature points. Independent checks have not established the match elsewhere. Use Check overlapping areas and inspect at 1:1.':'Build both mosaics and match the colour layer to measure the relationship.';
  $('#match-transform').textContent=p.match?`RGB → native L\n${p.match.scale.toFixed(5)}× · ${p.match.rotation.toFixed(3)}°\ndx ${p.match.matrix[0][2].toFixed(2)} · dy ${p.match.matrix[1][2].toFixed(2)} L px\n${p.match.inliers}/${p.match.candidates} ${p.match.method==='manual'?'control pairs':'feature inliers'}`:'';
  $('#match-chip').setAttribute('aria-expanded',String(!$('#match-popover').hidden));
  const orient=p.orientation;
  $('#orientation-info').textContent=orient?`${orient.matches} terrain matches · ${orient.rms_px.toFixed(2)} L px RMS. ${Math.abs(p.edits.rotation-orient.rotation)<.001&&p.edits.mirror===orient.mirror?'Lunar north is up.':'North-up estimate kept; the view has been adjusted.'}`:'Both layers turn together. Uses lunar reference maps, downloaded if needed.';
  for(const key of ['strength','saturation','smoothing']) $(`[data-edit="${key}"]`).disabled=!p.match;
  if(!pendingEdits&&!saving) syncEdits(p.edits);
  showVisibility();
}
function showVisibility(){ const editing=page==='edit';$('#setup').hidden=editing;$('#workspace').hidden=!editing;$('#welcome').hidden=!!state.project&&page!=='projects';$('#close-project').hidden=!state.project;$('#import-page').hidden=!state.project||page!=='import';$('#build-page').hidden=!state.project||page!=='build';if(state.project&&page!=='projects')$('#recent-projects').hidden=true; }
function syncEdits(e){ $$('[data-edit]').forEach(el=>{el.value=e[el.dataset.edit]; updateOutput(el.dataset.edit,e[el.dataset.edit]);}); $$('[data-visible]').forEach(el=>el.checked=e[el.dataset.visible]);$('#coverage').value=e.coverage;$('#rotation').value=e.rotation;$('#mirror').checked=e.mirror;$('#wavelet-kernel').value=e.wavelet_kernel;$$('[data-wave]').forEach(el=>{el.value=e.wavelets[Number(el.dataset.wave)];$(`[data-wave-output="${el.dataset.wave}"]`).textContent=Number(el.value).toFixed(2);}); }
function updateOutput(key,v){const out=$(`[data-output="${key}"]`);if(out) out.textContent=key==='strength'||key==='highlights'?`${Math.round(v*100)}%`:key==='saturation'?`${Number(v).toFixed(2)}×`:key==='smoothing'?`${Number(v).toFixed(1)} px`:Number(v).toFixed(key==='usm_threshold'?3:2);}
function changeEdit(key,value){pendingEdits={...(pendingEdits||state.project.edits),[key]:value};saveFailed=false;updateOutput(key,value);$('#saved').textContent='Saving…';clearTimeout(saveTimer);saveTimer=setTimeout(()=>flushEdits().catch(e=>message(e.message)),180);}
async function flushEdits(){
  clearTimeout(saveTimer);
  if(saving){while(saving) await new Promise(r=>setTimeout(r,30)); if(pendingEdits) return flushEdits(); return;}
  if(!pendingEdits) return;
  const edits=pendingEdits;pendingEdits=null;saving=true;
  try {state=await api('edits',payload({edits,revision:state.project.revision}));saveFailed=false;await refreshPreview(false);}
  // the rejected values go back into pendingEdits (newer keystrokes win), so render() keeps showing what the user chose
  catch(e){pendingEdits={...edits,...(pendingEdits||{})};saveFailed=true;try{state=await api('state');}catch(_){}render();throw e;}
  finally {saving=false;render();}
  if(pendingEdits) await flushEdits();
}
async function refreshPreview(reset=true){
  if(page!=='edit'||!state.project?.sets.L.mosaic)return;
  const ticket=++previewVersion, id=state.project.id;
  try {
    const info=await api('geometry?id='+encodeURIComponent(id)); if(ticket!==previewVersion)return;
    geometry=info;$('#status-dimensions').textContent=`${info.width.toLocaleString()} × ${info.height.toLocaleString()} · ${view}`;
    $('.status-right').textContent=view==='errors'?'Green <1 px · amber <3 px · red ≥3 px · hollow = unknown':'Drag to pan · scroll to zoom';
    $('#coverage-info').textContent=info.matched?`${info.missing_colour.toFixed(1)}% of the covered L canvas has no colour.`:'No colour layer matched.';
    const sharp=info.sharpening;
    $('#grain-info').textContent=sharp?.enabled?`Fine-scale variation ${sharp.fine_variation===null?'unmeasurable':sharp.fine_variation.toFixed(2)+'×'} in the sampled crop. Includes real detail and grain; inspect at 1:1.`:'Sharpening is off.';
    $('#grain-info').classList.toggle('warning',!!sharp?.enabled&&sharp.fine_variation>1.5);
    const response=await fetch(`/api/preview?id=${id}&view=${view}&v=${state.project.revision}`);
    if(!response.ok){const e=await response.json();throw new Error(e.error);}
    const url=URL.createObjectURL(await response.blob()); if(ticket!==previewVersion){URL.revokeObjectURL(url);return;}
    const im=$('#mosaic');const old=im.dataset.url;
    im.onload=()=>{if(old)URL.revokeObjectURL(old);im.dataset.url=url;$('#empty-canvas').hidden=true;if(reset||fitted)fit();else positionImage();};im.src=url;
  }catch(e){message(e.message);}
}
function fit(){if(!geometry)return;const stage=$('#stage');scale=Math.min((stage.clientWidth-370)/geometry.width,(stage.clientHeight-140)/geometry.height);scale=Math.max(.001,scale);offset={x:Math.max(70,(stage.clientWidth-340-geometry.width*scale)/2),y:Math.max(110,(stage.clientHeight-geometry.height*scale)/2)};fitted=true;positionImage();}
function positionImage(){if(!geometry)return;const im=$('#mosaic');im.style.width=geometry.width+'px';im.style.height=geometry.height+'px';im.style.transform=`translate(${offset.x}px,${offset.y}px) scale(${scale})`;$('#zoom-label').textContent=`${Math.round(scale*100)}%`;$('#detail').hidden=true;clearTimeout(detailTimer);detailTimer=setTimeout(loadDetail,180);}
async function loadDetail(){
  if(page!=='edit'||!geometry)return;
  const stage=$('#stage'), ticket=previewVersion, position=`${scale},${offset.x},${offset.y}`, id=state.project.id;
  const q=new URLSearchParams({id,view,x:-offset.x,y:-offset.y,w:stage.clientWidth,h:stage.clientHeight,scale});
  try {const r=await fetch('/api/viewport?'+q);if(!r.ok)return;const url=URL.createObjectURL(await r.blob());if(ticket!==previewVersion||position!==`${scale},${offset.x},${offset.y}`){URL.revokeObjectURL(url);return;}const im=$('#detail'),old=im.dataset.url;im.onload=()=>{if(old)URL.revokeObjectURL(old);im.dataset.url=url;im.hidden=false;};im.src=url;}catch(e){/* retain the preview while disconnected */}
}
function zoom(factor,x,y){if(!geometry)return;const stage=$('#stage');x??=stage.clientWidth/2;y??=stage.clientHeight/2;const next=Math.max(.005,Math.min(8,scale*factor));offset.x=x-(x-offset.x)*next/scale;offset.y=y-(y-offset.y)*next/scale;scale=next;fitted=false;positionImage();}
async function importFiles(key,files){
  if(!state.project){message('Create or open a project first.');return;}
  const supported=files.filter(f=>/\.(tiff?|png|jpe?g)$/i.test(f.name));if(!supported.length){message('Choose TIFF, PNG or JPEG panels. FITS is not supported yet.');return;}
  if(supported.length!==files.length)message(`${files.length-supported.length} non-image files skipped.`);
  importing=true;render();let count=0;
  try {for(const file of supported){$('#saved').textContent=`Importing ${++count}/${supported.length}…`;const q=new URLSearchParams({id:state.project.id,set:key,name:file.name});const r=await fetch('/api/upload?'+q,{method:'POST',body:file});const o=await r.json();if(!r.ok)throw new Error(o.error);state=o;}
    const card=$(`.set[data-set="${key}"]`);
    state=await api('inputs',payload({set:key,paths:state.project.sets[key].inputs,optics:{pixel_um:$('.pixel',card).value,focal_mm:$('.focal',card).value,barlow:$('.barlow',card).value}}));
    message(`${count} panels imported. A copy is kept inside the project.`);}
  catch(e){message(e.message);}
  finally{importing=false;render();}
}
function clearPoints(){points.L=[];points.RGB=[];$$('.point-dot').forEach(el=>el.remove());$('#point-count').textContent='Choose point 1 in luminance.';}
async function poll(){if(polling||saving||importing)return;polling=true;try{const old=state,next=await api('state');if(saving||pendingEdits||importing)return;if(state.project&&next.project?.id===state.project.id&&next.project.revision<state.project.revision)return;state=next;render();if(page==='edit'&&(old.project?.id!==state.project?.id||old.project?.match!==null&&!state.project?.match||JSON.stringify(old.project?.match)!==JSON.stringify(state.project?.match)))refreshPreview();if(old.job.state==='running'&&state.job.state!=='running'){if(state.job.error)message(state.job.error);if(page==='edit')refreshPreview(false);}}catch(e){$('#saved').textContent='Connection lost · reconnecting…';}finally{polling=false;}}
$$('[data-page]').forEach(b=>b.onclick=()=>showPage(b.dataset.page));
$('#home').onclick=()=>showPage('projects');
$('#close-project').onclick=async()=>{if(await action('close'))showPage('import');};
$('#create').onclick=async()=>{if(await action('new',{folder:$('#project-folder').value,name:$('#project-name').value}))showPage('import');};
$('#open-project').onclick=async()=>{if(await action('open',{folder:$('#open-folder').value}))showPage(landingPage());};
$('#recent-clear').onclick=async()=>{if(await action('recent-clear'))loadRecent();};
$$('.set').forEach(card=>{const key=card.dataset.set;
  $('.scan',card).onclick=()=>action('inputs',{set:key,paths:[$('.source-path',card).value],optics:{pixel_um:$('.pixel',card).value,focal_mm:$('.focal',card).value,barlow:$('.barlow',card).value}});
  $('.folder-upload',card).onchange=e=>importFiles(key,[...e.target.files]);
  $$('.optics input',card).forEach(el=>el.onchange=()=>{if(state.project?.sets[key].inputs.length&&!state.project.sets[key].mosaic)
    action('inputs',{set:key,paths:state.project.sets[key].inputs,optics:{pixel_um:$('.pixel',card).value,focal_mm:$('.focal',card).value,barlow:$('.barlow',card).value}});});
  const drop=$('.drop',card);drop.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('.folder-upload',card).click();}};
  drop.ondragover=e=>{e.preventDefault();drop.classList.add('drag');};drop.ondragleave=()=>drop.classList.remove('drag');drop.ondrop=e=>{e.preventDefault();drop.classList.remove('drag');importFiles(key,[...e.dataTransfer.files]);};
});
$('#build-both').onclick=async()=>{if(await action('build'))showPage('build');};$('#cancel-job').onclick=()=>action('cancel');$('#go-edit').onclick=()=>showPage('edit');
$('#match').onclick=()=>action('match');$('#match-chip').onclick=()=>{$('#match-popover').hidden=!$('#match-popover').hidden;$('#match-chip').setAttribute('aria-expanded',String(!$('#match-popover').hidden));};
$('#match-popover-close').onclick=()=>{$('#match-popover').hidden=true;$('#match-chip').setAttribute('aria-expanded','false');$('#match-chip').focus();};
$('#show-error-map').onclick=()=>{$('[data-view="errors"]').click();$('#match-popover-close').click();};
$$('[data-visible]').forEach(el=>el.onchange=()=>changeEdit(el.dataset.visible,el.checked));
$('#check-match').onclick=()=>action('check-match');$('#north-up').onclick=()=>{fitted=true;action('north-up');};
$$('[data-layer]').forEach(b=>b.onclick=()=>{$('#rgb-controls').hidden=b.dataset.layer!=='RGB';$('#l-controls').hidden=b.dataset.layer!=='L';});
$$('[data-edit]').forEach(el=>el.oninput=()=>changeEdit(el.dataset.edit,Number(el.value)));
$('#coverage').onchange=()=>{fitted=true;changeEdit('coverage',$('#coverage').value);};$('#rotation').onchange=()=>{fitted=true;changeEdit('rotation',Number($('#rotation').value));};
$('#mirror').onchange=()=>{fitted=true;changeEdit('mirror',$('#mirror').checked);};$('#wavelet-kernel').onchange=()=>changeEdit('wavelet_kernel',$('#wavelet-kernel').value);
$$('[data-wave]').forEach(el=>el.oninput=()=>{const waves=[...(pendingEdits||state.project.edits).wavelets];waves[Number(el.dataset.wave)]=Number(el.value);$(`[data-wave-output="${el.dataset.wave}"]`).textContent=Number(el.value).toFixed(2);changeEdit('wavelets',waves);});
$('#reset-edits').onclick=()=>{pendingEdits=structuredClone(defaultEdits);fitted=true;flushEdits().then(()=>render()).catch(e=>message(e.message));};
$$('[data-view]').forEach(b=>b.onclick=()=>{view=b.dataset.view;$$('[data-view]').forEach(x=>x.classList.toggle('active',x===b));refreshPreview(false);});
$('#fit').onclick=fit;$('#zoom-in').onclick=()=>zoom(1.4);$('#zoom-out').onclick=()=>zoom(1/1.4);$('#one-to-one').onclick=()=>zoom(1/scale);
const stage=$('#stage');stage.onwheel=e=>{e.preventDefault();const rect=stage.getBoundingClientRect();zoom(e.deltaY<0?1.15:1/1.15,e.clientX-rect.left,e.clientY-rect.top);};let drag=null;
stage.onpointerdown=e=>{drag={x:e.clientX,y:e.clientY,ox:offset.x,oy:offset.y};stage.setPointerCapture(e.pointerId);};stage.onpointermove=e=>{if(!drag)return;offset={x:drag.ox+e.clientX-drag.x,y:drag.oy+e.clientY-drag.y};fitted=false;positionImage();};stage.onpointerup=()=>drag=null;stage.onpointercancel=()=>drag=null;
window.onresize=()=>{if(fitted)fit();else positionImage();};
async function exportPreview(){
  const ticket=++exportVersion;$('#export-run').disabled=true;$('#export-summary').textContent='Preparing preview…';$('#export-preview').hidden=true;
  const q=new URLSearchParams({id:state.project.id,coverage:$('#export-coverage').value,factor:$('#export-scale').value,layer:$('#export-layer').value});
  try{const info=await api('export-preview?'+q);if(ticket!==exportVersion)return;
    $('#export-preview').src=info.preview;$('#export-preview').hidden=false;
    const format=$('#format').value;
    const jpegOK=format!=='jpg'||info.width*info.height<=info.jpeg_max_pixels&&Math.max(info.width,info.height)<=info.jpeg_max_side;
    $('#export-summary').textContent=`${info.width.toLocaleString()} × ${info.height.toLocaleString()} px · ${info.composition} · ${format==='jpg'?'8':'16'}-bit · ${info.missing_colour.toFixed(1)}% L coverage without colour. ${format==='tif'?`Approximately ${fileSize(info.raw_bytes)} uncompressed.`:'Compressed file size depends on the image.'} Preview approximates sharpening; inspect native detail in the workspace.${jpegOK?'':' Too large for JPEG; choose TIFF/PNG or a smaller size.'}`;
    $('#export-run').disabled=!jpegOK;
  }catch(e){if(ticket===exportVersion)$('#export-summary').textContent=e.message;}
}
$('#export-open').onclick=async()=>{try{await flushEdits();$('#export-path').value=state.project.folder.replace(/[/\\]$/,'')+'/exports/Moon-LRGB.'+$('#format').value;$('#export-coverage').value=state.project.edits.coverage;$('#export-coverage').disabled=!state.project.match;$('#export-layer option[value=colour]').disabled=!state.project.match;if(!state.project.match&&$('#export-layer').value==='colour')$('#export-layer').value='composite';$('#export-dialog').showModal();exportPreview();}catch(e){message(e.message);}};
$('#format').onchange=()=>{$('#export-path').value=$('#export-path').value.replace(/\.[^.\/\\]+$/,'.'+$('#format').value);exportPreview();};
for(const selector of ['#export-scale','#export-layer','#export-coverage'])$(selector).onchange=exportPreview;
$('#export-run').onclick=async()=>{if(await action('export',{output:$('#export-path').value,factor:Number($('#export-scale').value),layer:$('#export-layer').value,coverage:$('#export-coverage').value})){ $('#export-dialog').close();showPage('build'); }};
$('#export-dialog form').onsubmit=e=>{e.preventDefault();};
$$('[data-export-close]').forEach(b=>b.onclick=()=>$('#export-dialog').close());
$('#manual-open').onclick=()=>{clearPoints();$('#manual-l').src=`/api/native-preview?id=${state.project.id}&set=L`;$('#manual-rgb').src=`/api/native-preview?id=${state.project.id}&set=RGB`;$('#manual-dialog').showModal();};
$$('[data-points]').forEach(box=>box.onclick=e=>{const key=box.dataset.points, expected=points.L.length===points.RGB.length?'L':'RGB';if(key!==expected){message(`Choose the corresponding point in ${expected==='L'?'luminance':'colour'} first.`);return;}const rect=$('img',box).getBoundingClientRect();const u=(e.clientX-rect.left)/rect.width,v=(e.clientY-rect.top)/rect.height;if(u<0||u>1||v<0||v>1)return;const s=state.project.sets[key];points[key].push([u*s.width,v*s.height]);const dot=document.createElement('span');dot.className='point-dot';dot.style.left=u*100+'%';dot.style.top=v*100+'%';dot.textContent=points[key].length;box.append(dot);$('#point-count').textContent=key==='L'?`Choose point ${points.L.length} in colour.`:`${points.L.length} pairs chosen. Add another pair or use these points.`;});
$('#clear-points').onclick=clearPoints;$('#manual-run').onclick=async()=>{if(await action('manual',{l_points:points.L,rgb_points:points.RGB})){ $('#manual-dialog').close();refreshPreview(); }};
document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='s'){e.preventDefault();flushEdits().then(()=>message('Project saved locally.')).catch(err=>message(err.message));}if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='e'){e.preventDefault();if(!$('#export-open').disabled)$('#export-open').click();}});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('#match-popover').hidden)$('#match-popover-close').click();});
window.addEventListener('beforeunload',e=>{if(pendingEdits||saving||importing){e.preventDefault();e.returnValue='';}});
api('state').then(s=>{state=s;render();showPage(landingPage());}).catch(e=>message(e.message));setInterval(poll,1200);
