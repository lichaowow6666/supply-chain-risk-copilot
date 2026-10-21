"""Exercise the actual graph JavaScript without a browser or model calls."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node.js is needed for graph interaction checks')
class GraphInteractionTests(unittest.TestCase):
    def test_pin_hover_toggle_persistence_and_impact_visibility(self):
        html = (Path(__file__).parents[1] / 'pe_copilot/ui/assets/graph_explorer.html').read_text()
        script = html.split('<script>')[1].split('</script>')[0]
        harness = r'''
const vm=require('vm'),assert=require('assert');
const script=SCRIPT;
const storage=new Map();
class Element{
 constructor(){this.listeners={};this.children=[];this.textContent='';this.style={};this.clientWidth=900;this.clientHeight=600;this.classes=new Set();this.classList={toggle:(n,on)=>on?this.classes.add(n):this.classes.delete(n),contains:n=>this.classes.has(n)};}
 setAttribute(k,v){if(k==='class')this.classes=new Set(v.split(' '));}
 append(...v){this.children.push(...v);} replaceChildren(){this.children=[];}
 addEventListener(k,v){this.listeners[k]=v;} setPointerCapture(){} releasePointerCapture(){}
}
function render(company='company:1',snapshot='report:1'){
 const impact=direction=>({direction,label:direction,notes:[]});
 const node=id=>({id,label:id,kind:'material',origin:'confirmed',attributes:{},observations:[],evidence:[]});
 const payload={summary:{company_id:company,snapshot_id:snapshot,ticker:'TEST',period_end:'2025-12-31'},lens:'all_relationships',lens_label:'All',focus:null,nodes:['a','b','c','d'].map(node),edges:[{id:'ab',source:'a',target:'b',label:'uses',evidence:[]},{id:'adverse',source:'c',target:'d',label:'uses',evidence:[],impact:impact('adverse')},{id:'favorable',source:'d',target:'c',label:'uses',evidence:[],impact:impact('favorable')},{id:'uncertain',source:'c',target:'d',label:'uses',evidence:[],impact:impact('uncertain')}],chains:{a:{nodes:['a','b'],edges:['ab']},c:{nodes:['c','d'],edges:['adverse','favorable','uncertain']}},overlay:{nodes:[],edges:[],roots:[]}};
 const dom=new Map(),get=id=>{if(!dom.has(id))dom.set(id,new Element());return dom.get(id);};
 get('graph-data').textContent=JSON.stringify(payload);
 const context=vm.createContext({document:{getElementById:get,createElementNS:()=>new Element(),createElement:()=>new Element(),addEventListener(){}},sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},ResizeObserver:class{observe(){}},URL});
 vm.runInContext(script,context);
 return {context,dom,run:code=>vm.runInContext(code,context),click:id=>vm.runInContext(`elements.get('${id}').listeners.pointerdown({stopPropagation(){},clientX:0,clientY:0,pointerId:1});elements.get('${id}').listeners.pointerup({pointerId:1});`,context)};
}
let page=render();
page.click('a');
page.run("elements.get('c').listeners.mouseenter()");
assert.strictEqual(page.run('pinned'),'a');
assert.strictEqual(page.dom.get('entityTitle').textContent,'a','hover must preserve pinned inspector');
assert.strictEqual(page.run("elements.get('a').classList.contains('focused')"),true);
for(const direction of ['adverse','favorable','uncertain'])assert.strictEqual(page.run(`edgeElements.get('${direction}').classList.contains('dim')`),false,'scenario links remain highlighted');
page=render();
assert.strictEqual(page.run('pinned'),'a','pin survives component remount');
assert.strictEqual(page.dom.get('entityTitle').textContent,'a');
page=render();
assert.strictEqual(page.run('pinned'),'a','restoring a view must not overwrite its pin');
page.click('a');
assert.strictEqual(page.run('pinned'),null,'second click releases the same node');
page.run("elements.get('c').listeners.mouseenter()");
assert.strictEqual(page.dom.get('entityTitle').textContent,'c','hover resumes after release');
page.click('c');page.click('a');
assert.strictEqual(page.run('pinned'),'a','another click changes the pinned entity');
assert.strictEqual(render('company:2').run('pinned'),null,'company scope isolates pins');
assert.strictEqual(render('company:1','report:2').run('pinned'),null,'report scope isolates pins');
console.log('pin, hover, toggle, rerender persistence, scope isolation and impact visibility passed');
'''.replace('SCRIPT', json.dumps(script))
        result = subprocess.run([shutil.which('node'), '-e', harness], text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
