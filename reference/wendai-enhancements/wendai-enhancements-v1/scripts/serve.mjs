import http from 'node:http';
import {readFile} from 'node:fs/promises';
const file=new URL('../demo/工e稳袋_离线增强演示.html',import.meta.url);
const server=http.createServer(async(req,res)=>{
  if(req.url!=='/'&&req.url!=='/index.html'){res.writeHead(404);res.end('Not found');return;}
  try{res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'});res.end(await readFile(file));}
  catch{res.writeHead(500);res.end('Run npm run build:offline first');}
});
server.listen(4178,'127.0.0.1',()=>console.log('本地离线演示 http://127.0.0.1:4178 。没有账户、接口或数据上传。'));
