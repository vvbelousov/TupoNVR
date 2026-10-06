import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {runInNewContext} from 'node:vm';

const source=readFileSync(new URL('../public/reader.js',import.meta.url),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));

function harness(delayedPost=false){
  const calls=[];
  let resolvePost;
  let postedResolve;
  const posted=new Promise(resolve=>{postedResolve=resolve});
  // Advertise the optional codecs so capability probes do not need real ICE.
  class Peer {
    addTransceiver(){}
    createDataChannel(){}
    async createOffer(){return {sdp:'m=video 9 UDP/TLS/RTP/SAVPF 96\r\na=rtpmap:96 H264/90000\r\na=ice-ufrag:x\r\na=ice-pwd:y\r\n pcma/8000/2 multiopus/48000/6 L16/48000/2'}}
    async setLocalDescription(){}
    async setRemoteDescription(){}
    close(){}
  }
  const context={window:{setTimeout,clearTimeout},clearTimeout,URL,RTCPeerConnection:Peer,RTCSessionDescription:class {constructor(value){Object.assign(this,value)}},btoa:s=>Buffer.from(s).toString('base64'),fetch:async(url,opts)=>{
    calls.push({url,method:opts.method});
    if(opts.method==='OPTIONS')return {headers:{get:()=>null}};
    if(opts.method==='POST'){
      postedResolve();
      if(delayedPost)await new Promise(resolve=>{resolvePost=resolve});
      return {status:201,headers:{get:()=>'/session/1'},text:async()=>''};
    }
    return {status:204};
  }};
  runInNewContext(source,context);
  return {Reader:context.window.MediaMTXWebRTCReader,calls,posted,finishPost:()=>resolvePost()};
}

test('closing a reader deletes its WHEP session',{timeout:2000},async()=>{
  const h=harness();
  const reader=new h.Reader({url:'http://localhost/cam_1/whep'});
  await h.posted;
  await tick();
  reader.close();
  reader.close();
  assert.equal(h.calls.filter(c=>c.method==='DELETE').length,1);
  assert.equal(h.calls.at(-1).url,'http://localhost/session/1');
});

test('a session created after close is also deleted',{timeout:2000},async()=>{
  const h=harness(true);
  const reader=new h.Reader({url:'http://localhost/cam_1/whep'});
  await h.posted;
  reader.close();
  h.finishPost();
  await tick();
  assert.equal(h.calls.filter(c=>c.method==='DELETE').length,1);
});
