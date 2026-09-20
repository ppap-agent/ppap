<script setup lang="ts">
// Adapted from the legacy PcAuditFilePreviewPanel: readonly frame with explicit fallback.
import {onBeforeUnmount, ref, watch} from 'vue'
import {apiUrl, originalUrl, request, type Material} from './api'
import OfficePreview from './OfficePreview.vue'
const props = defineProps<{file?: Material; roundId?: string}>()
const url = ref(''), text = ref(''), notice = ref('请选择一份文件。'), loading = ref(false)
const office=ref<{available:boolean;script_url:string;config:Record<string,unknown>}|null>(null)
let controller: AbortController | undefined, revision = 0
function clear() { controller?.abort(); if (url.value) URL.revokeObjectURL(url.value); url.value = ''; text.value = '';office.value=null }
async function fallback(file:Material,current:number,reason='') {
  try{const href=originalUrl(file,props.roundId);const result=await request<{available:boolean;text?:string;notice:string}>(href.slice(apiUrl.length).replace(/\/download$/, '/preview'));if(current!==revision)return;text.value=result.available?result.text || '':'';notice.value=[reason,result.notice].filter(Boolean).join(' ')}catch{if(current===revision)notice.value='无法预览，请下载原件。'}
}
function officeFailed(){office.value=null;if(props.file)void fallback(props.file,revision,'Office 预览暂不可用，以下为文字摘录。')}
watch(() => [props.file?.file_version_id, props.roundId], async () => {
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
    <p>{{ file?.original_filename }}</p><p v-if="loading" role="status">正在加载原件…</p>
    <OfficePreview v-if="office" :key="file?.file_version_id" :script-url="office.script_url" :config="office.config" @failed="officeFailed" />
    <iframe v-if="url" :key="url" :src="url" :title="file?.original_filename || '原件预览'" sandbox="allow-same-origin" referrerpolicy="no-referrer" />
    <p v-if="notice" class="pc-muted">{{ notice }}</p><pre v-if="text">{{ text }}</pre>
  </section>
</template>
