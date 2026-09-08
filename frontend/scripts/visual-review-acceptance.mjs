import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';
const out=path.resolve('../artifacts/reviews/20260908-visual-rag-fix/editor');await fs.mkdir(out,{recursive:true});
const server=await createServer({server:{host:'127.0.0.1',port:0,proxy:{},strictPort:false},logLevel:'error'});await server.listen();
const port=server.httpServer.address().port;
const browser=await chromium.launch({headless:true,channel:"chrome"});const report=[];
try {for(const width of [1440,1024,390]) {const page=await browser.newPage({viewport:{width,height:940}});const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(`http://127.0.0.1:${port}/e2e/fixtures/visual-review.html`);await page.getByRole('heading',{name:'对照原图修订'}).waitFor();await page.screenshot({path:path.join(out,`editor-${width}.png`)});
 await page.getByLabel('问题 1 处理结果').selectOption('confirmed');await page.getByLabel('问题 1 核对说明').fill('对照原图箭头与失败分支一致');
 await page.getByRole('button',{name:'标记原图位置',exact:true}).nth(1).click();await page.locator('.visual-region-picker img').waitFor();await page.locator('.visual-region-picker').scrollIntoViewIfNeeded();const r=await page.locator('.visual-region-picker .visual-image-canvas').boundingBox();
 await page.mouse.move(r.x+r.width*.07,r.y+r.height*.37);await page.mouse.down();await page.mouse.move(r.x+r.width*.31,r.y+r.height*.60,{steps:8});await page.mouse.up();await page.screenshot({path:path.join(out,`region-${width}.png`)});
 await page.getByLabel('本次修订说明').fill('核对失败分支，并标记判断节点的原图区域');await page.getByRole('checkbox').check();await page.getByRole('button',{name:'确认修订并生成新版本'}).click();
 const saved=await page.evaluate(()=>window.__savedReview);const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);
 if(!saved || saved.extraction.graphs[0].nodes[0].bbox_basis!=='human' || saved.resolutions[0].disposition!=='confirmed' || overflow || errors.length) throw Error(JSON.stringify({width,saved,overflow,errors}));
 report.push({width,overflow,errors,selected_bbox:saved.extraction.graphs[0].nodes[0].bbox,receipt:saved.resolutions[0]});await page.close();}
 await fs.writeFile(path.join(out,'visual-review-browser.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));}finally{await browser.close();await server.close();}
