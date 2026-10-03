import { readFile,writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root=fileURLToPath(new URL('../',import.meta.url));
// Small, explicit local-module packer for this artifact only. No npm install, CDN, eval, or runtime fetch.
const files=['core/common.mjs','core/cash-engine.mjs','core/forecast.mjs','core/settlement.mjs','core/reserve.mjs','core/adapter.mjs','data/fixtures.mjs'];
let bundle='const M = Object.create(null);\n';
for(const file of files) {
  let source=await readFile(root+file,'utf8');
  source=source.replace(/^import\s+\{([^}]+)\}\s+from\s+'([^']+)';?\s*$/gm,(_,names,rel)=>{
    const resolved=path.posix.normalize(path.posix.join(path.posix.dirname(file),rel));
    if(!files.slice(0,files.indexOf(file)).includes(resolved))throw new Error('Module order: '+resolved);
    return `const {${names}} = M[${JSON.stringify(resolved)}];`;
  });
  const exports=[...source.matchAll(/^export (?:function|const)\s+([A-Za-z0-9_]+)/gm)].map(m=>m[1]);
  source=source.replace(/^export /gm,'');
  bundle+=`M[${JSON.stringify(file)}] = (() => {\n${source}\nreturn {${exports.join(',')}};\n})();\n`;
}
bundle+='globalThis.WendaiModules = M;\n';
await writeFile(root+'demo/bundle.js',bundle);
const template=await readFile(root+'demo/template.html','utf8');
const ui=await readFile(root+'demo/ui.js','utf8');
assertNoClosingTag(bundle);assertNoClosingTag(ui);
await writeFile(root+'demo/工e稳袋_离线增强演示.html',template.replace('/* CORE_BUNDLE */',bundle).replace('/* DEMO_UI */',ui));
console.log('离线单文件已生成：demo/工e稳袋_离线增强演示.html；可直接双击，不需要服务器。');
function assertNoClosingTag(s){if(/<\/script/i.test(s))throw new Error('Unexpected script closing tag');}
