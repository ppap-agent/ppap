<script setup lang="ts">
import {onBeforeUnmount, onMounted, ref} from 'vue'
defineProps<{title:string; busy?:boolean}>()
const emit = defineEmits<{close:[]}>()
const dialog = ref<HTMLDialogElement>()
onMounted(() => dialog.value?.showModal())
onBeforeUnmount(() => dialog.value?.close())
</script>
<template>
  <dialog ref="dialog" class="modal" aria-labelledby="modal-title" @cancel.prevent="!busy && emit('close')">
    <header class="modal-heading"><h2 id="modal-title">{{ title }}</h2><button :disabled="busy" aria-label="关闭" @click="emit('close')">关闭</button></header>
    <div class="modal-content"><slot /></div>
  </dialog>
</template>
