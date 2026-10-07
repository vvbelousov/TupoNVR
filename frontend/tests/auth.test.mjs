import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';

const source=readFileSync(new URL('../src/api.ts',import.meta.url),'utf8').replace("import {t} from './i18n';","const t=(value:string)=>value;");
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {api,isUnauthorized}=await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'));

test('startup handles a plain 401 and login/logout use explicit application requests',async t=>{
  const calls=[];
  const replies=[
    new Response(JSON.stringify({detail:'Authentication required'}),{status:401}),
    new Response(JSON.stringify({ok:true})),
    new Response(JSON.stringify({username:'admin'})),
    new Response(null,{status:204}),
    new Response(JSON.stringify({detail:'Authentication required'}),{status:401}),
  ];
  t.mock.method(globalThis,'fetch',async (path,options)=>{
    calls.push({path,options});
    return replies.shift();
  });
  await assert.rejects(api('/api/config'),isUnauthorized);
  const credentials={username:'admin',password:'secret'};
  await api('/api/login',{method:'POST',body:JSON.stringify(credentials)});
  assert.deepEqual(await api('/api/config'),{username:'admin'});
  assert.equal(await api('/api/logout',{method:'POST'}),undefined);
  await assert.rejects(api('/api/config'),isUnauthorized);
  assert.deepEqual(JSON.parse(calls[1].options.body),credentials);
  assert.equal(calls[1].options.method,'POST');
  assert.equal(calls[1].options.headers['Content-Type'],'application/json');
  assert.equal(calls[3].options.method,'POST');
  for(const {options} of calls)assert.equal(new Headers(options.headers).get('Authorization'),null);
  assert.equal(calls.length,5,'No legacy authentication retry');
});
