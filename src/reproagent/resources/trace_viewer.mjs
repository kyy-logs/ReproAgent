// Fixed offline module. Trace values are only rendered as text.
export function isFailure(node) {
  const a = node.attributes || {};
  return ['error','failed'].includes(String(node.display_status).toLowerCase()) ||
    ['ERROR','FAIL','FAILED'].includes(String(a.result_code).toUpperCase()) ||
    ['failed','blocked'].includes(a.health);
}

export function computeVisibleRows(view, state, matchedContentKeys = new Set()) {
  const query = (state.query || '').trim().toLowerCase();
  const filtered = !!query || !!state.typeSet?.size || state.errorsOnly || !!state.permissionFilter;
  const included = new Set();
  for (const n of view.nodes) {
    if (state.typeSet?.size && !state.typeSet.has(n.kind_tag)) continue;
    if (state.errorsOnly && !isFailure(n)) continue;
    if (state.permissionFilter && n.permission_result !== state.permissionFilter) continue;
    const metadata = [n.label,n.model,n.source,n.span_id,JSON.stringify(n.attributes)].join(' ').toLowerCase();
    if (query && !metadata.includes(query) && !n.content_keys.some(k => matchedContentKeys.has(k))) continue;
    let k = n.key;
    while (k !== null && k !== undefined && !included.has(k)) {
      included.add(k); k = view.nodes[k].parent_key;
    }
  }
  const rows = [], stack = [...view.roots].reverse();
  while (stack.length) {
    const k = stack.pop(), n = view.nodes[k];
    if (!included.has(k)) continue;
    rows.push(n);
    if (!filtered && state.collapsedKeys.has(k)) continue;
    for (let i=n.child_keys.length-1; i>=0; i--) stack.push(n.child_keys[i]);
  }
  return rows;
}

export function formatStartTime(value) {
  if(value === null || value === undefined || typeof value === 'boolean') return '未记录';
  const d = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(d.getTime()) ? '未记录' : d.toLocaleString()+' '+Intl.DateTimeFormat().resolvedOptions().timeZone;
}

function el(tag, cls, text) {
  const item = document.createElement(tag);
  if (cls) item.className = cls;
  if (text !== undefined) item.textContent = String(text);
  return item;
}
const figure = v => v === null || v === undefined ? '未知 / unknown' : String(v);
const seconds = v => v === null || v === undefined ? '未知' : v === 0 ? '0s' : v < 1 ? (v*1000).toFixed(1)+'ms' : v.toFixed(2)+'s';
function button(text, action, cls) {
  const b = el('button',cls,text); b.type='button'; if (action) b.addEventListener('click',action); return b;
}
function option(select, value, text) {
  const o=el('option','',text); o.value=value; select.append(o);
}

export function mountTraceViewer(root, view) {
  const fallback = document.getElementById('trace-fallback');
  const state = {query:'',typeSet:new Set(),errorsOnly:false,permissionFilter:'',
    collapsedKeys:new Set(view.nodes.filter(n=>n.depth>=2&&n.child_keys.length).map(n=>n.key)),selectedKey:null};
  const captures = new Map(view.contents.map(c=>[c.key,document.getElementById(c.container_id)]));
  const bodyIndex = new Map([...captures].map(([k,e])=>[k,e?.querySelector('[data-content-body]')?.textContent?.toLowerCase() || '']));
  const fragment = document.createDocumentFragment();
  const h = view.header;
  const heading=el('div','trace-heading'); heading.append(el('h1','','ReproAgent Trace'),el('span','pill',h.status),el('span','trace-id',figure(h.trace_id)));
  fragment.append(heading);
  const stats=el('div','trace-stats');
  const start=formatStartTime(h.started_at);
  const metrics=[['开始',start],['主任务',seconds(h.main_duration)],['含学习',seconds(h.total_duration)],
    ['Agent reply',figure(h.agent_replies)],['逻辑调用',figure(h.logical_calls)],['HTTP 尝试',figure(h.http_attempts)],
    ['工具执行',figure(h.tool_executions)],['输入 token · '+h.token_label,figure(h.input_tokens)],
    ['输出 token · '+h.token_label,figure(h.output_tokens)],['总 token',figure(h.total_tokens)],
    ['费用 · '+h.cost_label,figure(h.cost)],['TTFT','未采集'],['缓存命中','未采集']];
  for (const [label,value] of metrics) { const m=el('span','metric',label); m.append(el('b','',value)); stats.append(m); }
  fragment.append(stats);
  if(h.partial || !h.metrics_complete) fragment.append(el('p','warn','incomplete / 基于已保留数据；计数与提示不覆盖未采集步骤。'));
  if(!h.content_complete) fragment.append(el('p','warn','部分正文 missing or was cut short；详情保留截断与缺失原因。'));
  for (const w of h.warnings || []) fragment.append(el('p','warn',w));
  const hints=el('div','trace-hints'); hints.append(el('strong','','运行提示 · 程序规则 '));
  for(const hint of view.hints) hints.append(hint.node_key===null ? el('span','',hint.text+' · ') : button(hint.text,()=>selectNode(hint.node_key)));
  if(!view.hints.length) hints.append(el('span','','当前保留数据无提示'));
  fragment.append(hints);
  const tabs=el('div','trace-tabs'); tabs.setAttribute('role','tablist');
  const callsTab=button('调用树',()=>showTab(false)), decisionsTab=button('验收',()=>showTab(true));
  callsTab.setAttribute('role','tab'); decisionsTab.setAttribute('role','tab');
  callsTab.setAttribute('aria-selected','true'); decisionsTab.setAttribute('aria-selected','false');
  tabs.append(callsTab,decisionsTab); fragment.append(tabs);
  const calls=el('section',''); calls.id='trace-calls-panel';
  const acceptance=el('section',''); acceptance.id='trace-acceptance-panel'; acceptance.hidden=true;
  callsTab.setAttribute('aria-controls',calls.id); decisionsTab.setAttribute('aria-controls',acceptance.id);
  const toolbar=el('div','trace-toolbar');
  const search=el('input',''); search.type='search'; search.placeholder='搜索名称、属性、已保留正文'; search.setAttribute('aria-label','搜索调用');
  let timer;
  search.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>{state.query=search.value;drawRows();},150);});
  const clear=button('清空筛选',()=>{clearTimeout(timer);search.value='';state.query='';state.typeSet.clear();
    for (const cb of typeBoxes) cb.checked=false; errors.checked=false;state.errorsOnly=false;permission.value='';state.permissionFilter='';drawRows();});
  const types=el('details','');types.append(el('summary','','类型'));
  const typeBoxes=[];
  for (const tag of ['ENTRY','AGENT','LLM','HTTP','TOOL','VERIFY','PROCESS','OTHER']) {
    const label=el('label',''); const cb=el('input','');cb.type='checkbox';cb.value=tag;
    cb.addEventListener('change',()=>{cb.checked?state.typeSet.add(tag):state.typeSet.delete(tag);drawRows();});
    label.append(cb,document.createTextNode(tag+' '));types.append(label);typeBoxes.push(cb);
  }
  const errorLabel=el('label',''); const errors=el('input',''); errors.type='checkbox'; errors.id='trace-errors-only';
  errors.addEventListener('change',()=>{state.errorsOnly=errors.checked;drawRows();});errorLabel.append(errors,document.createTextNode('仅失败 / 异常'));
  const permission=el('select','');permission.setAttribute('aria-label','权限结果');
  option(permission,'','全部权限结果'); option(permission,'ALLOWED','ALLOWED · 允许');
  option(permission,'DENIED','DENIED · 拒绝');option(permission,'RESERVED_FOR_PUBLISHING','RESERVED · 发布预留');
  permission.addEventListener('change',()=>{state.permissionFilter=permission.value;drawRows();});
  toolbar.append(search,clear,types,errorLabel,permission);calls.append(toolbar);
  const workspace=el('div','trace-workspace'), tree=el('div','trace-tree'), detail=el('aside','trace-detail'); detail.hidden=true; detail.setAttribute('aria-label','节点详情');
  const axis=el('div','axis-row');axis.append(el('span','',view.time_axis.label));
  const ticks=el('div','axis-ticks'); const extent=view.time_axis.extent_seconds;
  for(const fraction of [0,.25,.5,.75,1]) ticks.append(el('span','',extent===null?'—':seconds(extent*fraction)));
  axis.append(ticks);tree.append(axis);
  const rows=el('div',''); rows.id='trace-rows'; tree.append(rows);workspace.append(tree,detail);calls.append(workspace);
  fragment.append(calls,acceptance,el('p','trace-footer','本地离线查看 · 无新增模型调用 / token · 仅展示已记录正文与程序验收，不补写思考过程'));

  function showTab(decisions) { calls.hidden=decisions;acceptance.hidden=!decisions;
    callsTab.setAttribute('aria-selected',String(!decisions));decisionsTab.setAttribute('aria-selected',String(decisions)); }
  function restoreCaptures() { const registry=fallback.querySelector('#calls'); for(const item of captures.values()) if(item) registry.append(item); }
  function closeDetail() { const key=state.selectedKey; restoreCaptures();detail.replaceChildren();detail.hidden=true;
    workspace.classList.remove('has-detail');state.selectedKey=null;drawRows();rows.querySelector('[data-select-key="'+key+'"]')?.focus(); }
  function selectNode(key) {
    const n=view.nodes[key]; if(!n)return;
    showTab(false);restoreCaptures();detail.replaceChildren();state.selectedKey=key;
    detail.hidden=false;workspace.classList.add('has-detail');
    const head=el('div','detail-head');head.append(el('h3','',n.label),button('关闭',closeDetail));detail.append(head);
    const fields=el('dl','detail-fields');
    const pairs=[['Span ID',n.span_id],['实际父节点',figure(n.actual_parent_span_id)],['来源',n.source],
      ['展示状态',n.display_status],['原始 OTel 状态',figure(n.otel_status)],['开始偏移',seconds(n.offset_seconds)],
      ['耗时',seconds(n.duration_seconds)],['模型',figure(n.model)]];
    if(n.missing_parent)pairs.push(['父节点','parent not retained']);
    if(n.permission_result)pairs.push(['权限结果',n.permission_result]);
    for(const [label,value] of pairs)fields.append(el('dt','',label),el('dd','',value));
    detail.append(fields,el('h4','','受控属性 / 程序依据'),el('pre','',JSON.stringify(n.attributes,null,2)));
    for(const k of n.content_keys) { const c=captures.get(k); if(c) {c.open=true;detail.append(c);} }
    if(!n.content_keys.length)detail.append(el('p','unknown','未保留正文；不补写推理、参数或结果。'));
    drawRows();head.querySelector('button').focus();
  }
  detail.addEventListener('keydown',e=>{if(e.key==='Escape')closeDetail();});
  function drawRows() {
    const q=state.query.trim().toLowerCase(), matched=new Set();
    if(q)for(const [k,text]of bodyIndex)if(text.includes(q))matched.add(k);
    const visible=computeVisibleRows(view,state,matched), frag=document.createDocumentFragment();
    const filtered=!!q||state.typeSet.size||state.errorsOnly||!!state.permissionFilter;
    for(const n of visible) {
      const row=el('div','trace-row node-'+n.kind_tag+(n.key===state.selectedKey?' selected':''));
      const label=el('div','node-label'); label.style.paddingLeft=Math.min(n.depth,12)*12+'px';
      const expanded=filtered||!state.collapsedKeys.has(n.key);
      const toggle=button(n.child_keys.length?(expanded?'⌄':'›'):'·',()=>{state.collapsedKeys.has(n.key)?state.collapsedKeys.delete(n.key):state.collapsedKeys.add(n.key);drawRows();rows.querySelector('[data-toggle-key="'+n.key+'"]')?.focus();},'node-toggle');
      toggle.dataset.toggleKey=n.key;toggle.disabled=!n.child_keys.length;toggle.setAttribute('aria-label',(expanded?'折叠 ':'展开 ')+n.label);
      if(n.child_keys.length)toggle.setAttribute('aria-expanded',String(expanded));
      const select=button('',()=>selectNode(n.key),'node-select');select.dataset.selectKey=n.key;
      select.append(el('span','node-badge',n.kind_tag),el('span','node-name',n.label));
      if(n.model)select.append(el('span','node-meta','model: '+n.model));
      if(isFailure(n))select.append(el('span','status-error','失败'));
      if(n.permission_result)select.append(el('span',n.permission_result==='DENIED'?'permission-denied':n.permission_result==='RESERVED_FOR_PUBLISHING'?'permission-reserved':'node-meta',n.permission_result));
      if(n.missing_parent)select.append(el('span','node-meta','parent not retained'));
      label.append(toggle,select);
      const waterfall=el('div','waterfall'); waterfall.append(el('span','node-duration',seconds(n.duration_seconds)));
      const track=el('div','bar-track');
      if(Number.isFinite(n.start_percent)&&(n.is_point||Number.isFinite(n.width_percent))) {
        const bar=el('span','time-bar'+(n.is_point||n.duration_seconds===0?' point':''));
        bar.style.left=Math.max(0,Math.min(100,n.start_percent))+'%';
        if(!n.is_point)bar.style.width=Math.max(0,Math.min(100,n.width_percent))+'%';
        bar.title='偏移 '+seconds(n.offset_seconds)+' · 耗时 '+seconds(n.duration_seconds);track.append(bar);
      } else track.append(el('span','bar-unknown','无完整时间数据'));
      waterfall.append(track);row.append(label,waterfall);frag.append(row);
    }
    if(!visible.length)frag.append(el('p','trace-empty','没有匹配的调用。调整或清空筛选。'));
    rows.replaceChildren(frag);
  }
  drawRows();root.replaceChildren(fragment);
  // Move the static acceptance section, preserving a single set of check elements.
  const decisions=fallback.querySelector('#decisions'); if(decisions)acceptance.append(decisions);
  root.hidden=false;fallback.hidden=true;
}

if (typeof document !== 'undefined') {
  const root=document.getElementById('trace-app'), fallback=document.getElementById('trace-fallback');
  try { mountTraceViewer(root,JSON.parse(document.getElementById('trace-view-data').textContent)); }
  catch (error) {
    // Failed enhancement must leave the original static report available.
    const decisions=document.getElementById('decisions');if(decisions&&!fallback.contains(decisions))fallback.append(decisions);
    const registry=fallback.querySelector('#calls');
    for(const capture of document.querySelectorAll('.capture'))if(registry&&!fallback.contains(capture))registry.append(capture);
    root.hidden=true;fallback.hidden=false;
    console.error('Trace viewer initialization failed',error);
  }
}
