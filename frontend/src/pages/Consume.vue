<template>
  <div>
    <h1>按临期消费（全层）</h1>
    <p class="muted">全层入口：按到期先后跨层 FEFO，回包可见是否跨层、各扣了哪层</p>
    <select v-model.number="item_id"><option v-for="i in items" :value="i.id">{{ i.name }}</option></select>
    <input type="number" v-model.number="qty" min="0" step="any" placeholder="数量" />
    <input v-model="note" placeholder="备注（可选）" />
    <div>
      <button @click="doPreview">预演</button>
      <button v-if="canConfirm" @click="doConfirm">确认扣减</button>
    </div>

    <div v-if="preview" class="plan">
      <template v-if="preview.ok">
        <p>将扣批号{{ preview.cross_layer ? '（跨层）' : '（单层）' }}，确认前各层数字不变：</p>
        <ul>
          <li v-for="d in preview.deductions" :key="d.lot_id">
            批#{{ d.lot_id }} ×{{ d.take }}（{{ label[d.layer] || d.layer }} · {{ d.expiry }}）
          </li>
        </ul>
      </template>
      <p v-else class="err">全层库存不足：缺 {{ preview.short }}</p>
    </div>

    <div v-if="applied" class="plan">
      <p>
        已扣减（以回包为准）：
        <span v-if="applied.cross_layer" class="warn">跨层 · {{ touchedText(applied) }}</span>
        <span v-else>单层 · {{ touchedText(applied) }}</span>
      </p>
      <ul>
        <li v-for="d in applied.deductions" :key="d.lot_id">
          批#{{ d.lot_id }} ×{{ d.take }}（{{ label[d.layer] || d.layer }}）
        </li>
      </ul>
    </div>
    <p v-if="error" class="err">{{ error }}</p>
  </div>
</template>
<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../api'
const label = { upper: '上层', mid: '中层', lower: '下层' }
const items = ref([])
const item_id = ref(1)
const qty = ref(1)
const note = ref('')
const preview = ref(null)
const previewBody = ref('')
const applied = ref(null)
const error = ref('')

const canConfirm = computed(() => preview.value && preview.value.ok)
const touchedText = p => p.layers_touched.map(l => label[l] || l).join('、')
watch([item_id, qty], () => { preview.value = null })

onMounted(async () => { items.value = await api('/items'); if (items.value[0]) item_id.value = items.value[0].id })

async function doPreview() {
  error.value = ''; applied.value = null
  const body = { item_id: item_id.value, qty: qty.value, note: note.value }
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
  } catch (e) {
    preview.value = null
    error.value = '确认失败：' + e.message + '（库存可能刚被另一笔消费改动，请重新预演）'
  }
}
</script>
