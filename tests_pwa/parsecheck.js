const acorn=require("acorn"),fs=require("fs");
const f=process.argv[2];
const h=fs.readFileSync(f,"utf8").replace(/\r/g,"").replace(/\{\{[^}]*\}\}/g,"[]");
const re=/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g; let m,n=0;
while((m=re.exec(h))){n++; try{acorn.parse(m[1],{ecmaVersion:2022});}catch(e){
  const lines=m[1].split("\n"); console.log("  script #"+n+": "+e.message+"\n  >> "+(lines[e.loc.line-1]||"").slice(Math.max(0,e.loc.column-60),e.loc.column+60));}}
console.log("  ("+n+" inline scripts)");
