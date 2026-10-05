<template>
  <div>
    <h1>冰箱分层</h1>
    <p class="muted">竖列分层按<b>批所在层</b>归列（批号可与品项默认层不一致）· FEFO 消费走「消费」页或各层页</p>
    <div class="fridge">
      <section v-for="L in layers" :key="L" class="shelf">
        <h3>{{ label[L] }}</h3>
        <span v-for="x in by(L)" :key="x.id" class="lot">
          {{ x.name }} ×{{ x.qty_remain }} · {{ x.expiry }}
          <em v-if="x.layer_mismatch" class="mm" :title="`品项默认层：${label[x.item_layer]}`">异层</em>
          <em v-if="x.data_quality === 'dirty'" class="dirty" title="脏数据（如负余量）">!</em>
        </span>
        <span v-if="!by(L).length" class="muted">空</span>
      </section>
    </div>
    <button style="margin-top:12px" @click="sweep">过期下架</button>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const rows = ref([])
const layers = ['upper','mid','lower']
const label = { upper: '上层', mid: '中层', lower: '下层' }
function by(L) { return rows.value.filter(r => r.layer === L) }
async function load() { rows.value = await api('/fridge') }
async function sweep() { await api('/expire-sweep', { method: 'POST', body: '{}' }); await load() }
onMounted(load)
</script>
<style scoped>
.mm { font-style: normal; font-size: 11px; color: var(--alert); margin-left: 4px; }
.dirty { font-style: normal; font-size: 11px; color: #b00; margin-left: 4px; font-weight: bold; }
</style>
