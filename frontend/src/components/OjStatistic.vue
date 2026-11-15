<template lang="">
  <el-row class="text-center bg-blue-50 p-4 rounded-lg">
    <el-col :span="6">
      <el-statistic title="当前活跃人数" :value="532" value-style="color: #5A9CF8" />
    </el-col>
    <el-col :span="6">
      <el-statistic :value="76" value-style="color: #5A9CF8">
        <template #title>
          <div style="display: inline-flex; align-items: center">
            今日过题情况
            <el-icon style="margin-left: 4px" :size="12">
              <Upload />
            </el-icon>
          </div>
        </template>
        <template #suffix><span class="text-gray-500">/130</span></template>
      </el-statistic>
    </el-col>
    <el-col :span="6">
      <el-statistic title="平台总提交量" :value="outputValue" value-style="color: #5A9CF8" />
    </el-col>
    <el-col :span="6">
      <el-statistic title="题解与交流数量" :value="562" value-style="color: #5A9CF8">
        <template #suffix>
          <el-icon color="#5A9CF8" size="small">
            <EditPen />
          </el-icon>
        </template>
      </el-statistic>
    </el-col>
  </el-row>
</template>

<script setup lang="ts">
import { useTransition } from '@vueuse/core'

const source = ref(0)
const outputValue = useTransition(source, {
  duration: 1000,
})
source.value = 172000
</script>

<style lang=""></style>
