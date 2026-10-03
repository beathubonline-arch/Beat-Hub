// Execute the shipped inline script with a small DOM model. This is not a visual browser test.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/../app.py','utf8');
const js=source.slice(source.indexOf('<script>')+8,source.indexOf('</script>')).replace('{{ initial_county|tojson }}','null');
const elements=new Map();
function element(id){if(!elements.has(id))elements.set(id,{id,value:'',style:{display:'none'},children:[],textContent:'',innerHTML:'',validity:{valid:true},add(x){this.children.push(x)},appendChild(x){this.children.push(x)},addEventListener(){},scrollIntoView(){},remove(){}});return elements.get(id)}
const store=new Map();let matches=[],calls=[];
const ctx={document:{getElementById:element,querySelectorAll:()=>[],createElement:()=>element('new'+Math.random())},sessionStorage:{getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v)},crypto:{randomUUID:()=> 'test-idempotency-uuid'},history:{replaceState(){}},location:{search:'',href:'https://example.org/',assign(){}},navigator:{},Option:function(text,value){return {text,value}},URL,URLSearchParams,console};
ctx.window=ctx;
ctx.fetch=async(url,options={})=>{calls.push({url,options});let data={};if(url==='/api/csrf')data={token:'test-csrf'};else if(url.startsWith('/api/candidates/resolve'))data={matches};else if(url.startsWith('/api/candidates'))data={candidates:[]};else if(url.startsWith('/api/geography'))data={wards:['Kapsoit']};else if(url.startsWith('/api/results'))data={total:0,results:[]};else if(url.startsWith('/api/ad'))data={ad:null};return {ok:true,json:async()=>data}};
vm.createContext(ctx);vm.runInContext(js,ctx);
(async()=>{
 element('county').value='Kericho';
 const races=['President','Governor','Senator','Woman Representative','Member of Parliament','MCA'];
 for(let i=0;i<5;i++){
  await ctx.advanceParticipation({complete:false,completed:races.slice(0,i+1),next_race:races[i+1]});
  assert.equal(element('supportbox').style.display,'none');
  assert.equal(element('race').value,races[i+1]);
 }
 await ctx.advanceParticipation({complete:true,completed:races,next_race:null,constituency:'Ainamoi',ward:'Kapsoit'});
 assert.equal(element('supportbox').style.display,'block');
 ctx.dismissSupport();
 await ctx.advanceParticipation({complete:true,completed:races,next_race:null},true);
 assert.equal(element('supportbox').style.display,'none');
 element('race').value='President';element('candidate').value='Nickname';
 matches=[{id:1,name:'Canonical Person'}];calls=[];
 await ctx.vote();
 assert.match(element('candidateConfirmText').textContent,/Did you mean Canonical Person/);
 assert.equal(calls.some(x=>x.url==='/api/vote'),false,'No submission before explicit confirmation');
 matches=[{id:1,name:'Same Name',party:'A'},{id:2,name:'Same Name',party:'B'}];
 await ctx.vote();
 assert.equal(element('candidateConfirm').children.length,2);
 assert.equal(calls.some(x=>x.url==='/api/vote'),false);
 matches=[];await ctx.vote();
 assert.match(element('candidateConfirmText').textContent,/No verified/);
 assert.equal(calls.some(x=>x.url==='/api/vote'),false);
 assert.match(source,/min=0.01 step=0.01/);
 assert.doesNotMatch(source,/onclick="supportAmount\([0-9]/);
 console.log('UI checks passed: five early gates, MCA completion, dismissal/refresh, explicit confirmation, ambiguity and unknown names.');
})().catch(e=>{console.error(e);process.exitCode=1});
