<template>
  <div v-if="plan" class="plan">
    <div v-if="plan.drifted === true" class="banner warn">
      预览后库存已变化，已按最新库存扣减（以下为确认时的实际批号）
    </div>
    <div v-if="plan.cross_layer" class="banner cross">
      本次将跨层：<b>{{ layersText }}</b>（全层入口按到期先后，不限单层）
    </div>

    <div v-if="!plan.ok && plan.reason === 'short'" class="banner short">
      库存不足：可用 <b>{{ plan.available }}</b> {{ plan.unit }}，缺 <b>{{ plan.short }}</b> {{ plan.unit }}
      <div v-if="plan.scope_layer" class="muted">本层不足时不借邻层；需要跨层请走「消费」页的全层入口。</div>
    </div>
    <div v-else-if="plan.ok" class="banner ok">
      将扣减 <b>{{ plan.requested_qty }}</b> {{ plan.unit }}，共 {{ plan.deductions.length }} 个批号
      <template v-if="plan.committed"> · 已确认（记录 #{{ plan.consumption_id }}）</template>
    </div>

    <div v-for="L in plan.layers_touched" :key="L" class="plan-layer">
      <h4>{{ label[L] || L }}<span v-if="plan.scope_layer === L" class="tag">本层</span></h4>
      <table>
        <tr v-for="d in byLayer(L)" :key="d.lot_id">
          <td>批号 #{{ d.lot_id }}</td>
          <td>{{ d.expiry || '无到期' }}</td>
          <td>扣 {{ d.take }} {{ plan.unit }}</td>
          <td><span v-if="mismatchLots[d.lot_id]" class="tag mismatch">与品项默认层不一致</span></td>
        </tr>
      </table>
    </div>
  </div>
</template>
<script setup>
import { computed } from 'vue'
const props = defineProps({
  plan: Object,
  mismatchLots: { type: Object, default: () => ({}) },
})
const label = { upper: '上层', mid: '中层', lower: '下层' }
const layersText = computed(() =>
  (props.plan?.layers_touched || []).map((x) => label[x] || x).join(' → '))
function byLayer(L) {
  return (props.plan?.deductions || []).filter((d) => d.layer === L)
}
</script>
<style scoped>
.plan { margin-top: 8px; }
.banner { border-radius: 10px; padding: 8px 12px; margin: 8px 0; font-size: 14px; }
.banner.cross { background: #e4f0d9; border: 1px solid #b6d19a; color: #3f5f1f; }
.banner.short { background: #ffe8d8; border: 1px solid #f0c9a8; color: var(--alert); }
.banner.warn { background: #fff3cd; border: 1px solid #e6d28a; color: #7a5c00; }
.banner.ok { background: #dff3f0; border: 1px solid #9fd3cc; color: var(--teal); }
.plan-layer { background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 8px 12px; margin: 6px 0; }
.plan-layer h4 { margin: 4px 0; font-size: 13px; color: var(--teal); }
table { border-collapse: collapse; width: 100%; }
td { padding: 4px 8px; font-size: 13px; }
.tag { font-size: 11px; border-radius: 999px; padding: 1px 8px; margin-left: 6px; background: var(--ice); color: var(--teal); }
.tag.mismatch { background: #f6e2c8; color: var(--alert); }
</style>
