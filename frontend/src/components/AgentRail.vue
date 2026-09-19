<script setup>
import { computed, ref } from 'vue'
import { clockTime, relativeTime } from '../format'
import { agentMeta, eventMeta, useWorkbench } from '../composables/useWorkbench'

const { collaboration, timeline, runSkills, runEvidence, agentOutputs, isRunning } = useWorkbench()

const tabs = [
  { id: 'events', label: '事件流' },
  { id: 'skills', label: 'Skill' },
  { id: 'evidence', label: '证据' },
  { id: 'outputs', label: '模型输出' },
]
const tab = ref('events')
const focused = ref(null)

const agents = computed(() => (collaboration.value?.agents || []).filter((agent) => agent.agent !== 'system'))
const invokedSkills = computed(() => runSkills.value.filter((skill) => skill.calls?.length))
const idleSkills = computed(() => runSkills.value.filter((skill) => !skill.calls?.length))
const toneOf = (state) => ({ running: 'warn', completed: 'ok', failed: 'danger' }[state] || 'muted')
</script>

<template>
  <aside class="rail card">
    <div class="card-head">
      <h2>Agent 协作</h2>
      <span class="spacer" />
      <span class="tag" :class="isRunning ? 'warn' : 'muted'">
        <span class="dot" :class="{ pulse: isRunning }" />{{ isRunning ? '进行中' : '空闲' }}
      </span>
    </div>

    <div v-if="agents.length" class="agents">
      <button v-for="agent in agents" :key="agent.agent" class="agent" :class="{ open: focused === agent.agent }"
              @click="focused = focused === agent.agent ? null : agent.agent">
        <span class="agent-top">
          <span class="tag" :class="toneOf(agent.state)"><span class="dot" :class="{ pulse: agent.state === 'running' }" /></span>
          <b class="truncate">{{ agent.name || agentMeta(agent.agent).name }}</b>
          <small>{{ agent.events }}</small>
        </span>
        <small class="duty truncate">{{ agent.purpose || agentMeta(agent.agent).duty }}</small>
        <div v-if="focused === agent.agent" class="agent-detail">
          <p><span>状态</span><b>{{ agent.state }}</b></p>
          <p><span>作业</span><b class="mono">{{ agent.jobs?.join(', ') || '—' }}</b></p>
          <p><span>Skill</span><b>{{ agent.skills?.join(', ') || '未记录' }}</b></p>
          <p v-if="agent.updated"><span>更新</span><b>{{ relativeTime(agent.updated) }}</b></p>
        </div>
      </button>
    </div>
    <div v-else class="empty">选择一个运行以查看 Agent 协作过程。</div>

    <nav class="tabs">
      <button v-for="item in tabs" :key="item.id" :class="{ active: tab === item.id }" @click="tab = item.id">
        {{ item.label }}
      </button>
    </nav>

    <div class="panel scroll">
      <ul v-if="tab === 'events'" class="timeline">
        <li v-for="event in timeline.slice(0, 60)" :key="event.seq">
          <span class="marker" :class="eventMeta(event.event).tone"><i class="dot" /></span>
          <div>
            <b>{{ eventMeta(event.event).label }}</b>
            <small>
              <span>{{ agentMeta(event.agent).name }}</span>
              <span v-if="event.job" class="mono">· {{ event.job }}</span>
              <span v-if="event.skill" class="tag brand">{{ event.skill }}</span>
              <span v-if="event.state" class="tag">{{ event.state }}</span>
            </small>
          </div>
          <time>{{ clockTime(event.timestamp) }}</time>
        </li>
        <li v-if="!timeline.length" class="empty">Agent 开始工作后事件会显示在这里。</li>
      </ul>

      <div v-else-if="tab === 'skills'" class="list">
        <div v-for="skill in invokedSkills" :key="skill.name" class="item">
          <b>{{ skill.name }}<span class="tag brand">{{ skill.calls.length }} 次</span></b>
          <p>{{ skill.purpose }}</p>
          <small class="mono truncate">sha256 {{ skill.hash ? skill.hash.slice(0, 16) : '不可用' }}</small>
        </div>
        <details v-if="idleSkills.length" class="idle">
          <summary>未在本次运行中调用（{{ idleSkills.length }}）</summary>
          <span v-for="skill in idleSkills" :key="skill.name" class="tag">{{ skill.name }}</span>
        </details>
        <div v-if="!runSkills.length" class="empty">没有 Skill 元数据。</div>
      </div>

      <div v-else-if="tab === 'evidence'" class="list">
        <div v-for="artifact in runEvidence" :key="artifact.name" class="item">
          <b>{{ artifact.name }}<span class="tag" :class="artifact.available ? 'ok' : 'muted'">
            {{ artifact.available ? '已产出' : '未产出' }}</span></b>
          <pre v-if="artifact.value" class="mono">{{ JSON.stringify(artifact.value, null, 1).slice(0, 600) }}</pre>
        </div>
        <div v-if="!runEvidence.length" class="empty">运行产生的证据文件会显示在这里。</div>
      </div>

      <div v-else class="list">
        <div v-for="output in agentOutputs" :key="`${output.agent}-${output.attempt}`" class="item">
          <b>{{ agentMeta(output.agent).name }}<span class="tag">尝试 {{ output.attempt }}</span></b>
          <pre class="mono output">{{ output.text }}</pre>
        </div>
        <div v-if="!agentOutputs.length" class="empty">还没有模型输出记录。</div>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.rail { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
.spacer { margin-left: auto; }

.agents { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; padding: 10px 12px; }
.agent {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 9px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface-2);
  text-align: left;
  cursor: pointer;
  transition: border-color 0.14s ease;
}
.agent:hover { border-color: var(--border-strong); }
.agent.open { grid-column: 1 / -1; border-color: var(--brand); background: var(--brand-soft); }
.agent-top { display: flex; align-items: center; gap: 6px; }
.agent-top b { flex: 1; min-width: 0; font-size: 11.5px; }
.agent-top small { font-size: 10px; color: var(--text-3); }
.agent-top .tag { padding: 0 5px; height: 16px; }
.duty { max-width: 100%; font-size: 10px; color: var(--text-3); }
.agent-detail { display: flex; flex-direction: column; gap: 3px; margin-top: 6px; padding-top: 6px; border-top: 1px dashed var(--border-strong); }
.agent-detail p { display: flex; gap: 8px; font-size: 10.5px; }
.agent-detail span { width: 40px; color: var(--text-3); flex: none; }
.agent-detail b { font-weight: 600; word-break: break-all; }

.tabs { display: flex; gap: 2px; padding: 0 10px; border-bottom: 1px solid var(--border); }
.tabs button {
  flex: 1;
  padding: 8px 4px;
  border: none;
  background: none;
  border-bottom: 2px solid transparent;
  color: var(--text-3);
  font-size: 11.5px;
  font-weight: 600;
  cursor: pointer;
  transition: color 0.14s ease, border-color 0.14s ease;
}
.tabs button:hover { color: var(--text-2); }
.tabs button.active { color: var(--brand); border-bottom-color: var(--brand); }

.panel { flex: 1; min-height: 0; padding: 10px 12px 14px; }

.timeline { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.timeline li { display: flex; gap: 9px; padding: 6px 0; position: relative; }
.timeline li + li::before {
  content: '';
  position: absolute;
  left: 6px;
  top: -4px;
  height: 10px;
  width: 1px;
  background: var(--border);
}
.marker { padding-top: 5px; color: var(--text-3); }
.marker.ok { color: var(--ok); }
.marker.warn { color: var(--warn); }
.marker.danger { color: var(--danger); }
.marker.brand, .marker.info { color: var(--brand); }
.timeline div { flex: 1; min-width: 0; }
.timeline b { display: block; font-size: 11.5px; font-weight: 600; }
.timeline small { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; font-size: 10px; color: var(--text-3); }
.timeline time { font-size: 10px; color: var(--text-3); font-family: var(--font-mono); }

.list { display: flex; flex-direction: column; gap: 8px; }
.item { padding: 9px 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface-2); }
.item b { display: flex; align-items: center; gap: 6px; font-size: 11.5px; }
.item p { margin-top: 3px; font-size: 11px; color: var(--text-2); }
.item small { display: block; margin-top: 4px; font-size: 10px; color: var(--text-3); }
.item pre {
  margin: 6px 0 0;
  padding: 7px 8px;
  border-radius: var(--radius-sm);
  background: var(--surface-3);
  font-size: 10px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 150px;
  overflow: auto;
}
.item pre.output { max-height: 190px; }
.idle { font-size: 11px; color: var(--text-3); }
.idle summary { cursor: pointer; padding: 4px 0; }
.idle .tag { margin: 2px 3px 0 0; }
</style>
