<script setup>
import { ref } from 'vue'
import BaseDialog from './BaseDialog.vue'
import { api } from '../api'
import { useWorkbench } from '../composables/useWorkbench'

const emit = defineEmits(['close'])
const { datasets, selectedDatasetId, loadDatasets, selectDataset, notify, guard } = useWorkbench()

const form = ref({ name: '', rows: '[{"raw_content":"  Hello   world  "}]' })
const busy = ref(false)

async function register() {
  busy.value = true
  try {
    const rows = JSON.parse(form.value.rows)
    if (!Array.isArray(rows)) throw new Error('数据集需要是 JSON 数组')
    const created = await api.registerDataset({ name: form.value.name, rows })
    await loadDatasets()
    await selectDataset(created.id)
    notify('数据集已注册', 'ok')
    emit('close')
  } catch (error) {
    notify(error.message)
  } finally {
    busy.value = false
  }
}

async function remove(id) {
  await guard(async () => {
    await api.deleteDataset(id)
    if (selectedDatasetId.value === id) selectedDatasetId.value = ''
    await loadDatasets()
  })
}
</script>

<template>
  <BaseDialog title="数据集" @close="emit('close')">
    <div class="body scroll">
      <label class="field"><span>名称</span><input v-model="form.name" class="input" placeholder="cleaning-demo" /></label>
      <label class="field"><span>数据行（JSON 数组）</span>
        <textarea v-model="form.rows" class="textarea" rows="7" spellcheck="false" />
      </label>
      <button class="btn primary submit" :disabled="busy || !form.name.trim()" @click="register">
        {{ busy ? '注册中…' : '注册数据集' }}
      </button>

      <div class="registered">
        <span class="eyebrow">已注册 · {{ datasets.length }}</span>
        <div v-for="item in datasets" :key="item.id" class="row" :class="{ active: item.id === selectedDatasetId }">
          <button class="pick truncate" @click="guard(() => selectDataset(item.id))">{{ item.name }}</button>
          <span class="tag">{{ item.rows }} 行</span>
          <button class="btn small danger" @click="remove(item.id)">删除</button>
        </div>
        <div v-if="!datasets.length" class="empty">还没有注册数据集。</div>
      </div>
    </div>
  </BaseDialog>
</template>

<style scoped>
.body { padding: 14px 16px 16px; display: flex; flex-direction: column; gap: 11px; }
.submit { height: 34px; }
.registered { display: flex; flex-direction: column; gap: 6px; padding-top: 10px; border-top: 1px dashed var(--border); }
.row { display: flex; align-items: center; gap: 8px; }
.row.active .pick { color: var(--brand); font-weight: 650; }
.pick { flex: 1; text-align: left; border: none; background: none; padding: 4px 0; font-size: 12px; color: var(--text-2); cursor: pointer; }
.pick:hover { color: var(--brand); }
</style>
