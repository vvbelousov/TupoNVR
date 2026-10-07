import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const source=ts.transpileModule(readFileSync(new URL('../src/fullscreen.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext}}).outputText;
const {fullscreenSupported,toggleFullscreen}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
function player(){
  const calls=[];
  const document={fullscreenEnabled:true,fullscreenElement:null,async exitFullscreen(){calls.push('exit');this.fullscreenElement=null}};
  const element={ownerDocument:document,async requestFullscreen(){calls.push(this);document.fullscreenElement=this}};
  return {element,document,calls};
}
test('requests only the selected player and exits its fullscreen session',async()=>{
  const {element,document,calls}=player();
  assert.equal(fullscreenSupported(element),true);
  await toggleFullscreen(element);
  assert.equal(document.fullscreenElement,element);
  await toggleFullscreen(element);
  assert.deepEqual(calls,[element,'exit']);
  assert.equal(document.fullscreenElement,null);
});
test('another fullscreen player does not make this player an exit action',async()=>{
  const {element,document,calls}=player();document.fullscreenElement={};
  await toggleFullscreen(element);assert.deepEqual(calls,[element]);
});
test('missing or policy-disabled fullscreen gracefully performs no action',async()=>{
  for(const disable of [h=>{h.document.fullscreenEnabled=false},h=>{h.element.requestFullscreen=undefined},h=>{h.document.exitFullscreen=undefined}]){
    const h=player();disable(h);assert.equal(fullscreenSupported(h.element),false);
    await toggleFullscreen(h.element);assert.deepEqual(h.calls,[]);
  }
});
test('request and exit rejections reach the UI error handler',async()=>{
  const h=player();h.element.requestFullscreen=async()=>{throw new Error('denied')};
  await assert.rejects(toggleFullscreen(h.element),/denied/);
  h.document.fullscreenElement=h.element;h.document.exitFullscreen=async()=>{throw new Error('exit denied')};
  await assert.rejects(toggleFullscreen(h.element),/exit denied/);
});
