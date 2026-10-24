/* Interactive optical archive: real beveled meshes, perspective, glass and a cobalt core. */
(() => {
  const hero = document.querySelector('#hero');
  const canvas = document.querySelector('#ambientLight');
  const layer = document.querySelector('#ambientBackdrop');
  const fallback = document.querySelector('.ambient-fallback');
  const motionButton = document.querySelector('#ambientMotionToggle');
  const rotateButton = document.querySelector('#sceneRotate');
  const expandButton = document.querySelector('#sceneExpand');
  if (!hero || !canvas || !layer) return;
  const gl = canvas.getContext('webgl', { alpha: false, antialias: true, powerPreference: 'low-power' });
  let lost = false;
  function showFallback() {
    lost = true; canvas.hidden = true; canvas.dataset.motion = 'static';
    if (fallback) fallback.hidden = false;
    const hint = document.querySelector('.art-caption > span');
    if (hint) hint.textContent = '当前显示静态档案。';
    for (const button of [motionButton, rotateButton, expandButton]) if (button) button.disabled = true;
    if (motionButton) motionButton.textContent = '静态展示';
  }
  if (!gl) { showFallback(); return; }

  function program(vertexSource, fragmentSource) {
    const shaders = [[gl.VERTEX_SHADER, vertexSource], [gl.FRAGMENT_SHADER, fragmentSource]].map(([type, source]) => {
      const shader = gl.createShader(type); gl.shaderSource(shader, source); gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) { gl.deleteShader(shader); return null; }
      return shader;
    });
    if (shaders.some(shader => !shader)) { shaders.filter(Boolean).forEach(shader => gl.deleteShader(shader)); return null; }
    const result = gl.createProgram(); shaders.forEach(shader => gl.attachShader(result, shader)); gl.linkProgram(result);
    shaders.forEach(shader => gl.deleteShader(shader));
    if (!gl.getProgramParameter(result, gl.LINK_STATUS)) { gl.deleteProgram(result); return null; }
    return result;
  }
  const surface = program(`
    attribute vec3 aPosition; attribute vec3 aNormal;
    uniform mat4 uModel; uniform mat4 uCamera;
    varying vec3 vWorld; varying vec3 vNormal;
    void main(){ vec4 world=uModel*vec4(aPosition,1.); vWorld=world.xyz;
      vNormal=normalize(mat3(uModel)*aNormal); gl_Position=uCamera*world; }
  `, `
    precision highp float;
    varying vec3 vWorld; varying vec3 vNormal;
    uniform vec3 uColor; uniform float uOpacity; uniform float uTime; uniform float uFlat; uniform float uDistance;
    void main(){
      vec3 n=normalize(vNormal); if(!gl_FrontFacing)n=-n;
      vec3 eye=normalize(vec3(0.,0.,uDistance)-vWorld);
      vec3 light=normalize(vec3(-2.8,4.4,5.));
      vec3 fill=normalize(vec3(4.,1.,-2.));
      float diffuse=max(dot(n,light),0.);
      float rim=pow(1.-max(dot(n,eye),0.),3.);
      vec3 reflection=reflect(-eye,n);
      float softbox=pow(max(dot(reflection,normalize(vec3(-.6,1.,1.8))),0.),18.);
      float strip=pow(max(dot(reflection,normalize(vec3(1.6,.5,2.))),0.),90.);
      float moving=pow(max(dot(reflection,normalize(vec3(sin(uTime*.55)*2.,2.,3.))),0.),70.);
      vec3 color=uColor*(.70+.28*diffuse+.12*max(dot(n,fill),0.));
      color=mix(color,vec3(.13,.42,.86),rim*.46);
      color+=vec3(.92,.97,1.)*(softbox*.52+strip*.70+moving*.20);
      color=mix(color,uColor,uFlat);
      float alpha=mix(min(1.,uOpacity+rim*.40+softbox*.13),uOpacity,uFlat);
      gl_FragColor=vec4(clamp(color,0.,1.),alpha);
    }
  `);
  const backdrop = program(`
    attribute vec2 aPosition; varying vec2 vUv;
    void main(){vUv=aPosition*.5+.5;gl_Position=vec4(aPosition,0.,1.);}
  `, `
    precision highp float; varying vec2 vUv;
    void main(){
      vec2 uv=vec2(vUv.x,1.-vUv.y); vec3 color=vec3(.973,.980,.988);
      float glow=exp(-dot((uv-vec2(.63,.43))/vec2(.36,.38),(uv-vec2(.63,.43))/vec2(.36,.38)));
      color=mix(color,vec3(.86,.92,.99),glow*.42);
      vec2 floor=(uv-vec2(.50,.84))/vec2(.29,.045);
      float shadow=exp(-dot(floor,floor));color=mix(color,vec3(.60,.69,.82),shadow*.23);
      gl_FragColor=vec4(color,1.);
    }
  `);
  if (!surface || !backdrop) { showFallback(); return; }

  const identity = () => new Float32Array([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]);
  function multiply(a,b) {
    const result=new Float32Array(16);
    for(let c=0;c<4;c++)for(let r=0;r<4;r++)for(let k=0;k<4;k++)result[c*4+r]+=a[k*4+r]*b[c*4+k];
    return result;
  }
  const translate=(x,y,z)=>{const m=identity();m[12]=x;m[13]=y;m[14]=z;return m;};
  const rx=a=>{const c=Math.cos(a),s=Math.sin(a);return new Float32Array([1,0,0,0,0,c,s,0,0,-s,c,0,0,0,0,1]);};
  const ry=a=>{const c=Math.cos(a),s=Math.sin(a);return new Float32Array([c,0,-s,0,0,1,0,0,s,0,c,0,0,0,0,1]);};
  const rz=a=>{const c=Math.cos(a),s=Math.sin(a);return new Float32Array([c,s,0,0,-s,c,0,0,0,0,1,0,0,0,0,1]);};
  function perspective(aspect) {
    const f=1/Math.tan(36*Math.PI/360),near=.1,far=40;
    return new Float32Array([f/aspect,0,0,0,0,f,0,0,0,0,(far+near)/(near-far),-1,0,0,2*far*near/(near-far),0]);
  }
  const gpuBuffer = values => { const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(values),gl.STATIC_DRAW);return buffer; };
  function mesh(width,height,depth,bevel) {
    const positions=[],normals=[],lines=[];
    const polygon=(w,h)=>[[-w/2+bevel,-h/2],[w/2-bevel,-h/2],[w/2,-h/2+bevel],[w/2,h/2-bevel],[w/2-bevel,h/2],[-w/2+bevel,h/2],[-w/2,h/2-bevel],[-w/2,-h/2+bevel]];
    const outer=polygon(width,height),inner=polygon(width-bevel*1.4,height-bevel*1.4);
    const point=(poly,i,z)=>[...poly[i%8],z];
    function triangle(a,b,c){
      const u=b.map((v,i)=>v-a[i]),v=c.map((n,i)=>n-a[i]);
      const n=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]];
      const length=Math.hypot(...n)||1;
      positions.push(...a,...b,...c);for(let i=0;i<3;i++)normals.push(...n.map(x=>x/length));
    }
    const quad=(a,b,c,d)=>{triangle(a,b,c);triangle(a,c,d);};
    for(let i=0;i<8;i++){
      triangle([0,0,depth/2],point(inner,i,depth/2),point(inner,i+1,depth/2));
      triangle([0,0,-depth/2],point(inner,i+1,-depth/2),point(inner,i,-depth/2));
      quad(point(inner,i,depth/2),point(outer,i,depth/2-bevel*.65),point(outer,i+1,depth/2-bevel*.65),point(inner,i+1,depth/2));
      quad(point(outer,i,depth/2-bevel*.65),point(outer,i,-depth/2+bevel*.65),point(outer,i+1,-depth/2+bevel*.65),point(outer,i+1,depth/2-bevel*.65));
      quad(point(outer,i,-depth/2+bevel*.65),point(inner,i,-depth/2),point(inner,i+1,-depth/2),point(outer,i+1,-depth/2+bevel*.65));
      lines.push(...point(inner,i,depth/2+.001),...point(inner,i+1,depth/2+.001));
      lines.push(...point(inner,i,-depth/2-.001),...point(inner,i+1,-depth/2-.001));
    }
    return {positions:gpuBuffer(positions),normals:gpuBuffer(normals),count:positions.length/3,lines:gpuBuffer(lines),lineCount:lines.length/3};
  }
  const plate=mesh(1.90,1.90,.23,.09),core=mesh(.82,.82,.82,.12);
  const etch=[];
  for(let i=0;i<3;i++){const y=.56-i*.18;etch.push(-.48,y,.117,i===0?.38:.15+(i%2)*.22,y,.117);}
  etch.push(-.48,-.56,.117,-.31,-.56,.117,.31,-.56,.117,.48,-.56,.117);
  const etched={positions:gpuBuffer(etch),count:etch.length/3};
  const quad=gpuBuffer([-1,-1,1,-1,-1,1,1,1]);
  const surfaceLocations=Object.fromEntries(['uModel','uCamera','uColor','uOpacity','uTime','uFlat','uDistance'].map(name=>[name,gl.getUniformLocation(surface,name)]));
  const position=gl.getAttribLocation(surface,'aPosition'),normal=gl.getAttribLocation(surface,'aNormal');
  const backdropPosition=gl.getAttribLocation(backdrop,'aPosition');
  const reduced=matchMedia('(prefers-reduced-motion:reduce)');
  let width=0,height=0,visible=false,paused=false,frame=0,lastTime=0,elapsed=0;
  let expanded=false,spread=0,angle=0,targetAngle=0,pitch=0,targetPitch=0,drag=null;
  let hoverX=0,hoverY=0,targetHoverX=0,targetHoverY=0;

  function draw(object,model,color,opacity,flat=false,mode=gl.TRIANGLES) {
    gl.uniformMatrix4fv(surfaceLocations.uModel,false,model);gl.uniform3fv(surfaceLocations.uColor,color);
    gl.uniform1f(surfaceLocations.uOpacity,opacity);gl.uniform1f(surfaceLocations.uFlat,flat?1:0);
    gl.bindBuffer(gl.ARRAY_BUFFER,object.positions);gl.enableVertexAttribArray(position);gl.vertexAttribPointer(position,3,gl.FLOAT,false,0,0);
    if(flat){gl.disableVertexAttribArray(normal);gl.vertexAttrib3f(normal,0,0,1);}
    else{gl.bindBuffer(gl.ARRAY_BUFFER,object.normals);gl.enableVertexAttribArray(normal);gl.vertexAttribPointer(normal,3,gl.FLOAT,false,0,0);}
    gl.drawArrays(mode,0,object.count);
  }
  function render() {
    if(!width||!height||lost)return;
    gl.disable(gl.DEPTH_TEST);gl.disable(gl.CULL_FACE);gl.disable(gl.BLEND);gl.depthMask(true);
    gl.clearColor(.973,.980,.988,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
    gl.useProgram(backdrop);gl.bindBuffer(gl.ARRAY_BUFFER,quad);gl.enableVertexAttribArray(backdropPosition);gl.vertexAttribPointer(backdropPosition,2,gl.FLOAT,false,0,0);
    gl.drawArrays(gl.TRIANGLE_STRIP,0,4);
    gl.useProgram(surface);
    const time=reduced.matches?0:elapsed;
    const rotation=multiply(ry(-.60+time*.18+angle+hoverX),multiply(rx(-.42+pitch+hoverY),rz(-.38)));
    const group=multiply(translate(0,.10+Math.sin(time*1.1)*.06,0),rotation);
    const coreModel=multiply(group,multiply(translate(0,0,.22),multiply(ry(.38),rz(.24))));
    const plates=Array.from({length:5},(_,i)=>{
      const offset=i-2;
      const local=multiply(translate(offset*(.10+spread*.50),offset*.055,offset*.42),ry(offset*spread*.15));
      const model=multiply(group,local);return {model,index:i,z:model[14]};
    }).sort((a,b)=>a.z-b.z);
    // Fit the current transformed volume in both axes, including expanded and dragged views.
    // This stays continuous while the layers ease apart and leaves room around their bevels.
    const tangent=Math.tan(36*Math.PI/360),aspect=width/height;
    let distance=5.4;
    const fit=(model,half)=>{
      for(const x of [-half[0],half[0]])for(const y of [-half[1],half[1]])for(const z of [-half[2],half[2]]){
        const wx=model[0]*x+model[4]*y+model[8]*z+model[12];
        const wy=model[1]*x+model[5]*y+model[9]*z+model[13];
        const wz=model[2]*x+model[6]*y+model[10]*z+model[14];
        distance=Math.max(distance,wz+Math.max(Math.abs(wx)/(tangent*aspect*.86),Math.abs(wy)/(tangent*.86)));
      }
    };
    for(const object of plates)fit(object.model,[.95,.95,.116]);
    fit(coreModel,[.41,.41,.41]);
    const camera=multiply(perspective(aspect),translate(0,0,-distance));
    gl.uniformMatrix4fv(surfaceLocations.uCamera,false,camera);gl.uniform1f(surfaceLocations.uTime,time);gl.uniform1f(surfaceLocations.uDistance,distance);
    gl.enable(gl.DEPTH_TEST);gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);
    gl.enable(gl.CULL_FACE);gl.cullFace(gl.BACK);
    draw(core,coreModel,[.07,.27,.69],1);
    gl.depthMask(false);
    for(const object of plates){
      const tint=object.index===2?[.60,.78,.98]:[.76,.88,1.];
      gl.cullFace(gl.FRONT);draw(plate,object.model,tint,.22);
      gl.cullFace(gl.BACK);draw(plate,object.model,tint,.30);
      gl.disable(gl.CULL_FACE);
      draw({positions:plate.lines,count:plate.lineCount},object.model,[.61,.77,.96],.34,true,gl.LINES);
      draw(etched,object.model,[.55,.72,.92],.28,true,gl.LINES);
      gl.enable(gl.CULL_FACE);
    }
    gl.depthMask(true);gl.disable(gl.CULL_FACE);gl.disable(gl.BLEND);
    if(fallback)fallback.hidden=true;
    canvas.dataset.ready='true';canvas.dataset.expanded=String(expanded);
  }
  function canRun(){return visible&&!document.hidden&&!paused&&!reduced.matches&&!lost;}
  function updateControls(){
    canvas.dataset.motion=lost||reduced.matches?'static':paused?'paused':canRun()?'running':'offscreen';
    if(motionButton){motionButton.disabled=lost||reduced.matches;motionButton.setAttribute('aria-pressed',String(paused));motionButton.textContent=lost||reduced.matches?'静态展示':paused?'播放动效':'暂停动效';}
    if(expandButton){expandButton.disabled=lost;expandButton.setAttribute('aria-pressed',String(expanded));expandButton.textContent=expanded?'收拢结构':'展开结构';}
    if(rotateButton)rotateButton.disabled=lost;
  }
  function tick(now){
    frame=0;if(!canRun()){lastTime=0;updateControls();return;}
    if(lastTime&&now-lastTime<32){frame=requestAnimationFrame(tick);return;}
    elapsed+=lastTime?Math.min((now-lastTime)/1000,.05):0;lastTime=now;
    spread+=((expanded?1:0)-spread)*.075;angle+=(targetAngle-angle)*.12;pitch+=(targetPitch-pitch)*.12;
    hoverX+=(targetHoverX-hoverX)*.07;hoverY+=(targetHoverY-hoverY)*.07;
    render();frame=requestAnimationFrame(tick);
  }
  function sync(){cancelAnimationFrame(frame);frame=0;lastTime=0;updateControls();if(canRun())frame=requestAnimationFrame(tick);else if(visible)render();}
  function immediate(){if(!canRun()){spread=expanded?1:0;angle=targetAngle;pitch=targetPitch;hoverX=0;hoverY=0;render();}updateControls();}
  function resize(){
    const bounds=layer.getBoundingClientRect();if(!bounds.width||!bounds.height)return;
    width=bounds.width;height=bounds.height;
    const resolution=Math.min(devicePixelRatio||1,1.6,Math.sqrt(900000/(width*height)));
    canvas.width=Math.round(width*resolution);canvas.height=Math.round(height*resolution);
    gl.viewport(0,0,canvas.width,canvas.height);render();
  }
  if(motionButton)motionButton.addEventListener('click',()=>{paused=!paused;sync();});
  if(rotateButton)rotateButton.addEventListener('click',()=>{targetAngle+=Math.PI/3;immediate();});
  if(expandButton)expandButton.addEventListener('click',()=>{expanded=!expanded;immediate();});
  canvas.addEventListener('pointerdown',event=>{
    if(lost||event.button!==0)return;
    drag={id:event.pointerId,x:event.clientX,y:event.clientY,angle:targetAngle,pitch:targetPitch};
    canvas.setPointerCapture(event.pointerId);canvas.classList.add('dragging');
  });
  canvas.addEventListener('pointermove',event=>{
    if(drag&&event.pointerId===drag.id){targetAngle=drag.angle+(event.clientX-drag.x)*.009;targetPitch=Math.max(-.7,Math.min(.7,drag.pitch+(event.clientY-drag.y)*.006));immediate();}
    else if(event.pointerType!=='touch'&&canRun()){
      const bounds=canvas.getBoundingClientRect();targetHoverX=((event.clientX-bounds.left)/width-.5)*.28;targetHoverY=((event.clientY-bounds.top)/height-.5)*.16;
    }
  });
  const endDrag=()=>{drag=null;canvas.classList.remove('dragging');};
  canvas.addEventListener('pointerup',endDrag);canvas.addEventListener('pointercancel',endDrag);canvas.addEventListener('lostpointercapture',endDrag);
  canvas.addEventListener('pointerleave',()=>{targetHoverX=0;targetHoverY=0;});
  document.addEventListener('visibilitychange',sync);
  reduced.addEventListener('change',()=>{targetHoverX=0;targetHoverY=0;hoverX=0;hoverY=0;spread=expanded?1:0;angle=targetAngle;pitch=targetPitch;sync();});
  new ResizeObserver(resize).observe(layer);
  new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;sync();},{threshold:0}).observe(layer);
  canvas.addEventListener('webglcontextlost',()=>{cancelAnimationFrame(frame);frame=0;showFallback();updateControls();});
  resize();
})();
