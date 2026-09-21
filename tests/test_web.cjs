// Run with: node --test tests/test_web.cjs
// Tests the shipped scripts with a small DOM adapter; no browser or model is started.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness() {
  const nodes = new Map(), documentEvents = new Map();
  function element(id) {
    if (!nodes.has(id)) {
      const events = new Map();
      nodes.set(id, {id,value:'',innerHTML:'',textContent:'',hidden:false,disabled:false,checked:false,open:false,inert:false,style:{},dataset:{},title:'',src:'',currentSrc:'',readyState:0,currentTime:0,duration:20,paused:true,volume:1,muted:false,playbackRate:1,defaultPlaybackRate:1,
        classList:{add(){},remove(){},toggle(){}},setAttribute(){},getAttribute(){return this.src},removeAttribute(){},querySelector(){return null},querySelectorAll(){return []},getBoundingClientRect(){return {width:640,height:360,left:0,top:0}},scrollIntoView(){},focus(){},load(){},pause(){this.paused=true},play(){this.paused=false;return Promise.resolve()},
        addEventListener(type,callback){if(!events.has(type))events.set(type,[]);events.get(type).push(callback)},
        async emit(type,event={}){await Promise.all((events.get(type)||[]).map(callback=>callback({preventDefault(){},stopPropagation(){},target:this,...event})))},
        showModal(){this.open=true},close(){this.open=false;this.emit('close')}});
    }
    return nodes.get(id);
  }
  const window = {location:{href:'http://127.0.0.1:47840/'},history:{replaceState(_a,_b,url){window.location.href=String(url)}},confirm:()=>true,addEventListener(){}};
  const document = {getElementById:element,querySelectorAll:()=>[],querySelector:selector=>selector==='.inspector'?element('inspector'):null,addEventListener(type,callback){if(!documentEvents.has(type))documentEvents.set(type,[]);documentEvents.get(type).push(callback)}};
  const context = vm.createContext({console,URL,window,document,ResizeObserver:class{observe(){}},setTimeout:()=>1,clearTimeout(){},performance:{now:()=>0},matchMedia:()=>({matches:true}),requestAnimationFrame:callback=>callback(),crypto:{randomUUID:()=> 'test-region'},fetch(){throw Error('unexpected real request')}});
  const base = path.join(__dirname,'../src/framepulse/web');
  vm.runInContext(fs.readFileSync(path.join(base,'app.js'),'utf8').replace(/boot\(\);\s*$/,''),context);
  vm.runInContext(fs.readFileSync(path.join(base,'queue.js'),'utf8'),context);
  vm.runInContext('globalThis.s=state;',context);
  return {context,element,window,state:context.s,stubApi(callback){context.stubApi=callback;vm.runInContext('api=globalThis.stubApi;',context)},async emitDocument(type,event){await Promise.all((documentEvents.get(type)||[]).map(callback=>callback(event)))}};
}
function mediaDocument() {
  return {asset:{kind:'video',name:'test.mp4',technical:{width:1920,height:1080,duration_us:20000000,has_audio:true}},shots:[{id:'shot-1',range:{start_us:1000000,end_us:5000000},keyframes:[{id:'frame-1',time_us:2000000}],regions:[{id:'region-1',keyframe_id:'frame-1',xywh:[.1,.2,.3,.4],label:'人物',origin:'human',confidence:null}],analysis:{},emotion:{},review:{state:'unreviewed',note:''}}],spec_version:'0.1.0',revision:1};
}
test('mute, volume and speed control the actual video element',async()=>{
  const h=harness(),v=h.element('video');h.state.document=mediaDocument();
  await h.element('muteButton').emit('click');assert.equal(v.muted,true);
  h.element('volume').value='0.35';await h.element('volume').emit('input');assert.equal(v.volume,.35);assert.equal(v.muted,false);
  h.element('playbackSpeed').value='1.5';await h.element('playbackSpeed').emit('change');assert.equal(v.playbackRate,1.5);assert.equal(v.defaultPlaybackRate,1.5);
});
test('region chips anchor to evidence frames; playing or labels off hides boxes',()=>{
  const h=harness(),v=h.element('video');h.state.document=mediaDocument();h.state.assetId=h.state.loadedAsset='asset';h.state.keyframeId='frame-1';v.src=v.currentSrc='http://127.0.0.1:47840/api/assets/asset/media';v.readyState=1;
  h.context.selectRegion(0);assert.equal(v.currentTime,2);assert.equal(v.paused,true);assert.equal(h.state.activeRegionId,'region-1');assert.match(h.element('regionOverlay').innerHTML,/region-1/);
  v.paused=false;h.context.renderRegions();assert.equal(h.element('regionOverlay').innerHTML,'');
  v.paused=true;h.state.labelsVisible=false;h.context.renderRegions();assert.equal(h.element('regionOverlay').innerHTML,'');
});
test('overview preserves missing values and displays real coverage and media labels',()=>{
  const h=harness();h.state.document=mediaDocument();h.context.renderOverview();assert.match(h.element('overviewBody').innerHTML,/尚无整片语义概括/);
  h.state.document.overview={status:'partial',coverage:{semantic_shots:1,total_shots:3},analysis:{summary:{value:'城市中的人物',origin:'inferred'},visual_medium:{value:['live_action']},tags:{value:['城市']}},warnings:[]};h.context.renderOverview();
  assert.match(h.element('overviewCoverage').textContent,/1 \/ 3/);assert.match(h.element('overviewBody').innerHTML,/实拍/);assert.match(h.element('overviewBody').innerHTML,/城市中的人物/);
});
test('queue pause clearly preserves the current running task',()=>{
  const h=harness();h.state.jobs=[{id:'running',path:'D:/a.mp4',state:'running',profile:'balanced',vision:true,model:'vision-model',progress:{phase:'vision',current:1,total:3}},{id:'pending',path:'D:/b.mp4',state:'pending',progress:{}}];h.state.queue={state:'paused'};
  h.window.framepulseQueue.render();assert.equal(h.element('queuePause').textContent,'恢复队列');assert.equal(h.element('queuePauseNotice').hidden,false);assert.match(h.element('queuePauseNotice').textContent,/正在处理的素材会继续完成/);assert.match(h.element('queueRows').innerHTML,/1 \/ 3 镜头/);
});
test('new remote address switches AI off until explicitly enabled',async()=>{
  const h=harness();h.state.config={provider_url:'http://127.0.0.1:11434/v1',model:'local-model',vision:true};h.element('visionEnabled').checked=true;h.element('providerUrl').value='https://example.test/v1';await h.element('providerUrl').emit('input');assert.equal(h.element('visionEnabled').checked,false);
});
test('settings reopens a saved remote address as compatible, despite original discovery metadata',async()=>{
  const h=harness();h.state.config={provider_url:'https://example.test/v1',provider_source:'local-discovery',model:'remote-vision',vision:true,profile:'balanced'};
  h.stubApi(async()=>({models:[],provider_url:'http://127.0.0.1:11434/v1',errors:[]}));
  await h.element('settingsOpen').emit('click');assert.equal(h.element('providerMode').value,'compatible');assert.equal(h.element('compatibleOptions').hidden,false);
});
test('settings sends a session key once and clears the input without keeping it in config',async()=>{
  const h=harness();h.state.config={provider_url:'https://example.test/v1',model:'vision',profile:'balanced',vision:false};let submitted;
  h.stubApi(async(url,options)=>{assert.equal(url,'/api/config');submitted=JSON.parse(options.body);return {...h.state.config,vision:true,has_api_key:true}});
  h.element('providerMode').value='compatible';h.element('providerUrl').value='https://example.test/v1';h.element('providerModel').value='vision';h.element('settingsProfile').value='balanced';h.element('visionEnabled').checked=true;h.element('providerKey').value='synthetic-test-key';
  await h.element('settingsForm').emit('submit');assert.equal(submitted.api_key,'synthetic-test-key');assert.equal(h.element('providerKey').value,'');assert.equal(h.state.config.api_key,undefined);
});
test('native file drops use the desktop path bridge and add without starting the queue',async()=>{
  const h=harness(),requests=[];let resolved=false;
  h.window.framepulseDesktop={pathsForFiles(files){assert.equal(files[0].name,'clip.mp4');resolved=true;return ['D:/Media/clip.mp4']}};
  h.stubApi(async(url,options)=>{requests.push(url);if(url==='/api/queue/add'){assert.equal(JSON.parse(options.body).paths[0],'D:/Media/clip.mp4');return {jobs:[{id:'new'}],skipped:[]}}if(url==='/api/jobs')return {queue:{state:'idle'},jobs:[{id:'new',path:'D:/Media/clip.mp4',state:'pending',progress:{}}]};throw Error(url)});
  await h.emitDocument('drop',{dataTransfer:{types:['Files'],files:[{name:'clip.mp4'}]},preventDefault(){}});assert.equal(resolved,true);assert.equal(requests.includes('/api/queue/start'),false);assert.equal(h.element('queuePanel').hidden,false);
});
test('import button and native multi-picker send the selected profile with valid queue fields',async()=>{
  const h=harness();h.state.config={profile:'detailed',vision:false,provider_url:'http://127.0.0.1:11434/v1',model:'local-vision',profiles:{balanced:{label:'均衡'},detailed:{label:'细读'}}};
  h.window.framepulseDesktop={pickFiles:async()=>['D:/素材/a.mp4','D:/素材/b.png']};let body;
  h.stubApi(async(url,options)=>{if(url==='/api/queue/add'){body=JSON.parse(options.body);return {jobs:[{id:'a'},{id:'b'}],skipped:[]}}if(url==='/api/jobs')return {jobs:[],queue:{state:'idle'}};throw Error(url)});
  await h.element('importOpen').emit('click');assert.equal(h.element('importDialog').open,true);assert.equal(h.element('importProfile').value,'detailed');
  await h.element('browseButton').emit('click');assert.equal(h.element('sourcePath').value,'D:/素材/a.mp4\nD:/素材/b.png');await h.element('importForm').emit('submit');
  assert.equal(body.profile,'detailed');assert.equal(body.vision,false);assert.equal(body.recursive,true);assert.equal(body.threshold,27);assert.equal(body.paths.length,2);assert.equal(h.element('importDialog').open,false);
});
test('model discovery always requests local Ollama and only enables vision models',async()=>{
  const h=harness();h.state.config={provider_url:'https://example.test/v1',model:'remote',profile:'balanced'};let called;
  h.stubApi(async(url)=>{called=url;return {provider_url:'http://127.0.0.1:11434/v1',models:[{name:'text-only',vision:false},{name:'local-vision',vision:true}],errors:[]}});
  await h.element('settingsOpen').emit('click');assert.equal(new URL(called,'http://localhost').searchParams.get('provider_url'),'http://127.0.0.1:11434/v1');assert.equal(h.element('localModel').value,'local-vision');assert.match(h.element('localModel').innerHTML,/value="text-only" disabled/);
});
test('config leaves an existing key untouched when blank, and sends empty only for explicit clearing',async()=>{
  const h=harness();h.state.config={provider_url:'https://example.test/v1',model:'vision',profile:'balanced',vision:false};const payloads=[];
  h.stubApi(async(_url,options)=>{payloads.push(JSON.parse(options.body));return {...h.state.config,profile:'detailed'}});
  h.element('providerMode').value='compatible';h.element('providerUrl').value=h.state.config.provider_url;h.element('providerModel').value='vision';h.element('settingsProfile').value='detailed';
  await h.element('settingsForm').emit('submit');assert.equal(Object.hasOwn(payloads[0],'api_key'),false);assert.equal(h.state.config.profile,'detailed');
  h.element('clearProviderKey').checked=true;await h.element('settingsForm').emit('submit');assert.equal(payloads[1].api_key,'');
});
test('pause, resume and retry use the agreed endpoints and preserve pending jobs',async()=>{
  const h=harness(),calls=[];h.state.queue={state:'running'};h.state.jobs=[{id:'failed-id',state:'failed',path:'D:/clip.mp4',profile:'detailed',progress:{}}];let mode='paused';
  h.stubApi(async(url)=>{calls.push(url);return url==='/api/jobs'?{queue:{state:mode},jobs:[{id:'failed-id',state:'pending',path:'D:/clip.mp4',profile:'detailed',progress:{phase:'pending'}}]}:{ok:true}});
  await h.element('queuePause').emit('click');assert.equal(calls[0],'/api/queue/pause');mode='running';await h.element('queuePause').emit('click');assert.equal(calls[2],'/api/queue/resume');
  const button={dataset:{queueRetry:'failed-id'},disabled:false};await h.element('queueRows').emit('click',{target:{closest:selector=>selector==='[data-queue-retry]'?button:null}});assert.ok(calls.includes('/api/jobs/failed-id/retry'));assert.equal(h.state.jobs[0].profile,'detailed');
});
test('a newer pending seek replaces earlier requests and rejects stale media events',()=>{
  const h=harness(),v=h.element('video');h.state.document=mediaDocument();h.state.assetId=h.state.loadedAsset='asset';v.src='http://127.0.0.1:47840/api/assets/asset/media';
  h.context.requestSeek(2);h.context.requestSeek(8);v.readyState=1;v.currentSrc='http://127.0.0.1:47840/api/assets/old/media';h.context.applyPendingSeek();assert.equal(v.currentTime,0);
  v.currentSrc=v.src;h.context.applyPendingSeek();assert.equal(v.currentTime,8);assert.equal(h.state.pendingSeek,null);
});
test('refresh keeps actual playback time and warning notes do not replace progress',()=>{
  const h=harness();h.state.document=mediaDocument();h.state.assetId=h.state.loadedAsset='asset';h.element('video').currentTime=8;
  vm.runInContext('renderShotStrip=()=>{};renderInspector=()=>{};renderTimeline=()=>{};fitMedia=()=>{};renderRegions=()=>{};',h.context);h.context.renderAsset(false);assert.equal(h.element('currentTime').textContent,'00:08.000');assert.equal(h.element('seek').value,8);
  h.state.assets=[{id:'asset',name:'clip.mp4',shot_count:3,analyzed_count:3,reviewed_count:0,warnings:['基础说明']}];h.context.renderLibrary();assert.match(h.element('assetList').innerHTML,/3 镜有 AI 记录/);assert.match(h.element('assetList').innerHTML,/title="基础说明"/);
});
test('stage progress displays time, megabytes and counts without changing the ratio',()=>{
  const h=harness();
  assert.equal(h.context.formatProgress({phase:'detect',current:1813708000,total:2063580000}),'30:13 / 34:23');
  assert.equal(h.context.formatProgress({phase:'hash',current:150000000,total:300000000}),'150.0 / 300.0 MB');
  assert.equal(h.context.formatProgress({phase:'vision',current:2,total:5}),'2 / 5 镜头');
  assert.equal(h.context.formatProgress({phase:'keyframes',current:10,total:20}),'10 / 20 帧');
  h.state.jobs=[{id:'detect',path:'D:/long.mp4',state:'running',progress:{phase:'detect',current:1813708000,total:2063580000}}];h.state.queue={state:'running'};h.window.framepulseQueue.render();
  assert.match(h.element('queueRows').innerHTML,/逐帧检测 · 30:13 \/ 34:23/);assert.doesNotMatch(h.element('queueRows').innerHTML,/1813708000/);assert.match(h.element('queueRows').innerHTML,new RegExp('width:'+String(1813708000/2063580000*100).replace('.','\\.')+'%'));
});
test('queue storage failure is prominent and clears after a successful recovery',()=>{
  const h=harness();h.state.queue={state:'paused',error:'无法保存队列，请检查磁盘空间。'};h.window.framepulseQueue.render();
  assert.equal(h.element('queueStorageError').hidden,false);assert.match(h.element('queueStorageError').textContent,/磁盘空间/);assert.equal(h.element('queueStateLabel').textContent,'存储故障 · 队列暂停');
  h.state.queue={state:'running',error:null};h.window.framepulseQueue.render();assert.equal(h.element('queueStorageError').hidden,true);
});

test('collapsed overview shows medium and up to three deduplicated themes with AI provenance',()=>{
  const h=harness();h.state.document=mediaDocument();h.state.document.overview={status:'completed',coverage:{semantic_shots:6,total_shots:6},analysis:{summary:{value:'角色在城市中相遇',origin:'inferred'},visual_medium:{value:['2d_animation','3d_animation'],origin:'inferred'},themes:{value:['相遇','成长'],origin:'inferred'},tags:{value:['成长','城市','第四个标签'],origin:'inferred'}},warnings:[]};
  h.context.renderOverview();
  const header=h.element('overviewSummaryTags').innerHTML;
  assert.equal(h.element('assetOverview').open,false);assert.match(header,/二维动画 \/ 三维动画/);assert.match(header,/相遇/);assert.match(header,/成长/);assert.match(header,/城市/);assert.doesNotMatch(header,/第四个标签/);assert.equal((header.match(/class="overview-tag"/g)||[]).length,3);
  assert.match(h.element('overviewBody').innerHTML,/第四个标签/);assert.match(h.element('overviewCoverage').textContent,/6 \/ 6 镜 · AI 推断/);assert.doesNotMatch(h.element('overviewCoverage').textContent,/已确认/);assert.match(h.element('overviewCoverage').title,/分析完成/);
  delete h.state.document.overview;h.context.renderOverview();assert.equal(h.element('overviewSummaryTags').innerHTML,'');
});

test('AI region labels preserve inference provenance on both chips and evidence overlay',()=>{
  const h=harness();h.state.document=mediaDocument();h.state.document.shots[0].regions[0].origin='inferred';h.state.keyframeId='frame-1';h.element('video').currentTime=2;h.context.renderRegions();h.context.renderRegionChips();
  assert.match(h.element('regionOverlay').innerHTML,/人物 · AI 推断/);assert.match(h.element('regionChips').innerHTML,/region-source">AI 推断/);assert.doesNotMatch(h.element('regionChips').innerHTML,/已确认/);
  h.state.document.shots[0].regions[0].origin='human';h.context.renderRegionChips();assert.match(h.element('regionChips').innerHTML,/region-source">人工/);
});
