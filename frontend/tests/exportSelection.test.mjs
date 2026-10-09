import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const source=readFileSync(new URL('../src/ExportSelection.tsx',import.meta.url),'utf8');
// Load the pure timestamp operation without the React renderer.
const operation=source.slice(source.indexOf('export function moveBoundary'),source.indexOf('export function ExportSelection'));
const compiled=ts.transpileModule(operation,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {moveBoundary}=await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'));
test('boundaries remain ordered with second precision',()=>{
  assert.deepEqual(moveBoundary({start:1000,end:5000},'start',9999),{start:4000,end:5000});
  assert.deepEqual(moveBoundary({start:1000,end:5000},'end',0),{start:1000,end:2000});
  assert.deepEqual(moveBoundary({start:1000,end:5000},'start',2345),{start:2000,end:5000});
});
test('absolute boundaries survive midnight and are independent of viewport',()=>{
  const start=Date.parse('2026-11-01T01:59:59-04:00'),end=Date.parse('2026-11-01T01:00:01-05:00');
  assert.equal(end-start,2000);
  assert.deepEqual(moveBoundary({start,end},'end',end+60000),{start,end:end+60000});
  const midnight=Date.parse('2026-10-09T23:59:59Z');
  assert.equal(moveBoundary({start:midnight,end:midnight+2000},'end',midnight+60000).end,midnight+60000);
});
