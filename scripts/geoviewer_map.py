# Copyright 2026 Ahmed Saher Nouh
# SPDX-License-Identifier: Apache-2.0
"""Offline native-coordinate map controls. Website: https://saherlabs.dev/

Marker diameters and text sizes are CSS pixels, independent of SVG zoom.
Coordinates and polygon segment order are never changed by display controls.
"""

CONTROLS = '''<div class="map-controls map-display-controls">
<label>Well diameter <input id="well-size" type="range" min="2" max="16" value="8"><output id="well-size-value">8 px</output></label>
<label>Point diameter <input id="point-size" type="range" min="1" max="8" value="2"><output id="point-size-value">2 px</output></label>
<label>Well opacity <input id="well-opacity" type="range" min="10" max="100" value="80"><output id="well-opacity-value">80%</output></label>
<label>Point opacity <input id="point-opacity" type="range" min="5" max="100" value="35"><output id="point-opacity-value">35%</output></label>
<label><input type="checkbox" id="well-labels">Well labels</label>
<label>Well <select id="well-select"><option value="">Select a well</option></select></label>
<button type="button" data-map-action="fit-all">Fit all</button>
<button type="button" data-map-action="fit-visible">Fit visible layers</button>
<button type="button" data-map-action="fit-wells">Fit wells</button>
<button type="button" data-map-action="fit-selected">Fit selected well</button>
<span id="map-view-status" role="status"></span></div>
<div id="map-selection" aria-live="polite">Click a well to inspect nearby or overlapping wells. Positions are never moved for display.</div>'''

STYLE = '''
.map-shell{background:#0c2029}#spatial-map{height:560px;max-height:70vh}.map-controls input[type=range]{width:95px}
#spatial-map .axis-label{display:none}
.map-controls label{display:inline-flex;align-items:center;gap:5px}
#map-selection{padding:8px;max-height:190px;overflow:auto;font-size:13px}
#spatial-map .map-grid{pointer-events:none}#spatial-map .map-grid line{stroke:#36535e}
#spatial-map .map-grid text{fill:#a9bdc7;stroke:#0c2029;paint-order:stroke;stroke-width:3px;vector-effect:non-scaling-stroke}
#spatial-map .well-label{fill:#eff8ff;stroke:#0c2029;paint-order:stroke;stroke-width:2px;vector-effect:non-scaling-stroke;pointer-events:none}
#spatial-map .map-points{opacity:1}#spatial-map .well-dot{stroke-width:.6;vector-effect:non-scaling-stroke}
#spatial-map .well-dot.selected{fill:#ffdf68;stroke:#fff;stroke-width:1.4}
'''

SCRIPT = r'''
(() => {
  const svg=document.getElementById('spatial-map'); if(!svg)return;
  const get=id=>document.getElementById(id), ns='http://www.w3.org/2000/svg';
  const wells=[...svg.querySelectorAll('.well-dot')], points=[...svg.querySelectorAll('#layer-points circle')];
  const polygons=[...svg.querySelectorAll('[data-polygon-object]')], labels=[...svg.querySelectorAll('.well-label')];
  const original=[0,0,1000,620], grid=svg.querySelector('.map-grid');
  let box=[...original], drag=null, selection=null;
  const wellSelect=get('well-select');
  wells.forEach((well,i)=>{const option=document.createElement('option');option.value=String(i);option.textContent=well.dataset.name;wellSelect.append(option);});
  const visible=node=>getComputedStyle(node).display!=='none' && getComputedStyle(node.parentElement).display!=='none';
  const coordinate=(x,y)=>new DOMPoint(x,y).matrixTransform(svg.getScreenCTM().inverse());
  const nice=x=>{const magnitude=10**Math.floor(Math.log10(x));return [1,2,5,10].find(n=>n*magnitude>=x)*magnitude;};
  function axes(scale){
    if(!grid||!Number.isFinite(+svg.dataset.nativeScale))return;
    grid.replaceChildren();const rect=svg.getBoundingClientRect();
    const lo=coordinate(rect.left+85,rect.top+20), hi=coordinate(rect.right-18,rect.bottom-24);
    const s=+svg.dataset.nativeScale,x0=+svg.dataset.x0,y0=+svg.dataset.y0,mx=+svg.dataset.minX,my=+svg.dataset.maxY;
    const xmin=mx+(lo.x-x0)/s,xmax=mx+(hi.x-x0)/s,ymin=my-(hi.y-y0)/s,ymax=my-(lo.y-y0)/s;
    const add=(tag,attrs,text)=>{const n=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));if(text!==undefined)n.textContent=text;if(attrs['font-size'])n.style.fontSize=attrs['font-size']+'px';if(attrs['stroke-width'])n.style.strokeWidth=attrs['stroke-width'];grid.append(n);};
    const dx=nice(Math.max((xmax-xmin)/6,1e-12)),dy=nice(Math.max((ymax-ymin)/5,1e-12));
    const fmt=v=>Math.abs(v)>1e8?v.toExponential(3):v.toLocaleString(undefined,{maximumFractionDigits:4});
    for(let v=Math.ceil(xmin/dx)*dx,n=0;v<=xmax&&n++<12;v+=dx){const x=x0+(v-mx)*s;add('line',{x1:x,x2:x,y1:lo.y,y2:hi.y,'stroke-width':.7/scale});add('text',{x,y:hi.y+15/scale,'font-size':10/scale,'text-anchor':'middle'},fmt(v));}
    for(let v=Math.ceil(ymin/dy)*dy,n=0;v<=ymax&&n++<12;v+=dy){const y=y0+(my-v)*s;add('line',{x1:lo.x,x2:hi.x,y1:y,y2:y,'stroke-width':.7/scale});add('text',{x:lo.x-4/scale,y:y+3/scale,'font-size':10/scale,'text-anchor':'end'},fmt(v));}
  }
  function apply(){
    svg.setAttribute('viewBox',box.join(' '));const m=svg.getScreenCTM();if(!m)return;
    const scale=Math.hypot(m.a,m.b);if(!scale)return;
    const ws=+get('well-size').value,ps=+get('point-size').value;
    wells.forEach((n,i)=>{n.setAttribute('r',ws/(2*scale));n.style.opacity=get('well-opacity').value/100;n.classList.toggle('selected',i===selection);});
    points.forEach(n=>{n.setAttribute('r',ps/(2*scale));n.style.opacity=get('point-opacity').value/100;});
    labels.forEach(n=>{n.style.display=get('well-labels').checked?'':'none';n.style.fontSize=(11/scale)+'px';n.setAttribute('x',+n.dataset.x+(ws/2+3)/scale);n.setAttribute('y',+n.dataset.y-3/scale);});
    get('map-view-status').textContent=(1000/box[2]).toFixed(1)+'x view';axes(scale);
  }
  function fit(nodes){
    const bounds=nodes.filter(visible).map(n=>n.tagName==='circle'?{x:+n.getAttribute('cx'),y:+n.getAttribute('cy'),width:0,height:0}:n.getBBox());
    if(!bounds.length){get('map-view-status').textContent='No visible objects to fit';return;}
    let x=Infinity,y=Infinity,x2=-Infinity,y2=-Infinity;bounds.forEach(b=>{x=Math.min(x,b.x);y=Math.min(y,b.y);x2=Math.max(x2,b.x+b.width);y2=Math.max(y2,b.y+b.height);});
    const w=Math.max(1,x2-x),h=Math.max(.62,y2-y);box=[(x+x2)/2-w*.6,(y+y2)/2-h*.6,w*1.2,h*1.2];apply();
  }
  function zoom(factor,anchor){
    const w=Math.max(.01,Math.min(100000,box[2]*factor)),f=w/box[2];
    anchor=anchor||{x:box[0]+box[2]/2,y:box[1]+box[3]/2};box=[anchor.x+(box[0]-anchor.x)*f,anchor.y+(box[1]-anchor.y)*f,w,box[3]*f];apply();
  }
  function inspect(indices){
    const panel=get('map-selection');panel.replaceChildren();
    const intro=document.createElement('p');intro.textContent=indices.length+' well(s) within the clicked marker area';panel.append(intro);
    indices.forEach(i=>{const w=wells[i],b=document.createElement('button');b.type='button';b.textContent=w.dataset.name+' Â· '+w.dataset.id+' Â· X '+w.dataset.nativeX+', Y '+w.dataset.nativeY;b.onclick=()=>{selection=i;wellSelect.value=String(i);apply();};panel.append(b);panel.append(document.createElement('br'));});
  }
  ['well-size','point-size','well-opacity','point-opacity'].forEach(id=>get(id).addEventListener('input',()=>{get(id+'-value').textContent=get(id).value+(id.endsWith('size')?' px':'%');apply();}));
  get('well-labels').onchange=apply;
  wellSelect.onchange=()=>{selection=wellSelect.value===''?null:+wellSelect.value;if(selection!==null)inspect([selection]);apply();};
  document.querySelectorAll('[data-layer]').forEach(c=>c.addEventListener('change',()=>{get(c.dataset.layer).style.display=c.checked?'':'none';apply();}));
  get('polygon-object-filter').onchange=()=>{const value=get('polygon-object-filter').value;polygons.forEach(n=>n.style.display=!value||n.dataset.polygonObject===value?'':'none');if(value)fit(polygons);else apply();};
  document.querySelectorAll('[data-map-action]').forEach(b=>b.onclick=()=>{switch(b.dataset.mapAction){
    case 'zoom-in':zoom(.8);break;case 'zoom-out':zoom(1.25);break;
    case 'fit-visible':fit([...wells,...points,...polygons]);break;case 'fit-wells':fit(wells);break;
    case 'fit-selected':if(selection!==null)fit([wells[selection]]);break;
    case 'fit-all':{const hidden=[...svg.querySelectorAll('[style*="display: none"]')];hidden.forEach(n=>n.style.display='');fit([...wells,...points,...polygons]);hidden.forEach(n=>n.style.display='none');break;}
    default:box=[...original];apply();
  }});
  svg.addEventListener('wheel',e=>{e.preventDefault();zoom(e.deltaY<0?.88:1.14,coordinate(e.clientX,e.clientY));},{passive:false});
  svg.addEventListener('pointerdown',e=>{if(e.button!==0)return;drag={x:e.clientX,y:e.clientY,p:coordinate(e.clientX,e.clientY),box:[...box]};svg.setPointerCapture(e.pointerId);});
  svg.addEventListener('pointermove',e=>{if(!drag)return;const p=coordinate(e.clientX,e.clientY);box[0]+=drag.p.x-p.x;box[1]+=drag.p.y-p.y;apply();});
  svg.addEventListener('pointerup',e=>{if(drag&&Math.hypot(e.clientX-drag.x,e.clientY-drag.y)<4){const m=svg.getScreenCTM(),near=[];wells.forEach((w,i)=>{if(!visible(w))return;const p=new DOMPoint(+w.getAttribute('cx'),+w.getAttribute('cy')).matrixTransform(m);if(Math.hypot(p.x-e.clientX,p.y-e.clientY)<=Math.max(6,+get('well-size').value/2))near.push(i);});if(near.length){selection=near[0];wellSelect.value=String(selection);inspect(near);apply();}}drag=null;});
  svg.addEventListener('pointercancel',()=>drag=null);
  new ResizeObserver(()=>apply()).observe(svg);apply();
})();
'''
