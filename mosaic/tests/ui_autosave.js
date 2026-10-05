'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const settle = () => new Promise(resolve => setImmediate(resolve));

async function editor() {
  const nodes = new Map(), groups = new Map(), timers = new Map();
  let timerId = 0;
  function element(selector) {
    if (!nodes.has(selector)) nodes.set(selector, {
      value:'', textContent:'', dataset:{}, style:{}, hidden:false,
      classList:{add(){},remove(){},toggle(){}},
      append(){}, replaceChildren(){}, setAttribute(){},
      querySelector:element, querySelectorAll:s=>groups.get(s)||[],
      parentElement:{querySelector:element},
    });
    return nodes.get(selector);
  }
  const controls = ['saturation','smoothing','strength'].map(key => {
    const el = element(`[data-edit="${key}"]`); el.dataset.edit=key; return el;
  });
  const waves = Array.from({length:4}, (_,i) => {
    const el = element(`[data-wave="${i}"]`); el.dataset.wave=String(i); return el;
  });
  groups.set('[data-edit]', controls);
  groups.set('[data-wave]', waves);
  const requests = [];
  const server = {project:{id:'autosave-test',name:'Test Moon',folder:'/test/moon',revision:1,
    sets:Object.fromEntries(['L','RGB'].map(key=>[key,{state:'empty',mosaic:null,inputs:[],optics:{}}])),
    match:null,orientation:null,edits:null},job:{state:'idle'}};
  const harness = {server,requests,offline:false};
  const response = (data,ok=true) => ({ok,json:async()=>structuredClone(data)});
  const context = vm.createContext({
    console, structuredClone, URLSearchParams,
    document:{querySelector:element,querySelectorAll:s=>groups.get(s)||[],
      createElement:()=>element(Symbol()),createTextNode:text=>({textContent:text}),
      body:{classList:{add(){},remove(){},toggle(){}}},documentElement:{},addEventListener(){}},
    window:{addEventListener(){}},
    setTimeout:fn=>{timers.set(++timerId,fn);return timerId;},
    clearTimeout:id=>timers.delete(id),setInterval(){},
    fetch:async(url,options)=>{
      if (url==='/api/state') {
        if (harness.offline) throw new Error('Connection lost');
        return response(server);
      }
      assert.equal(url,'/api/edits');
      return new Promise((resolve,reject)=>requests.push({body:JSON.parse(options.body),resolve,reject}));
    },
  });
  const source = fs.readFileSync(path.join(__dirname,'../ui/mosaic.js'),'utf8');
  // Run the complete shipped script, including input handlers, rendering and fetch parsing.
  vm.runInContext(source, context, {filename:'mosaic.js'});
  server.project.edits = JSON.parse(vm.runInContext('JSON.stringify(defaultEdits)',context));
  await settle();
  assert.equal(Number(controls[0].value),1,'The editor must finish loading before the first input');

  harness.input = (key,value) => {
    const el=element(`[data-edit="${key}"]`);el.value=String(value);el.oninput();
  };
  harness.wave = (index,value) => {waves[index].value=String(value);waves[index].oninput();};
  harness.flush = () => vm.runInContext('flushEdits()',context);
  harness.poll = () => vm.runInContext('poll()',context);
  harness.value = key => Number(element(`[data-edit="${key}"]`).value);
  harness.waveValues = () => waves.map(el=>Number(el.value));
  harness.saved = () => element('#saved').textContent;
  harness.accept = index => {
    const request=requests[index];assert.ok(request,'Expected a save request');
    assert.equal(request.body.revision,server.project.revision);
    server.project.edits=structuredClone(request.body.edits);server.project.revision++;
    request.resolve(response(server));
  };
  harness.conflict = index => {
    server.project.revision++;
    requests[index].resolve(response({error:'Revision conflict'},false));
  };
  return harness;
}

async function retry(h,index,expected) {
  const saving=h.flush();
  assert.equal(h.requests.length,index+1);
  for (const [key,value] of Object.entries(expected)) assert.deepEqual(h.requests[index].body.edits[key],value);
  h.accept(index);await saving;
  assert.equal(h.saved(),'Saved locally');
  for (const [key,value] of Object.entries(expected)) assert.deepEqual(h.server.project.edits[key],value);
}

const scenarios = {
  async single_failure() {
    const h=await editor();h.input('saturation',2);
    const saving=h.flush(), failed=assert.rejects(saving,/Revision conflict/);
    h.conflict(0);await failed;
    assert.equal(h.value('saturation'),2);assert.match(h.saved(),/^Not saved/);
    await retry(h,1,{saturation:2});
  },
  async different_fields_failure() {
    const h=await editor();h.input('saturation',2);
    const saving=h.flush(), failed=assert.rejects(saving,/Revision conflict/);
    h.input('smoothing',3);h.conflict(0);await failed;
    assert.equal(h.value('saturation'),2);assert.equal(h.value('smoothing'),3);
    assert.match(h.saved(),/^Not saved/);
    await retry(h,1,{saturation:2,smoothing:3});
  },
  async different_fields_success() {
    const h=await editor();h.input('saturation',2);
    const saving=h.flush();h.input('smoothing',3);h.accept(0);await settle();
    assert.equal(h.requests.length,2);
    assert.equal(h.requests[1].body.edits.saturation,2);
    assert.equal(h.requests[1].body.edits.smoothing,3);
    assert.equal(h.requests[0].body.edits.smoothing,0,'The submitted snapshot must not change');
    h.accept(1);await saving;
    assert.equal(h.value('saturation'),2);assert.equal(h.value('smoothing'),3);
    assert.equal(h.saved(),'Saved locally');
  },
  async same_field() {
    for (const value of [4,1]) {
      const h=await editor();h.input('saturation',2);
      const saving=h.flush(), failed=assert.rejects(saving,/Revision conflict/);
      h.input('saturation',value);h.conflict(0);await failed;
      await retry(h,1,{saturation:value});
      assert.equal(h.value('saturation'),value);
    }
  },
  async offline() {
    const h=await editor();h.input('saturation',2);
    const saving=h.flush(), failed=assert.rejects(saving,/Connection lost/);
    h.input('smoothing',3);h.offline=true;
    h.requests[0].reject(new Error('Connection lost'));await failed;
    assert.match(h.saved(),/^Not saved/);h.offline=false;
    await retry(h,1,{saturation:2,smoothing:3});
  },
  async wavelets() {
    for (const conflict of [false,true]) {
      const h=await editor();h.wave(0,.6);
      const saving=h.flush(), failed=conflict?assert.rejects(saving,/Revision conflict/):null;
      h.wave(1,1.1);
      if (conflict) {
        h.conflict(0);await failed;
        await retry(h,1,{wavelets:[.6,1.1,0,0]});
      } else {
        h.accept(0);await settle();
        assert.deepEqual(h.requests[1].body.edits.wavelets,[.6,1.1,0,0]);
        h.accept(1);await saving;
      }
      assert.deepEqual(h.requests[0].body.edits.wavelets,[.6,0,0,0]);
      assert.deepEqual(h.waveValues(),[.6,1.1,0,0]);
    }
  },
  async later_edit() {
    const h=await editor();h.input('saturation',2);
    const saving=h.flush();h.accept(0);await saving;
    h.server.project.edits.strength=.4;h.server.project.revision++;
    await h.poll();h.input('smoothing',3);
    await retry(h,1,{saturation:2,smoothing:3,strength:.4});
  },
};

if (require.main===module) {
  const name=process.argv[2];
  if (!Object.hasOwn(scenarios,name)) throw new Error('Choose an autosave scenario');
  scenarios[name]().then(()=>console.log(`PASS ${name}`)).catch(error=>{console.error(error);process.exitCode=1;});
}
