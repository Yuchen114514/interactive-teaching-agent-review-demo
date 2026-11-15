<template>
  <div
    class="fixed top-0 w-full flex justify-center items-center bg-blue-300 h-20"
    style="z-index: 9999"
  >
    <div class="text-center text-white">
      <span>{{ message }}</span>
      <el-button
        type="primary"
        round
        size="small"
        class="ml-4"
        @click="closeNotification"
        >关闭</el-button
      >
    </div>
  </div>
</template>
<script setup lang="ts">
import { useGlobalNotifyStore } from "@/stores/globalNotify";
const message = ref("");
const scrollPosition = ref(0);
const store = useGlobalNotifyStore();

onMounted(() => {
  message.value = store.message;
});

const closeNotification = () => {
  store.closeGlobalNotify();
};
</script>

<style scoped>
:deep(.markdown-body) {
  font-family: "Monaco", serif;
}

:deep(.markdown-body code) {
  background:rgb(255, 255, 255, 0) none !important;
}

:deep(.markdown-body .hljs) {
  background:rgb(255, 255, 255, 0) none !important;
  font-size: 17px;
}
</style>
