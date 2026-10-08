import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const source=ts.transpileModule(readFileSync(new URL('../src/snapshot.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext}}).outputText;
const {captureFrame}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
test('undecoded live or archive video gives an actionable error',async()=>{
  await assert.rejects(captureFrame({readyState:1,videoWidth:0,videoHeight:0}),/No decoded frame/);
});
test('canvas preserves native frame dimensions and returns a PNG',async()=>{
  const video={readyState:2,videoWidth:1920,videoHeight:1080};
  const canvas={getContext:()=>({drawImage:(element,x,y)=>assert.deepEqual([element,x,y],[video,0,0])}),toBlob:(callback,type)=>{assert.equal(type,'image/png');callback(new Blob(['png'],{type}))}};
  globalThis.document={createElement:()=>canvas};
  assert.equal((await captureFrame(video)).type,'image/png');
  assert.equal(canvas.width,1920);assert.equal(canvas.height,1080);
});
test('browser security and unavailable encoders fail clearly',async()=>{
  const video={readyState:2,videoWidth:64,videoHeight:64};
  globalThis.document={createElement:()=>({getContext:()=>({drawImage:()=>{throw new DOMException('Tainted','SecurityError')}})})};
  await assert.rejects(captureFrame(video),/browser security/);
  globalThis.document={createElement:()=>({getContext:()=>({drawImage:()=>{}}),toBlob:callback=>callback(null)})};
  await assert.rejects(captureFrame(video),/browser security or codec/);
});
