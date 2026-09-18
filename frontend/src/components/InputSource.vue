<script setup>
import { computed } from 'vue'
import { useWorkbench } from '../composables/useWorkbench'

const emit = defineEmits(['manage-datasets'])
const { datasets, selectedDatasetId, inputText, allowCustom, selectDataset, guard } = useWorkbench()

const rowCount = computed(() => {
  try {
    const rows = JSON.parse(inputText.value)
    return Array.isArray(rows) ? rows.length : null
  } catch {
    return null
  }
})
const invalid = computed(() => !selectedDatasetId.value && inputText.value.trim() && rowCount.value === null)
const activeDataset = computed(() => datasets.value.find((item) => item.id === selectedDatasetId.value))
</script>

<template>
  <div class="input-source">
    <div class="row">
      <label class="field grow">
        <span>输入数据</span>
        <select class="select" :value="selectedDatasetId" @change="guard(() => selectDataset($event.target.value))">
          <option value="">内联 JSON 行</option>
          <option v-for="item in datasets" :key="item.id" :value="item.id">
            {{ item.name }} · {{ item.rows }} 行
          </option>
        </select>
      </label>
      <button class="btn small" @click="emit('manage-datasets')">管理数据集</button>
    </div>

    <p v-if="activeDataset" class="note">
      将提交已注册数据集 <b>{{ activeDataset.name }}</b>（{{ activeDataset.rows }} 行）。下方预览仅供参考。
    </p>
    <p v-else class="note">将按下方 JSON 数组提交输入行。</p>

    <textarea v-model="inputText" class="textarea" rows="5" spellcheck="false"
              :class="{ invalid }" :aria-invalid="invalid"
              placeholder='[{"raw_content":"  Hello   world  "}]' />

    <div class="row foot">
      <span v-if="invalid" class="tag danger">JSON 无法解析</span>
      <span v-else-if="rowCount !== null" class="tag">{{ rowCount }} 行 · {{ selectedDatasetId ? '预览' : '将提交' }}</span>
      <span class="spacer" />
      <label class="toggle">
        <input v-model="allowCustom" type="checkbox" />
        <span>允许生成新算子</span>
      </label>
    </div>
  </div>
</template>

<style scoped>
.input-source {
  display: flex;
  flex-direction: column;
  gap: 9px;
  padding: 12px;
  border-top: 1px solid var(--border);
  background: var(--surface-2);
}
.row { display: flex; align-items: flex-end; gap: 8px; }
.row.foot { align-items: center; }
.grow { flex: 1; }
.spacer { margin-left: auto; }
.note { font-size: 11px; color: var(--text-3); }
.note b { color: var(--text-2); }
.textarea.invalid { border-color: var(--danger); }
.toggle { display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--text-2); cursor: pointer; }
.toggle input { accent-color: var(--brand); width: 14px; height: 14px; }
</style>
