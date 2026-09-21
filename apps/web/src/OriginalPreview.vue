<script setup lang="ts">
// Adapted from the legacy PcAuditFilePreviewPanel: readonly frame with explicit fallback.
import {computed, onBeforeUnmount, ref, watch} from 'vue'
import {apiUrl, originalUrl, request, type Material, type Finding} from './api'
import OfficePreview from './OfficePreview.vue'
const props = defineProps<{file?: Material; roundId?: string; finding?: Finding}>()
interface Excerpt {available:boolean;text?:string;notice:string;sheets?:string[];sheet?:string;rows?:{number:number;text:string}[];start_row?:number;end_row?:number;next_row?:number|null}
const excerpt=ref<Excerpt|null>(null), sheet=ref(''), startRow=ref(1), search=ref(''), searching=ref(false), lookupNote=ref('')
let excerptRevision=0
const excerptRows=computed(()=>excerpt.value?.rows?.filter(row=>!search.value || row.text.toLocaleLowerCase().includes(search.value.toLocaleLowerCase())) || [])
const textLines=computed(()=>(text.value || '').split('\n').filter(line=>!search.value || line.toLocaleLowerCase().includes(search.value.toLocaleLowerCase())))
const url = ref(''), text = ref(''), notice = ref('请选择一份文件。'), loading = ref(false), textMode = ref(false)
const office=ref<{available:boolean;script_url:string;config:Record<string,unknown>}|null>(null)
let controller: AbortController | undefined, revision = 0
function clear() { controller?.abort(); ++excerptRevision; excerpt.value=null;sheet.value='';startRow.value=1;search.value='';lookupNote.value='';searching.value=false;if (url.value) URL.revokeObjectURL(url.value); url.value = ''; text.value = '';office.value=null;textMode.value=false }
async function fallback(file:Material,current:number,reason='') {
  const lookup=++excerptRevision
  try{const href=originalUrl(file,props.roundId);const result=await request<Excerpt>(href.slice(apiUrl.length).replace(/\/download$/, '/preview'));if(current!==revision || lookup!==excerptRevision)return;excerpt.value=result;text.value=result.available?result.text || '':'';notice.value=[reason,result.notice].filter(Boolean).join(' ')}catch{if(current===revision && lookup===excerptRevision)notice.value='无法预览，请下载原件。'}
}
async function readSheet(){
  if(!props.file || !sheet.value)return
  const current=++excerptRevision;searching.value=true
  try{
    const href=originalUrl(props.file,props.roundId)
    const result=await request<Excerpt>(href.slice(apiUrl.length).replace(/\/download$/, '/preview')+'?'+new URLSearchParams({sheet:sheet.value,start_row:String(Math.max(1,Math.min(1048576,Number(startRow.value)||1)))}))
    if(current!==excerptRevision)return
    excerpt.value=result;text.value=result.text || '';notice.value=result.notice
  }catch{if(current===excerptRevision){excerpt.value=null;text.value='';notice.value='无法读取工作表，请重试或下载原件。'}}
  finally{if(current===excerptRevision)searching.value=false}
}
async function findEvidence(){
  if(!props.file)return
  if(url.value){lookupNote.value='请用原件阅读器查找检查说明中的页码或文字。';return}
  const current=revision, key=props.finding?.item_key
  textMode.value=true;loading.value=true
  await fallback(props.file,current,'')
  if(current!==revision)return
  loading.value=false
  if(key!==props.finding?.item_key)return
  const hint=[props.finding?.locator?.locator,props.finding?.suggestions,...(props.finding?.locator?.evidence?.sources || []).map(source=>source.locator)].filter(Boolean).join(' ')
  const named=excerpt.value?.sheets?.filter(name=>hint.includes(name)).sort((a,b)=>b.length-a.length)[0]
  search.value=''
  if(named){
    sheet.value=named
    const after=hint.slice(hint.indexOf(named)+named.length)
    const row=after.match(/^\s*(?:[，,:：!；;]\s*)?(?:第\s*)?(?:行\s*(\d+)|(\d+)\s*行)/)
    startRow.value=row?Math.max(1,Number(row[1] || row[2])-2):1
    lookupNote.value='按检查说明查找的位置，请对照下方原文核实。'
    await readSheet()
  }else{lookupNote.value='未识别到明确工作表，请选择工作表或输入文字查找。'}
}
defineExpose({findEvidence})
watch(()=>props.finding?.item_key,()=>{lookupNote.value='';search.value=''})
function officeFailed(){office.value=null;if(props.file)void fallback(props.file,revision,'Office 预览暂不可用，以下为文字摘录。')}
async function showText(){textMode.value=!textMode.value;if(textMode.value && props.file && !text.value){loading.value=true;const current=revision;await fallback(props.file,current,'文字摘录不执行宏，也不保留原件排版。');if(current===revision)loading.value=false}}
watch([() => props.file?.file_version_id, () => props.roundId], async () => {
  const current = ++revision; clear()
  if (!props.file) { notice.value = '请选择一份文件。'; loading.value=false; return }
  const file = props.file; loading.value = true; notice.value = ''
  try {
    const href = originalUrl(file, props.roundId)
    if (/\.(pdf|png|jpe?g|gif|webp)$/i.test(file.original_filename)) {
      controller = new AbortController()
      const response = await fetch(href, {credentials:'include', signal:controller.signal})
      if (!response.ok) throw new Error('无法加载原件，请检查权限。')
      const data = await response.arrayBuffer()
      if (current !== revision) return
      const extension = file.original_filename.split('.').pop()!.toLowerCase()
      const mime = ({pdf:'application/pdf',png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',gif:'image/gif',webp:'image/webp'} as Record<string,string>)[extension]
      url.value = URL.createObjectURL(new Blob([data], {type:mime}))
    } else {
      let result:typeof office.value=null
      try{result=await request(`/files/${file.file_id}/versions/${file.file_version_id}/office${props.roundId?`?round_id=${encodeURIComponent(props.roundId)}`:''}`)}catch{ /* Keep the authorized text preview available. */ }
      if(current!==revision)return
      if(result?.available)office.value=result
      else await fallback(file,current)
    }
  } catch(error) { if (current === revision) notice.value = error instanceof Error ? error.message : '无法预览，请下载原件。' }
  finally { if (current === revision) loading.value = false }
}, {immediate:true})
onBeforeUnmount(() => { revision++; clear() })
</script>
<template>
  <section class="audit-original"><header><h2>原件预览</h2><a v-if="file" :href="originalUrl(file, roundId)" target="_blank" rel="noopener">下载原件</a></header>
    <p class="preview-filename">{{ file?.original_filename }}</p><p v-if="loading" role="status">正在加载原件…</p>
    <button v-if="office" :disabled="loading" @click="showText">{{ textMode?'返回排版预览':'查看文字摘录' }}</button>
    <p v-if="lookupNote" class="preview-notice" role="status">{{ lookupNote }}</p>
    <p v-if="office && /\.xlsm$/i.test(file?.original_filename || '')" class="pc-muted preview-notice">含宏文件不运行宏。内容不可见时，可切换文字摘录或下载核对。</p>
    <OfficePreview v-if="office" v-show="!textMode" :key="file?.file_version_id" :script-url="office.script_url" :config="office.config" @failed="officeFailed" />
    <iframe v-if="url" :key="url" :src="url" :title="file?.original_filename || '原件预览'" sandbox="allow-same-origin" referrerpolicy="no-referrer" />
    <template v-if="!office || textMode">
      <form v-if="excerpt?.sheets?.length || sheet" class="excerpt-toolbar" @submit.prevent="readSheet">
        <label>工作表<select v-model="sheet" :disabled="loading || searching" @change="startRow=1;lookupNote='';readSheet()"><option value="" disabled>选择工作表</option><option v-for="name in excerpt?.sheets || [sheet]" :key="name" :value="name">{{ name }}</option></select></label>
        <label>起始行<input v-model.number="startRow" type="number" min="1" max="1048576" :disabled="loading || searching" /></label><button :disabled="!sheet || loading || searching">定位</button>
      </form>
      <label v-if="text" class="excerpt-search">查找当前摘录<input v-model="search" type="search" placeholder="输入文字或单元格，如 A35" /></label>
      <p v-if="notice" class="preview-notice pc-muted">{{ notice }}</p>
      <p v-if="searching" role="status">正在读取工作表…</p>
      <template v-else-if="excerpt?.sheet">
        <p class="excerpt-caption">{{ excerpt.sheet }} · 第 {{ excerpt.start_row }}–{{ excerpt.end_row || excerpt.start_row }} 行</p>
        <div class="excerpt-rows" aria-label="工作表原文摘录"><div v-for="row in excerptRows" :key="row.number" class="excerpt-row"><span>{{ row.number }}</span><code>{{ row.text }}</code></div><p v-if="!excerptRows.length" class="empty-state compact">{{ search?'当前摘录未找到匹配文字':'此范围没有非空单元格' }}</p></div>
        <div class="excerpt-pagination"><button :disabled="(excerpt.start_row || 1)<=1" @click="startRow=Math.max(1,(excerpt!.start_row || 1)-200);readSheet()">前 200 行</button><button :disabled="!excerpt.next_row" @click="startRow=excerpt!.next_row!;readSheet()">后续行 →</button></div>
      </template>
      <pre v-else-if="text">{{ textLines.join('\n') || '当前摘录未找到匹配文字' }}</pre>
    </template>
  </section>
</template>
