/* The investigation workspace shares the local backend with MCP. */
(() => {
  const el = id => document.getElementById(id);
  const state = {id:null, title:'', rounds:[], selected:null, generation:0, timer:null, sending:false, loading:false, polling:false};
  const statuses = {running:'正在采集与整理', completed:'本轮已完成', partial:'本轮部分完成', failed:'本轮未完成', cancelled:'已停止', interrupted:'服务重启，本轮已中断'};
  const errorText = {no_evidence:'本轮没有可供整理的资料。', no_model:'本轮未生成简答，可检查模型配置并查看原始资料。', TimeoutError:'整理超时，原始资料仍可查看。', ValueError:'回答未能通过引用检查，原始资料仍可查看。'};
  const makeKey = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const referenceKey = ref => `${ref.object_id}:${ref.version_id}`;
  const selectedRound = () => state.rounds.find(round => round.id === state.selected);
  const activeRound = () => [...state.rounds].reverse().find(round => round.status === 'running');
  const shortDate = value => {if(!value)return '时间未知';const date=new Date(value);return Number.isNaN(date.valueOf())?String(value).slice(0,16):date.toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false})};
  const stopPolling = () => { if(state.timer) clearTimeout(state.timer); state.timer=null; };
  function controls(){
    el('invSend').disabled=state.sending||state.loading;
    el('invHistory').disabled=state.sending||state.loading;
    el('newResearch').disabled=state.sending||state.loading||researchBusy;
    el('invStop').hidden=!activeRound();
    el('invSend').textContent=state.sending?'正在提交':state.id?'发送追问':'开始调查';
    el('invMessage').placeholder=state.id?'沿着这次发现，你还想了解什么？':'你想了解什么？';
  }
  function renderAnswer(answer){
    if(!answer) return '<p>资料正在汇集，完成后可查看整理结果。</p>';
    if(answer.status !== 'completed') return `<p>${esc(errorText[answer.reason] || (answer.status==='unavailable'?'本轮没有可供整理的资料。':'本轮回答未生成，仍可查看原始资料。'))}</p>`;
    const cites = new Map((answer.citations||[]).map(c=>[String(c.number),c]));
    return `<p>${String(answer.text||'').split(/(\[\d+\])/g).map(part=>{
      const match=part.match(/^\[(\d+)\]$/), cite=match&&cites.get(match[1]);
      return cite ? `<button class="inv-citation" type="button" data-ref="${esc(referenceKey(cite.reference))}" aria-label="查看引用 ${esc(match[1])}">${esc(part)}</button>` : esc(part);
    }).join('')}</p>`;
  }
  function renderRounds(){
    if(!state.rounds.length){
      el('invRounds').innerHTML='<div class="inv-empty"><span class="inv-empty-mark">◌</span><h3>让每一次发现，都能继续深入。</h3><p>输入问题，或指定公开网址作为调查入口。</p></div>';
      return;
    }
    el('invRounds').innerHTML=state.rounds.map(round=>{
      const answer=round.answer || (round.status==='running'?null:{status:'unavailable',reason:round.events.length?'no_model':'no_evidence'});
      const status=statuses[round.status]||'状态未知';
      const reason=round.status==='partial'?'部分来源或内容未覆盖，请结合原文查看。':round.status==='failed'?'采集未完成，已保存的资料仍可查看。':'';
      return `<article class="inv-round" data-round="${esc(round.id)}"><div class="inv-round-head"><h3>${esc(round.message)}</h3><span>${esc(status)}</span></div><div class="inv-answer">${renderAnswer(answer)}</div><div class="inv-round-foot"><span class="${round.status==='running'?'inv-progress':''}">${esc(reason || `${round.evidence.size} 条资料 · ${shortDate(round.created_at)}`)}</span></div></article>`;
    }).join('');
    el('invRounds').querySelectorAll('.inv-round').forEach(card=>{
      card.addEventListener('click',event=>{
        const round=state.rounds.find(item=>item.id===card.dataset.round);
        if(!round)return;
        state.selected=round.id;el('invStatus').textContent=statuses[round.status]||'调查已保存';renderEvidence();
        const citation=event.target.closest('.inv-citation');
        if(citation){
          const detail=[...el('invEvidence').querySelectorAll('details')].find(item=>item.dataset.ref===citation.dataset.ref);
          if(detail){detail.open=true;detail.scrollIntoView({behavior:'smooth',block:'nearest'});detail.querySelector('summary').focus()}
        }
      });
    });
  }
  function renderEvidence(){
    const round=selectedRound(), box=el('invEvidence');
    const open=new Set([...box.querySelectorAll('details[open]')].map(detail=>detail.dataset.ref));
    const entries=round ? [...round.evidence.entries()] : [];
    el('invEvidenceCount').textContent=String(entries.length);
    if(!entries.length){box.innerHTML='<p class="inv-evidence-empty">资料会在调查过程中陆续出现。</p>';return}
    box.innerHTML=entries.map(([key,entry])=>{
      const snapshot=entry.snapshot || {}, origin=entry.origin||{}, title=snapshot.title||((snapshot.type==='reply')?'讨论回复':'原始资料');
      const source=origin.kind==='reused'?'历史资料':origin.kind==='collected'?'本轮采集':'资料';
      const url=safeLink(snapshot.url||''), text=snapshot.text||snapshot.title|| (entry.error?'资料暂时无法读取。':'正在读取原文…');
      const level=snapshot.content_level==='search_result'?' · 搜索摘要':'';
      return `<details class="inv-source" data-ref="${esc(key)}" ${open.has(key)?'open':''}><summary><span class="inv-source-title">${esc(title)}</span><span class="inv-source-meta">${esc(source)}${esc(level)} · ${esc(shortDate(origin.observed_at))}</span></summary><div class="inv-source-body"><div class="inv-source-text">${esc(text)}</div>${entry.error?'<button class="btn inv-retry" type="button">重新读取</button>':''}${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">查看原始出处 ↗</a>`:''}</div></details>`;
    }).join('');
    box.querySelectorAll('.inv-retry').forEach(button=>button.onclick=async()=>{
      const key=button.closest('details').dataset.ref, entry=round.evidence.get(key);
      button.disabled=true;await readEvidence(round,entry.reference,entry.origin,state.generation);renderEvidence();
    });
  }
  function render(){
    el('invTitle').textContent=state.title||'从一个问题开始';
    const round=selectedRound();
    el('invStatus').textContent=round ? (statuses[round.status]||'调查已保存') : '发现公开资料，沿着线索继续追问。';
    renderRounds();renderEvidence();controls();
  }
  async function loadHistory(){
    try{
      const data=await api('/api/investigations?limit=100');
      el('invHistory').innerHTML='<option value="">新调查</option>'+(data.investigations||[]).map(item=>`<option value="${esc(item.id)}">${esc(item.question)}</option>`).join('');
      el('invHistory').value=state.id||'';
    }catch(error){el('invStatus').textContent='调查记录暂时无法读取，请检查服务。'}
  }
  async function readEvidence(round,ref,origin,generation){
    const key=referenceKey(ref), old=round.evidence.get(key);
    if(old){old.origin=origin||old.origin;if(old.snapshot)return;if(old.promise)return old.promise}
    const entry=old||{reference:ref,origin:origin||{},snapshot:null};round.evidence.set(key,entry);
    entry.error=false;
    entry.promise=(async()=>{
      try{
        const result=await api(`/api/evidence/${encodeURIComponent(ref.object_id)}?version=${encodeURIComponent(ref.version_id)}`);
        if(generation===state.generation)entry.snapshot=result.version;
      }catch(error){entry.error=true}
      finally{entry.promise=null}
    })();
    return entry.promise;
  }
  async function readRound(round,generation){
    if(round.readPromise)return round.readPromise;
    round.readPromise=(async()=>{
    let more=true;round.needsMore=true;
    while(more && generation===state.generation){
      const data=await api(`/api/investigation-rounds/${encodeURIComponent(round.id)}?after=${round.cursor}`);
      if(generation!==state.generation)return;
      Object.assign(round,data.round);round.answer=data.answer;
      const events=data.events||[];
      await Promise.all(events.flatMap(event=>{
        round.cursor=Math.max(round.cursor,event.seq);round.events.push(event);
        if(event.type!=='evidence' && event.type!=='evidence_refreshed')return [];
        return (event.references||[]).map(ref=>readEvidence(round,ref,event.origin,generation));
      }));
      more=events.length===100;
    }
    if(generation===state.generation)round.needsMore=false;
    })();
    try{return await round.readPromise}finally{round.readPromise=null}
  }
  async function poll(generation=state.generation){
    if(generation!==state.generation)return;
    if(state.polling){stopPolling();state.timer=setTimeout(()=>poll(generation),250);return}
    state.polling=true;
    try{
      await Promise.all(state.rounds.filter(round=>round.status==='running'||round.needsMore).map(round=>readRound(round,generation)));
      if(generation===state.generation)render();
    }catch(error){if(generation===state.generation)el('invStatus').textContent='进度暂时无法读取，正在重试。'}
    finally{
      state.polling=false;
      if(generation===state.generation && state.rounds.some(round=>round.status==='running'||round.needsMore)){stopPolling();state.timer=setTimeout(()=>poll(generation),1500)}
    }
  }
  async function openSession(id){
    stopPolling();const generation=++state.generation;
    state.id=id;state.title='';state.rounds=[];state.selected=null;state.loading=!!id;render();
    if(!id){el('invHistory').value='';el('invMessage').focus();return}
    el('invStatus').textContent='正在读取调查记录…';
    let loaded=false;
    try{
      const data=await api(`/api/investigations/${encodeURIComponent(id)}`);
      if(generation!==state.generation)return;
      state.title=data.investigation.question;
      state.rounds=(data.rounds||[]).map(round=>({...round,cursor:0,events:[],evidence:new Map(),answer:null}));
      loaded=true;
      state.selected=state.rounds.at(-1)?.id||null;
      await Promise.all(state.rounds.map(round=>readRound(round,generation)));
      if(generation!==state.generation)return;
      render();poll(generation);
    }catch(error){if(generation===state.generation){
      if(loaded){el('invStatus').textContent='部分资料读取失败，正在重试。';poll(generation)}
      else{state.id=null;el('invHistory').value='';el('invStatus').textContent='调查记录读取失败，请重新选择。'}
    }}
    finally{if(generation===state.generation){state.loading=false;controls()}}
  }
  async function submit(event){
    event.preventDefault();if(state.sending||state.loading)return;
    const message=el('invMessage').value.trim();if(!message){el('invMessage').focus();return}
    const seeds=el('invSeeds').value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
    if(seeds.length>6){toast('最多指定 6 个公开网址。');return}
    state.sending=true;controls();
    const generation=state.generation;
    try{
      if(!state.id){const created=await api('/api/investigations',{question:message});if(generation!==state.generation)return;state.id=created.investigation_id;state.title=message;await loadHistory()}
      if(generation!==state.generation)return;
      const id=state.id;
      const data=await api(`/api/investigations/${encodeURIComponent(id)}/rounds`,{message,request_key:makeKey(),seeds});
      if(generation!==state.generation)return;
      el('invMessage').value='';
      const round={id:data.round_id,message,status:'running',created_at:new Date().toISOString(),cursor:0,events:[],evidence:new Map(),answer:null};
      state.rounds.push(round);state.selected=round.id;render();stopPolling();poll();
    }catch(error){toast('未能开始本轮：'+error.message)}
    finally{state.sending=false;controls()}
  }
  el('invForm').addEventListener('submit',submit);
  el('invMessage').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();el('invForm').requestSubmit()}});
  el('invHistory').addEventListener('change',event=>openSession(event.target.value));
  el('invStop').addEventListener('click',async()=>{
    const round=activeRound();if(!round)return;
    el('invStop').disabled=true;
    try{await api(`/api/investigation-rounds/${encodeURIComponent(round.id)}/stop`,{});if(round.readPromise)await round.readPromise;await readRound(round,state.generation);render()}
    catch(error){toast('停止失败：'+error.message)}finally{el('invStop').disabled=false}
  });
  window.periscopeInvestigation={newSession:()=>{if(state.sending||state.loading)return;setView('investigation');openSession('')}};
  loadHistory();render();
})();
