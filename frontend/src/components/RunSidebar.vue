<script setup>
import { computed } from 'vue'
import { relativeTime, shortId } from '../format'
import { RUNNING_STATES, stateMeta, useWorkbench } from '../composables/useWorkbench'

const { runs, visibleRuns, runQuery, runFilter, selectedId, deletingRuns, loadingRuns,
        selectRun, deleteRun, startConversation, startingConversation, guard } = useWorkbench()

const filters = [
  { id: 'all', label: '全部' },
  { id: 'active', label: '进行中' },
  { id: 'done', label: '已完成' },
  { id: 'attention', label: '需处理' },
]

const counts = computed(() => ({
  all: runs.value.length,
  active: runs.value.filter((run) => RUNNING_STATES.includes(run.state)).length,
  attention: runs.value.filter((run) => ['BLOCKED', 'REFUSED', 'RESOURCE_REQUIRED', 'APPROVAL_REQUIRED'].includes(run.state)).length,
}))
</script>

<template>
  <aside class="sidebar card">
    <div class="card-head">
      <h2>运行记录</h2>
      <span class="tag">{{ counts.all }}</span>
      <span class="spacer" />
      <button class="btn small primary" :disabled="startingConversation" @click="startConversation">
        {{ startingConversation ? '创建中…' : '新对话' }}
      </button>
    </div>

    <div class="controls">
      <input v-model="runQuery" class="input" type="search" placeholder="搜索需求或 run id…" aria-label="搜索运行记录" />
      <div class="filters" role="tablist">
        <button v-for="item in filters" :key="item.id" class="filter" role="tab"
                :aria-selected="runFilter === item.id" :class="{ active: runFilter === item.id }"
                @click="runFilter = item.id">
          {{ item.label }}
          <i v-if="item.id === 'attention' && counts.attention" class="badge">{{ counts.attention }}</i>
          <i v-else-if="item.id === 'active' && counts.active" class="badge live">{{ counts.active }}</i>
        </button>
      </div>
    </div>

    <div class="run-list scroll" role="list">
      <template v-if="loadingRuns && !runs.length">
        <div v-for="n in 4" :key="n" class="skeleton run-skeleton" />
      </template>
      <button v-for="run in visibleRuns" :key="run.run_id" class="run" role="listitem"
              :class="{ active: run.run_id === selectedId, removing: deletingRuns.has(run.run_id) }"
              @click="guard(() => selectRun(run.run_id))">
        <span class="state-dot" :class="stateMeta(run.state).tone">
          <i class="dot" :class="{ pulse: RUNNING_STATES.includes(run.state) }" />
        </span>
        <span class="run-body">
          <b class="truncate">{{ run.request || '未命名任务' }}</b>
          <small>
            <span class="tag" :class="stateMeta(run.state).tone">{{ stateMeta(run.state).label }}</span>
            <span class="mono">{{ shortId(run.run_id) }}</span>
            <span v-if="run.updated">· {{ relativeTime(run.updated) }}</span>
          </small>
          <small v-if="run.operators?.length" class="operators truncate">{{ run.operators.filter(Boolean).join(' → ') }}</small>
        </span>
        <span class="remove" :title="deletingRuns.has(run.run_id) ? '删除中…' : '删除运行'"
              @click.stop="deleteRun(run.run_id)">
          {{ deletingRuns.has(run.run_id) ? '…' : '×' }}
        </span>
      </button>
      <div v-if="!visibleRuns.length && !loadingRuns" class="empty">
        <strong>{{ runs.length ? '没有匹配的运行' : '还没有运行记录' }}</strong>
        <span>{{ runs.length ? '换个关键字或筛选条件' : '在右侧助手中描述一个数据任务即可开始' }}</span>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
.controls { display: flex; flex-direction: column; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--border); }
.filters { display: flex; gap: 4px; }
.filter {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  height: 25px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-3);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.14s ease, color 0.14s ease;
}
.filter:hover { background: var(--surface-3); color: var(--text-2); }
.filter.active { background: var(--brand-soft); color: var(--brand); }
.badge {
  font-style: normal;
  font-size: 9.5px;
  padding: 0 5px;
  border-radius: 99px;
  background: var(--danger-soft);
  color: var(--danger);
}
.badge.live { background: var(--warn-soft); color: var(--warn); }

.run-list { flex: 1; min-height: 0; padding: 8px; display: flex; flex-direction: column; gap: 4px; }
.run-skeleton { height: 58px; }
.run {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  width: 100%;
  padding: 9px 8px 9px 10px;
  border: 1px solid transparent;
  border-radius: var(--radius);
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background 0.14s ease, border-color 0.14s ease;
}
.run:hover { background: var(--surface-2); }
.run.active { background: var(--brand-soft); border-color: color-mix(in srgb, var(--brand) 32%, transparent); }
.run.removing { opacity: 0.45; pointer-events: none; }
.state-dot { padding-top: 5px; }
.state-dot.ok { color: var(--ok); }
.state-dot.warn { color: var(--warn); }
.state-dot.danger { color: var(--danger); }
.state-dot.info, .state-dot.brand { color: var(--brand); }
.state-dot.muted { color: var(--text-3); }
.run-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.run-body b { font-size: 12.5px; font-weight: 600; }
.run-body small { display: flex; align-items: center; gap: 6px; font-size: 10.5px; color: var(--text-3); }
.operators { display: block; font-size: 10px; color: var(--text-3); opacity: 0.9; }
.remove {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: var(--radius-sm);
  color: var(--text-3);
  font-size: 14px;
  opacity: 0;
  transition: opacity 0.14s ease, background 0.14s ease;
}
.run:hover .remove, .run.active .remove { opacity: 1; }
.remove:hover { background: var(--danger-soft); color: var(--danger); }
</style>
