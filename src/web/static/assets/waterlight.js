/* One bounded, decorative waterlight field. Text and controls are always native DOM. */
(() => {
  const canvas = document.querySelector('#ambientLight');
  const layer = document.querySelector('#ambientBackdrop');
  const hero = document.querySelector('#hero');
  const button = document.querySelector('#ambientMotionToggle');
  const label = button?.querySelector('.motion-label');
  const glyph = button?.querySelector('use');
  const fallback = document.querySelector('.ambient-fallback');
  if (!canvas || !layer || !hero) return;
  const gl = canvas.getContext('webgl', { alpha: false, antialias: false, powerPreference: 'low-power' });
  const reduced = matchMedia('(prefers-reduced-motion:reduce)');
  const fine = matchMedia('(hover:hover) and (pointer:fine)');
  let width = 0, height = 0, visible = false, paused = false, lost = false;
  let frame = 0, lastTime = 0, elapsed = 0;
  const pointer = { x: .5, y: .5, targetX: .5, targetY: .5 };
  function fallbackView() {
    lost = true; canvas.hidden = true; canvas.dataset.motion = 'static';
    if (fallback) fallback.hidden = false;
    if (button) {
      button.disabled = true; button.title = '当前显示静态光影';
      button.setAttribute('aria-label', '静态展示'); button.setAttribute('aria-pressed', 'false');
    }
    if (label) label.textContent = '静态展示';
    if (glyph) glyph.setAttribute('href', '#i-light');
  }
  if (!gl) { fallbackView(); return; }
  const vertexSource = `
    attribute vec2 aPosition; varying vec2 vUv;
    void main(){vUv=aPosition*.5+.5;gl_Position=vec4(aPosition,0.,1.);}`;
  const fragmentSource = `
    precision highp float;
    varying vec2 vUv; uniform vec2 uSize; uniform vec2 uPointer; uniform float uTime;
    // Height and analytic slope of a shallow, continuous surface.
    vec3 water(vec2 p,float t){
      float a=p.x*1.35+p.y*.42+t*.40;
      float b=p.y*1.85-p.x*.33-t*.44;
      float c=p.x*3.10+p.y*2.35+t*.52;
      float d=p.x*5.4-p.y*3.3+t*.57;
      return vec3(.145*sin(a)+.095*sin(b)+.032*sin(c)+.014*sin(d),
        .19575*cos(a)-.03135*cos(b)+.09920*cos(c)+.07560*cos(d),
        .06090*cos(a)+.17575*cos(b)+.07520*cos(c)-.04620*cos(d));
    }
    void main(){
      vec2 uv=vec2(vUv.x,1.-vUv.y);
      vec2 screen=(vUv-.5)*vec2(uSize.x/uSize.y,1.);
      vec2 hand=(uPointer-.5)*.18;
      vec3 eye=vec3(hand.x,3.1,3.5+hand.y);
      vec3 forward=normalize(vec3(0.,-.1,-.55)-eye);
      vec3 right=normalize(cross(forward,vec3(0.,1.,0.)));
      vec3 up=cross(right,forward);
      vec3 ray=normalize(forward+screen.x*right*.93+screen.y*up*.93);
      float distance=-eye.y/ray.y;
      for(int i=0;i<3;i++){
        vec3 at=eye+ray*distance;
        distance+=(water(at.xz,uTime).x-at.y)/ray.y;
      }
      vec3 at=eye+ray*distance;
      vec3 surface=water(at.xz,uTime);
      vec3 normal=normalize(vec3(-surface.y,1.,-surface.z));
      vec3 reflected=reflect(ray,normal);
      float fresnel=pow(1.-max(dot(normal,-ray),0.),3.);
      float sky=smoothstep(-.20,.95,reflected.y);
      vec3 silver=mix(vec3(.29,.53,.72),vec3(.985,.995,1.),sky);
      float window=pow(max(dot(reflected,normalize(vec3(-.55,1.,.42))),0.),36.);
      float glint=pow(max(dot(reflected,normalize(vec3(.65,1.,.80))),0.),58.);
      vec3 color=mix(vec3(.60,.77,.87),silver,.62+fresnel*.30);
      color+=vec3(.51,.54,.56)*window+vec3(.53,.56,.58)*glint;
      color*=.94+.06*max(dot(normal,normalize(vec3(-.6,1.,.6))),0.);
      // Broad diffusion reserves a stable luminous center for the wordmark.
      vec2 center=(uv-vec2(.57,.28))/vec2(.30,.30);
      float veil=exp(-dot(center,center));
      vec3 porcelain=vec3(.969,.980,.988);
      color=mix(color,porcelain,.10+veil*.82);
      color=mix(color,porcelain,smoothstep(.38,.98,uv.y)*.93);
      float grain=fract(sin(dot(gl_FragCoord.xy,vec2(12.9898,78.233)))*43758.5453);
      color+=(grain-.5)/420.;
      gl_FragColor=vec4(clamp(color,0.,1.),1.);
    }`;
  function shader(type, source) {
    const result = gl.createShader(type); gl.shaderSource(result, source); gl.compileShader(result);
    if (!gl.getShaderParameter(result, gl.COMPILE_STATUS)) { gl.deleteShader(result); return null; }
    return result;
  }
  const vertex = shader(gl.VERTEX_SHADER, vertexSource), fragment = shader(gl.FRAGMENT_SHADER, fragmentSource);
  if (!vertex || !fragment) { [vertex, fragment].filter(Boolean).forEach(item => gl.deleteShader(item)); fallbackView(); return; }
  const program = gl.createProgram(); gl.attachShader(program, vertex); gl.attachShader(program, fragment); gl.linkProgram(program);
  gl.deleteShader(vertex); gl.deleteShader(fragment);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) { gl.deleteProgram(program); fallbackView(); return; }
  gl.useProgram(program); gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,1,1]), gl.STATIC_DRAW);
  const position = gl.getAttribLocation(program, 'aPosition');
  gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
  const uniforms = Object.fromEntries(['uSize','uPointer','uTime'].map(name => [name, gl.getUniformLocation(program, name)]));
  function render() {
    if (!width || !height || lost) return;
    gl.uniform2f(uniforms.uSize, width, height); gl.uniform2f(uniforms.uPointer, pointer.x, pointer.y);
    gl.uniform1f(uniforms.uTime, reduced.matches ? 0 : elapsed); gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    if (fallback) fallback.hidden = true;
    canvas.dataset.ready = 'true';
  }
  function canRun() { return visible && !document.hidden && !paused && !reduced.matches && !lost; }
  function controls() {
    const staticMode = reduced.matches || lost;
    canvas.dataset.motion = staticMode ? 'static' : paused ? 'paused' : canRun() ? 'running' : 'offscreen';
    if (!button) return;
    button.disabled = staticMode; button.setAttribute('aria-pressed', String(paused));
    const text = staticMode ? '静态展示' : paused ? '播放光影' : '暂停光影';
    if (label) label.textContent = text;
    button.setAttribute('aria-label', text);
    button.title = staticMode ? '已按系统偏好或图形能力显示静态光影' : text;
    if (glyph) glyph.setAttribute('href', staticMode ? '#i-light' : paused ? '#i-play' : '#i-pause');
  }
  function tick(now) {
    frame = 0;
    if (!canRun()) { lastTime = 0; controls(); return; }
    if (lastTime && now-lastTime < 1000/24) { frame=requestAnimationFrame(tick); return; }
    elapsed += lastTime ? Math.min((now-lastTime)/1000,.10) : 0; lastTime=now;
    pointer.x += (pointer.targetX-pointer.x)*.065; pointer.y += (pointer.targetY-pointer.y)*.065;
    render(); frame=requestAnimationFrame(tick);
  }
  function sync() {
    cancelAnimationFrame(frame); frame=0; lastTime=0; controls();
    if (canRun()) frame=requestAnimationFrame(tick); else if (visible) render();
  }
  function resize() {
    const bounds=layer.getBoundingClientRect(); if (!bounds.width || !bounds.height) return;
    width=bounds.width; height=bounds.height;
    const scale=Math.min(1,Math.sqrt(700000/(width*height)));
    canvas.width=Math.round(width*scale); canvas.height=Math.round(height*scale);
    gl.viewport(0,0,canvas.width,canvas.height); render();
  }
  window.addEventListener('pointermove', event => {
    if (!canRun() || !fine.matches || event.pointerType==='touch') return;
    pointer.targetX=Math.min(1,Math.max(0,event.clientX/width)); pointer.targetY=Math.min(1,Math.max(0,event.clientY/height));
  }, { passive:true });
  const reset=()=>{pointer.targetX=.5;pointer.targetY=.5;};
  document.documentElement.addEventListener('pointerleave',reset);
  document.addEventListener('visibilitychange',sync);
  reduced.addEventListener('change',()=>{reset();pointer.x=.5;pointer.y=.5;sync();});
  fine.addEventListener('change',reset);
  if (button) button.addEventListener('click',()=>{paused=!paused;sync();});
  new ResizeObserver(resize).observe(layer);
  new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;sync();},{threshold:0}).observe(hero);
  canvas.addEventListener('webglcontextlost',()=>{cancelAnimationFrame(frame);frame=0;fallbackView();controls();});
  resize();
})();
