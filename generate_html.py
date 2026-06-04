import sqlite3, json

conn = sqlite3.connect('data/bom_compare.db')
conn.row_factory = sqlite3.Row

items = conn.execute('''
    SELECT part_number, part_name, parent_pn, level, quantity
    FROM bom_item WHERE bom_id=1 ORDER BY line_no
''').fetchall()

root_pn = 'P1C85V68HP7T871000'

pn_map = {}
children_map = {}
for it in items:
    pn = it['part_number']
    pn_map[pn] = {'name': it['part_name'], 'pn': pn}
    parent = it['parent_pn']
    if parent:
        children_map.setdefault(parent, []).append(pn)

def to_dict(pn, depth=0):
    if depth > 10:
        return {'name': pn}
    node = pn_map.get(pn, {'name': pn, 'pn': pn})
    kids = children_map.get(pn, [])
    label = node['pn'] + ' ' + node['name']
    if len(label) > 35:
        label = label[:35] + '...'
    result = {'name': label}
    if kids:
        result['children'] = [to_dict(c, depth+1) for c in kids]
    return result

tree = {
    'name': root_pn + ' (根)',
    'children': [to_dict(c) for c in children_map.get(root_pn, [])]
}

tree_js = 'const BOM_TREE_DATA = ' + json.dumps(tree, ensure_ascii=False)

# Read HTML template and inject data
html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BOM 思维导图</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Microsoft YaHei',sans-serif;background:#f5f7fa;overflow:hidden}
#toolbar{position:fixed;top:0;left:0;right:0;height:48px;background:#fff;border-bottom:1px solid #e0e6ed;display:flex;align-items:center;padding:0 20px;z-index:1000;box-shadow:0 2px 8px rgba(0,0,0,0.08)}
#toolbar .title{font-size:16px;font-weight:600;color:#1a1a2e;margin-right:30px}
#toolbar button{margin-right:8px;padding:6px 14px;border:1px solid #d0d7de;border-radius:6px;background:#fff;color:#333;cursor:pointer;font-size:13px;transition:all .2s}
#toolbar button:hover{background:#e8f4fd;border-color:#91caff}
#info-bar{position:fixed;bottom:0;left:0;right:0;height:36px;background:#fff;border-top:1px solid #e0e6ed;display:flex;align-items:center;padding:0 20px;font-size:12px;color:#666;z-index:1000}
#search-box{margin-left:auto;display:flex;align-items:center;gap:6px}
#search-input{width:200px;padding:4px 10px;border:1px solid #d0d7de;border-radius:6px;font-size:13px;outline:none}
#search-input:focus{border-color:#1890ff}
#chart{width:100%;height:100vh;margin-top:48px}
.node-box{cursor:pointer}
.node-box:hover rect{filter:brightness(0.95)}
.link{fill:none;stroke:#c0c8d0;stroke-width:1.2}
.tooltip{position:fixed;background:rgba(0,0,0,0.85);color:#fff;padding:8px 12px;border-radius:6px;font-size:12px;pointer-events:none;z-index:9999;max-width:400px;box-shadow:0 4px 12px rgba(0,0,0,0.3);line-height:1.6;display:none}
.legend{position:fixed;top:60px;right:20px;background:#fff;border:1px solid #e0e6ed;border-radius:8px;padding:12px 16px;font-size:12px;color:#555;box-shadow:0 2px 8px rgba(0,0,0,0.1);z-index:999}
.legend-title{font-weight:600;margin-bottom:8px;color:#333}
.legend-item{display:flex;align-items:center;margin-bottom:4px}
.legend-color{width:14px;height:14px;border-radius:3px;margin-right:8px}
</style>
</head>
<body>

<div id="toolbar">
  <div class="title">📋 BOM 思维导图</div>
  <button onclick="expandDepth(1)">展开L1</button>
  <button onclick="expandDepth(2)">展开L2</button>
  <button onclick="expandDepth(3)">展开L3</button>
  <button onclick="collapseAll()">收起全部</button>
  <button onclick="resetZoom()">重置视图</button>
  <div id="search-box">
    <input type="text" id="search-input" placeholder="搜索物料号/名称..." oninput="onSearch(this.value)">
  </div>
</div>

<div id="chart"></div>

<div class="legend">
  <div class="legend-title">层级颜色</div>
  <div class="legend-item"><div class="legend-color" style="background:#d6e4f0"></div>L1</div>
  <div class="legend-item"><div class="legend-color" style="background:#e2efda"></div>L2</div>
  <div class="legend-item"><div class="legend-color" style="background:#fff2cc"></div>L3</div>
  <div class="legend-item"><div class="legend-color" style="background:#fce5cd"></div>L4</div>
  <div class="legend-item"><div class="legend-color" style="background:#f4cccc"></div>L5</div>
</div>

<div class="tooltip" id="tooltip"></div>

<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
TREE_DATA_PLACEHOLDER
</script>
</body>
</html>'''

# Replace placeholder with actual data
html = html.replace('TREE_DATA_PLACEHOLDER', tree_js + '''

const levelColors = {0:'#ffe4d6',1:'#d6e4f0',2:'#e2efda',3:'#fff2cc',4:'#fce5cd',5:'#f4cccc'};
const levelNames = {0:'根节点',1:'L1',2:'L2',3:'L3',4:'L4',5:'L5'};

const width = window.innerWidth;
const height = window.innerHeight - 48 - 36;
const margin = {top:60,right:120,bottom:60,left:120};

const svg = d3.select('#chart').append('svg')
  .attr('width', width).attr('height', height);

const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);

const zoom = d3.zoom().on('zoom', (e)=>g.attr('transform', e.transform));
svg.call(zoom);

const root = d3.hierarchy(BOM_TREE_DATA);
const treeLayout = d3.tree().nodeSize([80, 220]);
treeLayout(root);

// Initial: expand to depth 2
root.each(d=>{
  if(d.depth > 2 && d.children){
    d._children = d.children;
    d.children = null;
  }
});

function update(source) {
  const nodes = root.descendants();
  const links = root.links();
  
  const link = g.selectAll('.link').data(links, d=>d.target.data.name);
  link.exit().remove();
  const linkEnter = link.enter().append('path').attr('class','link')
    .attr('d', d=>{
      const o = {x:source.x0||0, y:source.y0||0};
      return `M${o.y},${o.x}C${(o.y+d.target.y)/2},${o.x} ${(o.y+d.target.y)/2},${d.target.x} ${d.target.y},${d.target.x}`;
    });
  linkEnter.merge(link).transition().duration(500)
    .attr('d', d=>`M${d.source.y},${d.source.x}C${(d.source.y+d.target.y)/2},${d.source.x} ${(d.source.y+d.target.y)/2},${d.target.x} ${d.target.y},${d.target.x}`);
  
  const node = g.selectAll('.node').data(nodes, d=>d.data.name);
  node.exit().remove();
  
  const nodeEnter = node.enter().append('g').attr('class','node')
    .attr('transform', d=>`translate(${source.y0||0},${source.x0||0})`)
    .on('click', (event,d)=>{
      if(d._children){d.children=d._children;d._children=null;}
      else if(d.children){d._children=d.children;d.children=null;}
      d.x0=d.x; d.y0=d.y;
      update(d);
    })
    .on('mouseover', (event,d)=>{
      const tip = document.getElementById('tooltip');
      tip.innerHTML = '<b>'+(d.data.name||'').replace(/\\n/g,' | ')+'</b><br>层级: '+levelNames[d.depth]+'<br>子节点: '+(d.children||d._children||[]).length;
      tip.style.display='block';
      tip.style.left=(event.clientX+15)+'px';
      tip.style.top=(event.clientY-10)+'px';
    })
    .on('mouseout', ()=>document.getElementById('tooltip').style.display='none');
  
  nodeEnter.append('rect').attr('class','node-box')
    .attr('rx',6).attr('ry',6).attr('height',32).attr('y',-16)
    .attr('width', d=>Math.max(120, (d.data.name||'').length*7+20))
    .attr('fill', d=>levelColors[Math.min(d.depth,5)])
    .attr('stroke', d=>d3.color(levelColors[Math.min(d.depth,5)]).darker(0.5))
    .attr('stroke-width',1.2);
  
  nodeEnter.append('text').attr('x',4).attr('y',4)
    .attr('font-size', d=>d.depth<=1?'11px':'10px')
    .attr('font-weight', d=>d.depth<=1?600:400)
    .attr('fill','#222')
    .text(d=>{const n=d.data.name||''; return n.length>25?n.substring(0,25)+'...':n;});
  
  const nodeUpdate = nodeEnter.merge(node);
  nodeUpdate.transition().duration(500)
    .attr('transform', d=>`translate(${d.y},${d.x})`);
  
  nodes.forEach(d=>{d.x0=d.x; d.y0=d.y;});
}

function expandDepth(maxDepth){
  function expand(d){
    if(d.depth < maxDepth && d._children){
      d.children = d._children;
      d._children = null;
      d.children.forEach(c=>expand(c));
    }
  }
  expand(root);
  update(root);
}

function collapseAll(){
  root.each(d=>{
    if(d.children && d.depth>0){
      d._children = d.children;
      d.children = null;
    }
  });
  update(root);
}

function resetZoom(){
  svg.transition().duration(500).call(d3.zoom().transform, d3.zoomIdentity);
}

function onSearch(val){
  if(!val){update(root);return;}
  val = val.toLowerCase();
  root.each(d=>{
    const name = (d.data.name||'').toLowerCase();
    d.highlight = name.includes(val);
    if(d._children && d._children.some(c=>(c.data.name||'').toLowerCase().includes(val))){
      d.children = d._children;
      d._children = null;
    }
  });
  update(root);
}

update(root);
''')

with open('bom_mindmap.html', 'w', encoding='utf-8') as f:
    f.write(html)

print('Self-contained HTML generated: bom_mindmap.html')
print('File size:', len(html), 'bytes')
conn.close()
