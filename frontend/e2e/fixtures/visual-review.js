import {createApp,h} from 'vue';
import VisualEditor from '../../src/components/VisualEditor.vue';
import '../../src/style.css';
import '../../src/styles/visuals.css';
const graph={direction:'LR',groups:[{id:'g',label:'生产服务区',parent:null,direction:'LR'}],nodes:[{id:'a',label:'请求检查',group:'g',shape:'diamond'},{id:'b',label:'正常返回',group:null,shape:'rectangle'},{id:'c',label:'记录错误',group:null,shape:'rectangle'}],edges:[{source:'a',target:'b',label:'是',condition:'检查通过',direction:'forward',style:'solid'},{source:'a',target:'c',label:'否',condition:'检查失败',direction:'forward',style:'dashed'}],uncertainties:['失败分支的箭头方向需要核对']};
const asset={id:'f'.repeat(32),image_url:'/e2e/fixtures/visual-review.png',extraction:{kind:'mixed',title:'请求处理及运行参数',description:'',transcription:'',body_text:'',graphs:[graph],tables:[{title:'运行参数',rows:3,columns:2,header_rows:1,notes:['仅在生产区启用'],cells:['组件','功率','请求检查','100 W','正常返回','200 W'].map((text,i)=>({row:Math.floor(i/2),column:i%2,rowspan:1,colspan:1,text}))}],uncertainties:[]}};
createApp({render:()=>h('main',{style:'max-width:1200px;margin:auto;padding:16px;min-width:0'},[h(VisualEditor,{asset,issues:[{id:'1'.repeat(24),message:'失败分支的箭头方向需要核对'}],onSave:value=>{window.__savedReview=value;}})])}).mount('#review-fixture');
