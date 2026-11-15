<template>
  <RouterView v-if="isInlineDiagramDemo" />
  <el-watermark content="" v-else-if="!currentIsHome">
    <el-container class="accoding">
      <GlobalNotify v-if="globalNotifyStore.isVisible" />
      <el-header class="ac-header z-10">
        <NavBar />
      </el-header>
      <el-main class="ac-main">
        <AgentWorkspaceEntry v-if="accodingStore.token" />
        <RouterView />
      </el-main>
      <el-footer
        class="ac-footer bg-blue-50 flex flex-col items-center justify-center"
      >
        <el-row class="pt-2">{{ isEnglishDemo ? 'Accoding Online Judge · English Demo' : '第五版 爱编程在线评测系统' }}</el-row>
        <el-row class="pt-2 text-gray-500">Research demonstration · No automated judging</el-row>
      </el-footer>
    </el-container>
  </el-watermark>
  <ACCodingHomePage v-else />
</template>

<script setup lang="ts">
import NavBar from "./components/NavBar.vue";
import { defineAsyncComponent } from "vue";
const AgentWorkspaceEntry = defineAsyncComponent(() => import("./agent/components/AgentWorkspaceEntry.vue"));
import GlobalNotify from "./components/GlobalNotify.vue";
import ACCodingHomePage from "./views/ACCodingHomePage.vue";
import { useGlobalNotifyStore } from "./stores/globalNotify";
import { useAccodingStore } from "./stores";
import { isEnglishDemo } from "./en-demo/mode";

const route = useRoute();
const currentIsHome = computed(() => route.path === "/home");
const isInlineDiagramDemo = computed(() => import.meta.env.DEV && route.path === "/__agent_inline_diagram_demo");

const globalNotifyStore = useGlobalNotifyStore();
const accodingStore = useAccodingStore();
const watermarkContent = computed(() => {
  if (
    accodingStore.user &&
    accodingStore.user.studentId !== undefined &&
    accodingStore.user.nickname !== undefined
  ) {
    return `${accodingStore.user.studentId}-${accodingStore.user.nickname}`;
  } else {
    return "OJ5TH please login";
  }
});
</script>

<style scoped>
.accoding {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  position: relative;
}

.ac-header {
  height: 70px;
  width: 100%;
  position: fixed;
  top: 0;
  left: 0;
  z-index: 1600;
}

.ac-main {
  flex: 1;
  padding: 75px 0px 20px 0px;
}

.ac-footer {
  height: 100px;
  @apply text-center;
}
</style>
