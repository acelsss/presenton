import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {runTask} from '../presentation-export/node_modules/@presenton/export-core/dist/index.js';
import puppeteer from '../presentation-export/node_modules/puppeteer/lib/puppeteer/puppeteer.js';

const here=path.dirname(fileURLToPath(import.meta.url));
const root=path.resolve(here,'..');
const out=process.argv[2] ? path.resolve(process.argv[2]) : path.join(root,'out/claude-ppt-demo');
const doc=JSON.parse(await fs.readFile(path.join(out,'render-snapshot.json'),'utf8'));
const started=Date.now();
const browser=await puppeteer.launch({headless:true,executablePath:process.env.PUPPETEER_EXECUTABLE_PATH ?? 'C:/Users/12555/AppData/Local/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-win64/chrome-headless-shell.exe',args:['--no-sandbox','--disable-gpu']});
const opts={outputDirectory:out,tempDirectory:path.join(out,'.export-temp'),getBrowser:()=>browser};
await fs.mkdir(opts.tempDirectory,{recursive:true});
const escape=s=>String(s).replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const colorKeys={primary:'primary-color',background:'background-color',card:'card-color',stroke:'stroke',primary_text:'primary-text',background_text:'background-text'};
const colors=doc.theme?.colors??{};
const variables=Object.entries(colors).map(([key,value])=>`--${colorKeys[key]??key.replaceAll('_','-')}:${value}`).join(';');
const themeFont=doc.theme?.fonts?.textFont;
let fontCss='';
if(themeFont?.url?.startsWith('data:')){
  fontCss=`@font-face{font-family:'${themeFont.name}';src:url(${themeFont.url})}`;
}

try{
 const parts=[];const heads=[];const renderTimes=[];
 for(const slide of doc.slides){
  const t=Date.now();
  const html=await runTask({type:'json-to-html',width:1280,height:720,ui:slide.ui},opts);
  await fs.writeFile(path.join(out,`slide-${slide.index+1}.html`),html);
  const page=await browser.newPage();await page.setContent(html);
  const parsed=await page.evaluate(()=>({head:document.head.innerHTML,body:document.body.innerHTML}));await page.close();
  heads.push(parsed.head);
  parts.push(`<section class="main-slide" data-speaker-note="${escape(slide.speakerNote??'')}">${parsed.body}</section>`);
  renderTimes.push({index:slide.index,ms:Date.now()-t});
 }
 const html=`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">${heads.join('\n')}<style>${fontCss}
 :root{${variables};--heading-font-family:'${themeFont?.name??'Microsoft YaHei'}','Microsoft YaHei',sans-serif;--body-font-family:'${themeFont?.name??'Microsoft YaHei'}','Microsoft YaHei',sans-serif}
 html,body{margin:0;padding:0;width:1280px;height:auto;overflow:visible;font-family:'${themeFont?.name??'Microsoft YaHei'}','Microsoft YaHei',sans-serif}
 #presentation-slides-wrapper{display:block;width:1280px;margin:0;padding:0}
 .main-slide{position:relative;width:1280px;height:720px;overflow:hidden;break-after:page}
 @page{size:1280px 720px;margin:0}
 </style></head><body><div id="presentation-slides-wrapper">${parts.join('\n')}</div></body></html>`;
 await fs.writeFile(path.join(out,'presentation.html'),html);
 const page=await browser.newPage();await page.setViewport({width:1280,height:720,deviceScaleFactor:1});
 await page.setContent(html,{waitUntil:'networkidle0'});await page.evaluate(()=>document.fonts.ready);
 const slides=await page.$$('#presentation-slides-wrapper > section');
 const geometry=[];
 for(let i=0;i<slides.length;i++){
  await slides[i].screenshot({path:path.join(out,`preview-${i+1}.png`)});
  geometry.push(await slides[i].evaluate(el=>{
   const r=el.getBoundingClientRect();const overflows=[];const texts=[];
   const walk=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);
   while(walk.nextNode()){
    const node=walk.currentNode;if(!node.textContent.trim())continue;
    if(['STYLE','SCRIPT'].includes(node.parentElement.tagName))continue;
    const range=document.createRange();range.selectNodeContents(node);const rect=range.getBoundingClientRect();
    texts.push({text:node.textContent.trim(),x:rect.x-r.x,y:rect.y-r.y,width:rect.width,height:rect.height});
    if(rect.x<r.x-2||rect.right>r.right+2||rect.y<r.y-2||rect.bottom>r.bottom+2)overflows.push(node.textContent.trim());
   }
   return {textCount:texts.length,texts,overflows,brokenImages:[...el.querySelectorAll('img')].filter(i=>!i.complete||i.naturalWidth===0).length};
  }));
 }
 await fs.writeFile(path.join(out,'geometry.json'),JSON.stringify(geometry,null,2));
 console.log('Rendered native slides:',slides.length,'overflow counts:',geometry.map(x=>x.overflows.length));
 const exportStart=Date.now();
 const result=await runTask({type:'html-to-any',html,format:'pptx',title:doc.title},opts);
const target=path.join(out,process.argv[2] ? 'presentation.pptx' : '外部Agent制作PPT-ClaudeCode.pptx');await fs.copyFile(result.filePath,target);
 await fs.writeFile(path.join(out,'export-timing.json'),JSON.stringify({renderTimes,exportMs:Date.now()-exportStart,totalMs:Date.now()-started,sourceRevision:doc.revision},null,2));
 console.log('PPTX:',target);
}finally{await browser.close()}
