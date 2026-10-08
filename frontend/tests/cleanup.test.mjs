import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const source=readFileSync(new URL('../src/cleanup.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {bytes,pageSelection,confirmationReady,instantChoice}=await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'));
test('page selection preserves other pages, de-duplicates and deselects only visible rows',()=>{
  assert.deepEqual(pageSelection([{id:1},{id:2}],[2,3],true),[2,3,1]);
  assert.deepEqual(pageSelection([{id:1},{id:2}],[1,2,3],false),[3]);
});
test('clear all requires exact DELETE; empty previews cannot be confirmed',()=>{
  assert.equal(confirmationReady({count:2,clear_all:true},'DELETE'),true);
  for(const text of ['','delete',' DELETE'])assert.equal(confirmationReady({count:2,clear_all:true},text),false);
  assert.equal(confirmationReady({count:2,clear_all:false},''),true);
  assert.equal(confirmationReady({count:0,clear_all:false},''),false);
});
test('local time resolution requires an explicit choice for repeated DST hours',()=>{
  const instants=[{time:'2026-11-01T05:30:00Z'},{time:'2026-11-01T06:30:00Z'}];
  assert.throws(()=>instantChoice(instants,''),/Repeated/);
  assert.equal(instantChoice(instants,'earlier'),instants[0].time);
  assert.equal(instantChoice(instants,'later'),instants[1].time);
  assert.equal(instantChoice([instants[0]],''),instants[0].time);
});

test('recording and reclaimed sizes use readable units without rounding small files to zero GB',()=>{
  assert.equal(bytes(512),'512 B');
  assert.equal(bytes(2048),'2.00 KB');
  assert.equal(bytes(1024**3),'1.00 GB');
});
