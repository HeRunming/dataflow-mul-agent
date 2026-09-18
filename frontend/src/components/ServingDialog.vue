<script setup>
import { ref } from 'vue'
import BaseDialog from './BaseDialog.vue'
import { api } from '../api'
import { useWorkbench } from '../composables/useWorkbench'

const emit = defineEmits(['close'])
const { resources, models, loadResources, notify } = useWorkbench()

const form = ref({
  name: 'llm_default',
  api_url: 'https://api.openai.com/v1',
  model_name: '',
  api_key: '',
  key_name_of_api_key: '',
  max_workers: 4,
  max_tokens: 4096,
  temperature: 0.2,
})
const busy = ref(false)
const discovering = ref(false)

async function discover() {
  discovering.value = true
  try {
    models.value = (await api.models({ api_url: form.value.api_url, api_key: form.value.api_key })).models
    if (!form.value.model_name && models.value.length) form.value.model_name = models.value[0].id
    notify(`发现 ${models.value.length} 个模型`, 'ok')
  } catch (error) {
    notify(error.message)
  } finally {
    discovering.value = false
  }
}

async function register() {
  busy.value = true
  try {
    await api.registerResource(form.value)
    await loadResources()
    form.value.api_key = ''
    notify('Serving 已注册', 'ok')
    emit('close')
  } catch (error) {
    notify(error.message)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <BaseDialog title="注册 LLM Serving / API" @close="emit('close')">
    <div class="body scroll">
      <p class="hint">
        Pipeline 中的 LLM 算子通过这里注册的 Chat Completions 服务运行。密钥只写入后端密钥登记表，
        不会出现在生成的 pipeline 代码或运行产物中。
      </p>

      <div class="grid">
        <label class="field"><span>资源名</span><input v-model="form.name" class="input" placeholder="llm_default" /></label>
        <label class="field"><span>模型</span>
          <select v-if="models.length" v-model="form.model_name" class="select">
            <option v-for="model in models" :key="model.id" :value="model.id">{{ model.id }}</option>
          </select>
          <input v-else v-model="form.model_name" class="input" placeholder="gpt-4o" />
        </label>
      </div>

      <label class="field"><span>API 地址</span>
        <input v-model="form.api_url" class="input" placeholder="https://provider.example/v1" />
      </label>

      <div class="grid">
        <label class="field"><span>API Key</span>
          <input v-model="form.api_key" class="input" type="password" placeholder="仅存于后端" />
        </label>
        <div class="field"><span>模型列表</span>
          <button class="btn" :disabled="discovering" @click="discover">
            {{ discovering ? '读取中…' : '读取 /models' }}
          </button>
        </div>
      </div>

      <div class="grid three">
        <label class="field"><span>并发度</span><input v-model.number="form.max_workers" class="input" type="number" min="1" max="128" /></label>
        <label class="field"><span>最大输出 tokens</span><input v-model.number="form.max_tokens" class="input" type="number" min="1" max="32768" /></label>
        <label class="field"><span>Temperature</span><input v-model.number="form.temperature" class="input" type="number" min="0" max="2" step="0.1" /></label>
      </div>

      <label class="field"><span>可选：自定义环境变量名</span>
        <input v-model="form.key_name_of_api_key" class="input" placeholder="DF_PIPELINE_…" />
      </label>

      <button class="btn primary submit" :disabled="busy" @click="register">
        {{ busy ? '注册中…' : '注册 Serving' }}
      </button>

      <div v-if="resources.length" class="registered">
        <span class="eyebrow">已注册</span>
        <div v-for="item in resources" :key="item.name" class="row">
          <b class="mono">{{ item.name }}</b>
          <span class="truncate">{{ item.model_name }}</span>
          <span class="tag">{{ item.max_workers || '—' }} workers</span>
          <span class="tag" :class="item.configured ? 'ok' : 'warn'">{{ item.configured ? '就绪' : '缺少密钥' }}</span>
        </div>
      </div>
    </div>
  </BaseDialog>
</template>

<style scoped>
.body { padding: 14px 16px 16px; display: flex; flex-direction: column; gap: 11px; }
.hint { font-size: 11.5px; color: var(--text-3); line-height: 1.6; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.grid.three { grid-template-columns: repeat(3, 1fr); }
.submit { height: 34px; margin-top: 2px; }
.registered { display: flex; flex-direction: column; gap: 6px; padding-top: 10px; border-top: 1px dashed var(--border); }
.row { display: flex; align-items: center; gap: 8px; font-size: 11.5px; color: var(--text-2); }
.row b { min-width: 90px; }
@media (max-width: 560px) { .grid, .grid.three { grid-template-columns: 1fr; } }
</style>
