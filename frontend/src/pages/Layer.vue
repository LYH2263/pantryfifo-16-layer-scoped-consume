<template>
  <div>
    <h1>{{ label[props.layer] || props.layer }}层</h1>
    <p class="muted">本层在架批 · 扣减默认只打本层在架批，确认前各层数字不变</p>
    <div>
      <span v-for="x in rows" :key="x.id" class="lot">
        {{ x.name }} ×{{ x.qty_remain }} · {{ x.expiry }}
        <em v-if="x.lot_layer && x.lot_layer !== x.item_layer" class="badge">品项属{{ label[x.item_layer] || x.item_layer }}</em>
      </span>
    </div>

    <h2>本层扣减</h2>
    <template v-if="itemsHere.length">
      <select v-model.number="item_id">
        <option v-for="i in itemsHere" :key="i.id" :value="i.id">{{ i.name }}</option>
      </select>
      <input type="number" v-model.number="qty" min="0" step="any" placeholder="数量" />
      <label class="muted borrow">
        <input type="checkbox" v-model="borrow" /> 本层不足时向其他层借扣（回包会明示扣了哪几层）
      </label>
      <div>
        <button @click="doPreview">预演</button>
        <button v-if="canConfirm" @click="doConfirm">确认扣减</button>
      </div>
    </template>
    <p v-else class="muted">本层暂无在架批，无法发起扣减</p>

    <div v-if="preview" class="plan">
      <template v-if="preview.ok">
        <p>将扣批号（确认前不写库）：</p>
        <ul>
          <li v-for="d in preview.deductions" :key="d.lot_id">
            批#{{ d.lot_id }} ×{{ d.take }}（{{ label[d.layer] || d.layer }} · {{ d.expiry }}）
          </li>
        </ul>
        <p v-if="preview.borrowed" class="warn">含其他层借扣：{{ touchedText(preview) }}</p>
      </template>
      <p v-else class="err">
        本层不足：缺 {{ preview.short }}{{ borrow ? '（借扣其他层后仍不足）' : '（可勾选借扣其他层）' }}
      </p>
    </div>

    <div v-if="applied" class="plan">
      <p>已扣减（以回包为准）：</p>
      <ul>
        <li v-for="d in applied.deductions" :key="d.lot_id">
          批#{{ d.lot_id }} ×{{ d.take }}（{{ label[d.layer] || d.layer }}）
        </li>
      </ul>
      <p v-if="applied.borrowed" class="warn">本次借扣了其他层：{{ touchedText(applied) }}</p>
    </div>
    <p v-if="error" class="err">{{ error }}</p>
  </div>
</template>
<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../api'
const props = defineProps({ layer: String })
const label = { upper: '上层', mid: '中层', lower: '下层' }
const rows = ref([])
const item_id = ref(null)
const qty = ref(1)
const borrow = ref(false)
const preview = ref(null)
const previewBody = ref('')
const applied = ref(null)
const error = ref('')

const itemsHere = computed(() => {
  const m = new Map()
  for (const r of rows.value) if (!m.has(r.item_id)) m.set(r.item_id, { id: r.item_id, name: r.name })
  return [...m.values()]
})
const canConfirm = computed(() => preview.value && preview.value.ok)
const touchedText = p => p.layers_touched.map(l => label[l] || l).join('、')

async function load() {
  rows.value = await api('/fridge?layer=' + props.layer)
  if (!itemsHere.value.some(i => i.id === item_id.value)) item_id.value = itemsHere.value[0]?.id ?? null
}
function resetPanel() { preview.value = null; applied.value = null; error.value = '' }
watch(() => props.layer, async () => { resetPanel(); await load() })
// any form change invalidates the preview so confirm always matches what was shown
watch([item_id, qty, borrow], () => { preview.value = null })

async function doPreview() {
  error.value = ''; applied.value = null
  const body = { item_id: item_id.value, qty: qty.value, layer: props.layer, borrow: borrow.value }
  try {
    preview.value = await api('/consume/preview', { method: 'POST', body: JSON.stringify(body) })
    previewBody.value = JSON.stringify(body)
  } catch (e) { preview.value = null; error.value = e.message }
}
async function doConfirm() {
  error.value = ''
  try {
    applied.value = await api('/consume/confirm', { method: 'POST', body: previewBody.value })
    preview.value = null
    await load()
  } catch (e) {
    preview.value = null
    error.value = '确认失败：' + e.message + '（库存可能刚被另一笔消费改动，请重新预演）'
    await load()
  }
}
onMounted(load)
</script>
