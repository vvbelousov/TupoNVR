import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const source=ts.transpileModule(readFileSync(new URL('../src/navigation.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext}}).outputText;
const {readRoute,liveURL,pageURL}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const route=url=>readRoute(new URL(url,'http://nvr.test'));
test('single-camera identity survives direct navigation and reload without transient state',()=>{
  assert.deepEqual(route(liveURL(42)),{page:'Live',cameraId:42});
  assert.deepEqual(route('/cameras/7/live/'),{page:'Live',cameraId:7});
});
test('invalid camera identifiers never resolve to another camera',()=>{
  for(const id of ['nope','0','-1','1.5','9007199254740992','1e2'])assert.deepEqual(route(`/cameras/${id}/live`),{page:'Live',cameraId:null});
});
test('normal pages have reloadable URLs independent of live camera URLs',()=>{
  for(const page of ['Overview','Cameras','Multiview','Archive','Storage'])assert.deepEqual(route(pageURL(page)),{page,cameraId:null});
  assert.deepEqual(route('/?page=unexpected'),{page:'Overview',cameraId:null});
});
