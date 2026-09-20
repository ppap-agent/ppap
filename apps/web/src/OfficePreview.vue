<script lang="ts">
interface Editor {destroyEditor:()=>void}
declare global {interface Window {DocsAPI?:{DocEditor:new(id:string,config:Record<string,unknown>)=>Editor}}}
let loadedScript:Promise<void>|undefined
let scriptSource=''
function loadEditor(src:string) {
  if(scriptSource && scriptSource!==src) return Promise.reject(new Error('Office 服务地址已变化，请刷新页面。'))
  if(loadedScript)return loadedScript
  scriptSource=src
  loadedScript=new Promise<void>((resolve,reject)=>{
    const script=document.createElement('script');script.src=src;script.async=true;script.referrerPolicy='no-referrer'
    const timer=setTimeout(()=>{script.remove();loadedScript=undefined;reject(new Error('Office 服务连接超时'))},15000)
    script.onload=()=>{clearTimeout(timer);resolve()}
    script.onerror=()=>{clearTimeout(timer);script.remove();loadedScript=undefined;reject(new Error('Office 服务不可用'))}
    document.head.append(script)
  })
  return loadedScript
}
</script>
<script setup lang="ts">
import {onBeforeUnmount,onMounted,ref} from 'vue'
const props=defineProps<{scriptUrl:string;config:Record<string,unknown>}>()
const emit=defineEmits<{failed:[]}>()
const id='office-'+crypto.randomUUID(), loading=ref(true)
let editor:Editor|undefined, disposed=false
onMounted(async()=>{try{await loadEditor(props.scriptUrl);if(disposed)return;if(!window.DocsAPI)throw new Error('Office 服务未就绪');editor=new window.DocsAPI.DocEditor(id,{...props.config,events:{onDocumentReady:()=>{loading.value=false},onError:()=>{if(!disposed)emit('failed')}}})}catch{if(!disposed)emit('failed')}})
onBeforeUnmount(()=>{disposed=true;editor?.destroyEditor()})
</script>
<template><div class="office-preview"><p v-if="loading" role="status">正在加载 Office 排版预览…</p><div :id="id" /></div></template>
