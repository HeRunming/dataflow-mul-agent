<script setup>
import { onMounted, onUnmounted, ref } from 'vue'

defineProps({ title: String, wide: Boolean })
const emit = defineEmits(['close'])
const panel = ref(null)

const onKey = (event) => {
  if (event.key === 'Escape') emit('close')
}
onMounted(() => {
  document.addEventListener('keydown', onKey)
  panel.value?.focus()
})
onUnmounted(() => document.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="backdrop" @click.self="emit('close')">
    <div ref="panel" class="dialog card" :class="{ wide }" role="dialog" aria-modal="true" tabindex="-1">
      <div class="card-head">
        <h2>{{ title }}</h2>
        <span class="spacer" />
        <slot name="actions" />
        <button class="btn small subtle" @click="emit('close')">关闭</button>
      </div>
      <slot />
    </div>
  </div>
</template>

<style scoped>
.backdrop {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: grid;
  place-items: center;
  padding: 24px;
  background: var(--overlay);
  backdrop-filter: blur(2px);
  animation: fade-up 0.16s ease;
}
.dialog {
  width: min(520px, 100%);
  max-height: min(86vh, 900px);
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-lg);
  overflow: hidden;
}
.dialog.wide { width: min(1080px, 100%); }
.spacer { margin-left: auto; }
</style>
