<template>
  <div>
    <h1>按临期消费（全层入口）</h1>
    <p class="muted">全层在架批按到期先后扣减，可能跨层；预演只列批号不落库，确认后回包可核对跨层情况。</p>
    <select v-model.number="item_id" :disabled="busy">
      <option v-for="i in items" :key="i.id" :value="i.id">{{ i.name }}</option>
    </select>
    <input type="number" v-model.number="qty" :disabled="busy" />
    <div class="btns">
      <button @click="preview" :disabled="busy || !(item_id != null && qty > 0)">预演</button>
      <button class="primary" @click="confirm" :disabled="busy || !canConfirm">确认扣减</button>
    </div>
    <p v-if="error" class="err">{{ error }}</p>
    <ConsumePlan :plan="plan" :mismatch-lots="mismatchMap" />
  </div>
</template>
<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
import ConsumePlan from '../components/ConsumePlan.vue'

const items = ref([])
const fridge = ref([])
const item_id = ref(null)
const qty = ref(1)
const plan = ref(null)
const error = ref('')
const busy = ref(false)

const canConfirm = computed(() => !!plan.value && plan.value.ok && !plan.value.committed)
const mismatchMap = computed(() =>
  Object.fromEntries(
    fridge.value
      .filter((r) => r.item_id === item_id.value && r.layer_mismatch)
      .map((r) => [r.id, true])))

onMounted(async () => {
  items.value = await api('/items')
  fridge.value = await api('/fridge')
  if (items.value[0]) item_id.value = items.value[0].id
})

function body() {
  return { item_id: item_id.value, qty: qty.value }  // 不带 layer：全层 FEFO，可跨层
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
    fridge.value = await api('/fridge')
  } catch (e) {
    const plan409 = e.status === 409 ? (e.body?.deductions ? e.body : e.body?.detail) : null
    if (plan409 && plan409.deductions !== undefined) {
      plan.value = plan409; fridge.value = await api('/fridge')
    }
    else if (e.status === 503) { error.value = '系统忙（数据库锁），请重试' }
    else { error.value = e.message }
  } finally { busy.value = false }
}
</script>
<style scoped>
.btns { display: flex; gap: 8px; margin: 4px 0 10px; }
button.primary { background: #14555c; }
.err { color: var(--alert); font-size: 13px; }
</style>
