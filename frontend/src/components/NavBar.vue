<template>
  <el-menu
    :default-active="$route.path"
    class="el-menu-demo"
    mode="horizontal"
    :ellipsis="false"
    router
  >
    <el-menu-item index="/home" route="/home">
      <img style="height: 50px" src="../assets/accoding.svg" alt="logo" />
    </el-menu-item>
    <el-sub-menu index="/class" :disabled="!isLogin">
      <template #title>{{ isEnglishDemo ? 'Classes' : '班级' }}</template>
      <el-menu-item index="/allClasses" route="/allClasses"
        >{{ isEnglishDemo ? 'All classes' : '全部班级' }}</el-menu-item
      >
      <el-menu-item index="/myClasses" route="/myClasses"
        >{{ isEnglishDemo ? 'My classes' : '我的班级' }}</el-menu-item
      >
      <el-menu-item v-if="isSuper" index="/addClass" route="/addClass"
        >{{ isEnglishDemo ? 'Create class' : '新增班级' }}</el-menu-item
      >
      <el-menu-item v-if="isSuper" index="/addCourse" route="/addCourse">
        {{ isEnglishDemo ? 'Create course' : '新增课程（班级类别）' }}
      </el-menu-item>
    </el-sub-menu>
    <el-menu-item index="/allExams" route="/allExams" :disabled="!isLogin"
      >{{ isEnglishDemo ? 'Contests' : '比赛' }}</el-menu-item
    >
    <el-sub-menu index="/problem" :disabled="!isLogin">
      <template #title>{{ isEnglishDemo ? 'Problems' : '题目' }}</template>
      <el-menu-item index="/allProblems" route="/allProblems"
        >{{ isEnglishDemo ? 'All problems' : '全部题目' }}</el-menu-item
      >
      <el-menu-item v-if="!isEnglishDemo" index="/allSolutions" route="/allSolutions"
        >题解广场</el-menu-item
      >
      <el-menu-item v-if="!isEnglishDemo" index="/userSolutions" route="/userSolutions"
        >我的题解</el-menu-item
      >
    </el-sub-menu>
    <el-menu-item
      index="/allSubmissions"
      route="/allSubmissions"
      v-if="isSuper"
    >
        <el-button type="primary" plain round size="small">{{ isEnglishDemo ? 'Submissions' : '全部提交' }}</el-button>
    </el-menu-item>
    <!-- <el-menu-item index="/recommend" route="/recommend" :disabled="!isLogin">每日推荐</el-menu-item> -->
    <el-menu-item index="/rank" route="/rank" :disabled="!isLogin">{{ isEnglishDemo ? 'Rankings' : '排行榜' }}</el-menu-item>
    <el-menu-item index="/other" route="/other" :disabled="!isLogin">{{ isEnglishDemo ? 'More' : '其他' }}</el-menu-item>
    <div class="nav-appearance-control" role="presentation"><ThemeToggle /></div>
    <el-sub-menu index="/user" v-if="isLogin">
      <template #title>
        {{ currentUserInfo.nickname }}
        <el-tag type="success" v-if="isSuper" class="ml-2">{{ isEnglishDemo ? 'Admin' : '超级管理员' }}</el-tag>
      </template>
      <el-menu-item v-if="!isEnglishDemo" index="/profile" route="/profile">个人档案</el-menu-item>
      <el-menu-item v-if="!isEnglishDemo" index="/findBackPassword" route="/findBackPassword"
        >忘记密码</el-menu-item
      >
      <el-menu-item v-if="!isEnglishDemo" index="/changePassword" route="/changePassword"
        >更改密码</el-menu-item
      >
      <el-menu-item index="/home" @click="useLogout">
        <span class="text-red-500">{{ isEnglishDemo ? 'Leave demo' : '退出系统' }}</span>
      </el-menu-item>
    </el-sub-menu>
    <el-menu-item v-else index="/login" route="/login">{{ isEnglishDemo ? 'Enter demo' : '登录' }}</el-menu-item>
  </el-menu>
</template>

<script setup lang="ts">
import { useRouter } from "vue-router";
import ThemeToggle from "./ThemeToggle.vue";
import { useAccodingStore } from "@/stores";
import { isEnglishDemo } from "@/en-demo/mode";

const handleSelect = (key: string, keyPath: string[]) => {
  console.log(key, keyPath);
};

const store = useAccodingStore();
const router = useRouter();
const isLogin = computed(() => store.isLogin);
const currentUserInfo = computed(() => store.user);
const isSuper = computed(() => store.user.role);

const useLogout = () => {
  store._logout();
  router.push("/home");
};
</script>

<style>
.nav-appearance-control { display: flex; align-items: center; padding: 0 10px; }
.el-menu--horizontal > .el-menu-item:nth-child(1) {
  margin-right: auto;
}
</style>
