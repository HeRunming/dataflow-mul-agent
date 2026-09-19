<script setup>
import { useWorkbench } from '../composables/useWorkbench'

/* Refs are destructured so the template unwraps them like local state. */
const { toasts, dismiss } = useWorkbench()
</script>

<template>
  <div class="toast-host" role="status" aria-live="polite">
    <TransitionGroup name="toast">
      <div v-for="toast in toasts" :key="toast.id" class="toast" :class="toast.tone">
        <span class="dot" />
        <span class="toast-text">{{ toast.message }}</span>
        <button class="btn subtle small" @click="dismiss(toast.id)">关闭</button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-host {
  position: fixed;
  right: 18px;
  bottom: 18px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  z-index: 60;
  max-width: min(440px, calc(100vw - 36px));
}
.toast {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 10px 10px 13px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: var(--surface);
  box-shadow: var(--shadow);
  font-size: 12px;
  color: var(--text);
}
.toast.danger { border-color: color-mix(in srgb, var(--danger) 45%, var(--border)); color: var(--danger); }
.toast.ok { border-color: color-mix(in srgb, var(--ok) 45%, var(--border)); color: var(--ok); }
.toast.info { border-color: color-mix(in srgb, var(--info) 45%, var(--border)); color: var(--info); }
.toast-text { flex: 1; word-break: break-word; color: var(--text); }
.toast-enter-active, .toast-leave-active { transition: all 0.22s ease; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateX(14px); }
</style>
