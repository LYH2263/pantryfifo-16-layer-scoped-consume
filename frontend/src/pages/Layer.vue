<template>
  <div>
    <h1>{{ label[props.layer] || props.layer }} 层</h1>
    <div class="shelf-lots">
      <span v-for="x in rows" :key="x.id" class="lot">
        {{ x.name }} ×{{ x.qty_remain }} · {{ x.expiry }}
        <em v-if="x.layer_mismatch" class="mm" :title="`品项默认层：${label[x.item_layer]}`">异层</em>
        <em v-if="x.data_quality === 'dirty'" class="dirty" title="脏数据">!</em>
      </span>
    </div>

    <h3>本层扣减</h3>
    <p class="muted">只扣本层在架批；本层不足不会借邻层，跨层请走「消费」页全层入口。</p>
    <select v-model.number="item_id" :disabled="busy">
      <option v-for="i in itemOptions" :key="i.item_id" :value="i.item_id">
        {{ i.name }}（本层可用 {{ i.available }} {{ i.unit }}）
      </option>
    </select>
    <input type="number" v-model.number="qty" :disabled="busy" />
    <div class="btns">
      <button @click="preview" :disabled="busy || !canSubmit">预演</button>
      <button class="primary" @click="confirm" :disabled="busy || !canConfirm">确认扣减</button>
    </div>
    <p v-if="error" class="err">{{ error }}</p>
    <ConsumePlan :plan="plan" :mismatch-lots="mismatchMap" />
  </div>
</template>
<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../api'
import ConsumePlan from '../components/ConsumePlan.vue'

const props = defineProps({ layer: String })
const label = { upper: '上层', mid: '中层', lower: '下层' }

const rows = ref([])
const item_id = ref(null)
const qty = ref(1)
const plan = ref(null)
const error = ref('')
const busy = ref(false)

const itemOptions = computed(() => {
  const map = new Map()
  for (const r of rows.value) {
    if (r.qty_remain <= 0) continue
    if (!map.has(r.item_id)) map.set(r.item_id, { item_id: r.item_id, name: r.name, unit: r.unit, available: 0 })
    map.get(r.item_id).available += r.qty_remain
  }
  return [...map.values()].map((x) => ({ ...x, available: Math.round(x.available * 1000) / 1000 }))
})
const canSubmit = computed(() => item_id.value != null && qty.value > 0)
const canConfirm = computed(() => !!plan.value && plan.value.ok && !plan.value.committed)
const mismatchMap = computed(() =>
  Object.fromEntries(rows.value.filter((r) => r.layer_mismatch).map((r) => [r.id, true])))

async function load() {
  rows.value = await api('/fridge?layer=' + props.layer)
  if (!itemOptions.value.some((i) => i.item_id === item_id.value)) {
    item_id.value = itemOptions.value[0]?.item_id ?? null
  }
}
watch(() => props.layer, load)
onMounted(load)

function body() {
  return { item_id: item_id.value, qty: qty.value, layer: props.layer }
}
async function preview() {
  error.value = ''; plan.value = null; busy.value = true
  try {
    plan.value = await api('/consume/preview', { method: 'POST', body: JSON.stringify(body()) })
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function confirm() {
  error.value = ''; busy.value = true
  try {
    const payload = { ...body(), expected_deductions: plan.value.deductions.map((d) => ({ lot_id: d.lot_id, take: d.take })) }
    plan.value = await api('/consume', { method: 'POST', body: JSON.stringify(payload) })
    await load()
  } catch (e) {
    // 409：body 即锁内重算的 plan（可能并发后变 short）；503：可重试
    const plan409 = e.status === 409 ? (e.body?.deductions ? e.body : e.body?.detail) : null
    if (plan409 && plan409.deductions !== undefined) { plan.value = plan409; await load() }
    else if (e.status === 503) { error.value = '系统忙（数据库锁），请重试' }
    else { error.value = e.message }
  } finally { busy.value = false }
}
</script>
<style scoped>
.shelf-lots { margin: 8px 0 16px; }
.mm { font-style: normal; font-size: 11px; color: var(--alert); margin-left: 4px; }
.dirty { font-style: normal; font-size: 11px; color: #b00; margin-left: 4px; font-weight: bold; }
.btns { display: flex; gap: 8px; margin: 4px 0 10px; }
button.primary { background: #14555c; }
.err { color: var(--alert); font-size: 13px; }
</style>
