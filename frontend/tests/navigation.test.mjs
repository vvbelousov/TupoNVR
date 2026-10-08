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

test('account route supports navigation, reload and a trailing slash',()=>{
  assert.equal(pageURL('Account'),'/account');
  for(const url of ['/account','/account/'])assert.deepEqual(route(url),{page:'Account',cameraId:null});
});

test('direct protected page paths preserve the requested destination',()=>{
  for(const [path,page] of Object.entries({overview:'Overview',multiview:'Multiview',archive:'Archive',settings:'Storage'})){
    for(const suffix of ['', '/'])assert.deepEqual(route(`/${path}${suffix}`),{page,cameraId:null});
  }
});

const {archiveURL,archiveTarget}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
test('live archive link keeps camera and an absolute UTC instant across midnight and repeated hours',()=>{
  for(const instant of ['2026-10-05T23:59:00Z','2026-11-01T05:30:00Z','2026-11-01T06:30:00Z']){
    const stamp=Date.parse(instant),url=new URL(archiveURL(7,stamp),'http://nvr.test');
    assert.equal(readRoute(url).page,'Archive');
    assert.deepEqual(archiveTarget(url.search),{id:7,stamp});
  }
});
test('invalid archive navigation parameters are ignored',()=>{
  for(const query of ['','?camera=0&at=2026-10-08','?camera=abc&at=2026-10-08','?camera=1&at=invalid','?camera=1&at=2026-10-08T12:00:00','?camera=9007199254740992&at=2026-10-08'])assert.equal(archiveTarget(query),null);
});
