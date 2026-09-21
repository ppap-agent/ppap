<script setup lang="ts">
import {ref} from 'vue'
import {request, type Material} from './api'
import Modal from './Modal.vue'
interface DriveFile {token:string; name:string; type:string}
const props=defineProps<{disabled:boolean; onImport:(material:Material)=>Promise<void>}>()
const opened=ref(false)
const emit=defineEmits<{busy:[value:boolean]}>()
const folder=ref(''), files=ref<DriveFile[]>([]), selected=ref<string[]>([]), next=ref<string|null>(null), role=ref<Material['role']>('target'), message=ref(''), busy=ref(false)
const key=(file:DriveFile)=>`${file.type}:${file.token}`
const supported=(file:DriveFile)=>['file','doc','docx','sheet','bitable'].includes(file.type)
async function action(work:()=>Promise<void>){if(busy.value||props.disabled)return;busy.value=true;emit('busy',true);try{await work()}catch(e){message.value=e instanceof Error?e.message:'飞书操作失败'}finally{busy.value=false;emit('busy',false)}}
async function browse(reset=true){await action(async()=>{
  let token=folder.value.trim()
  if(token.includes('://')){const url=new URL(token);const match=url.pathname.match(/^\/drive\/folder\/([A-Za-z0-9_-]+)\/?$/);if(url.protocol!=='https:'||!url.hostname.endsWith('.feishu.cn')||!match)throw new Error('请输入飞书文件夹链接或 token。');token=match[1]}
  const query=new URLSearchParams({folder_token:token});if(!reset&&next.value)query.set('page_token',next.value)
  const result=await request<{files:DriveFile[];next_page_token?:string}>(`/integrations/feishu/files?${query}`)
  if(reset)selected.value=[];files.value=reset?result.files:files.value.concat(result.files);folder.value=token;next.value=result.next_page_token||null;message.value=`${files.value.length} 个目录项`
})}
async function importFiles(){await action(async()=>{
  for(const file of files.value.filter(file=>selected.value.includes(key(file)))){
    message.value=`正在导入 ${file.name}`
    const version=await request<{file_id:string;id:string;original_filename:string}>('/integrations/feishu/files/import','POST',{file_token:file.token,doc_type:file.type})
    selected.value=selected.value.filter(id=>id!==key(file))
    await props.onImport({file_id:version.file_id,file_version_id:version.id,original_filename:version.original_filename,role:role.value,rule_version_id:null})
  }
  message.value='导入完成。';opened.value=false
})}
</script>
<template>
  <button type="button" :disabled="disabled" @click="opened=true">从飞书导入</button>
  <Modal v-if="opened" title="从飞书导入" :busy="busy" @close="opened=false">
    <fieldset class="plain-fieldset" :disabled="disabled || busy">
      <form class="toolbar" @submit.prevent="browse()"><input v-model="folder" aria-label="飞书文件夹链接" placeholder="粘贴飞书文件夹链接" /><button>打开</button><button type="button" @click="folder='';browse()">我的云空间</button></form>
      <p v-if="message" role="status">{{ message }}</p>
      <div class="drive-files"><div v-for="file in files" :key="key(file)"><button v-if="file.type==='folder'" class="text-button" type="button" @click="folder=file.token;browse()">文件夹 · {{ file.name }} →</button><label v-else class="inline-check"><input v-model="selected" type="checkbox" :value="key(file)" :disabled="!supported(file)" /> {{ file.name }} {{ supported(file)?'':'（不支持导入）' }}</label></div></div>
      <button v-if="next" type="button" @click="browse(false)">加载更多</button>
      <div class="action-bar"><label>资料用途<select v-model="role"><option value="target">待审文件</option><option value="reference">参考资料</option></select></label><button class="pc-primary" type="button" :disabled="!selected.length" @click="importFiles">导入{{ selected.length?' '+selected.length+' 份资料':'' }}</button></div>
    </fieldset>
  </Modal>
</template>
