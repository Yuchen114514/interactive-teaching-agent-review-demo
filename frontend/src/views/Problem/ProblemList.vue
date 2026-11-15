<template lang="">
  <div class="text-center text-2xl">
    <span class="text-gray-500">{{ isEnglishDemo ? 'Problem List' : '题目列表' }}</span>
  </div>
  <div class="mt-4 max-w-fit mx-auto">
    <el-input
      v-model="searchKey"
      style="width: 400px"
      :placeholder="isEnglishDemo ? 'Search English demo problems, then press Enter' : '输入查询题目关键字 > 回车查询'"
      clearable
      maxlength="30"
      class="mx-auto"
      @keyup.enter="searchProblemList(1)"
    >
      <template #prefix>
        <el-icon class="el-input__icon"><search /></el-icon>
      </template>
    </el-input>
  </div>
  <el-table
    :data="problemList"
    stripe
    style="width: 90%"
    @row-click="handleRowClick"
    border
    class="mx-auto mt-4 cursor-pointer"
  >
    <el-table-column prop="id" label="ID" :width="isEnglishDemo ? '100px' : '80px'" align="center" />
    <el-table-column prop="status" :label="isEnglishDemo ? 'Status' : '状态'" width="80px" align="center">
      <template #default="scope">
        <el-icon color="#67C23A" v-if="scope.row.status === 0">
          <CircleCheck />
        </el-icon>
        <el-icon color="#F56C6C" v-else-if="scope.row.status === 1">
          <CircleClose />
        </el-icon>
        <el-icon color="#909399" v-else>
          <Minus />
        </el-icon>
      </template>
    </el-table-column>
    <el-table-column prop="title" :label="isEnglishDemo ? 'Problem' : '题目'" align="center" />
    <el-table-column prop="creator" :label="isEnglishDemo ? 'Author' : '创建者'" align="center" />
    <el-table-column prop="difficulty" :label="isEnglishDemo ? 'Difficulty' : '难度'" align="center">
      <template #default="scope">
        <el-rate v-model="scope.row.difficulty" disabled />
      </template>
    </el-table-column>
    <el-table-column prop="tags" :label="isEnglishDemo ? 'Topic' : '标签'" align="center">
      <template #default="scope">
        <el-tag class="m-1" round>{{
          scope.row.tags.tagName || (isEnglishDemo ? 'No topic' : '暂无标签')
        }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column prop="subNum" :label="isEnglishDemo ? 'Submissions' : '总提交'" width="110px" align="center" />
    <el-table-column prop="acNum" :label="isEnglishDemo ? 'Accepted' : '总通过'" width="95px" align="center" />
  </el-table>
  <div class="mt-4 max-w-fit mx-auto">
    <el-pagination
      v-model:current-page="page"
      :default-page-size="pageSize"
      background
      layout="prev, pager, next, jumper"
      :page-count="pageCount"
    />
  </div>
</template>
<script setup lang="ts">
import { useAccodingStore } from "@/stores";
import {
  getProblemListService,
  getProblemListPageService,
  getSearchedProblemsService,
  getSearchedProblemsPageService,
} from "@/apis/problem";
import { openNotification } from "@/hooks";
import { isEnglishDemo } from "@/en-demo/mode";
import { demoDifficultyRating, demoProblems, searchDemoProblems } from "@/en-demo/catalog";

const page = ref(1);
const pageSize = ref(10);
const pageCount = ref(0);
const problemList = ref<any[]>([]);
const searchKey = ref("");
const store = useAccodingStore();

const updateProblemList = async () => {
  if (isEnglishDemo) {
    problemList.value = demoProblems.map(problem => ({
      id: problem.id, status: -1, title: problem.title,
      creator: "Accoding", difficulty: demoDifficultyRating(problem.difficulty),
      tags: { tagName: problem.topic }, subNum: 0, acNum: 0,
    }));
    pageCount.value = 1;
    return;
  }
  const problemListRes = (
    await getProblemListService(store.token, page.value, pageSize.value)
  ).data;
  const pageCountRes = (
    await getProblemListPageService(pageSize.value, store.token)
  ).data;
  if (problemListRes.status === 200 && pageCountRes.status === 200) {
    problemList.value = problemListRes.data;
    pageCount.value = pageCountRes.data;
  } else {
    openNotification("数据加载情况反馈", "获取数据失败", "error");
  }
};

watch(
  [page, pageSize],
  () => {
    searchKey ? searchProblemList(page.value) : updateProblemList();
  },
  { immediate: false },
);

const router = useRouter();
const handleRowClick = (row: any) => {
  router.push(`/problem/${row.id}`);
};

const searchProblemList = async (page: number) => {
  if (isEnglishDemo) {
    problemList.value = searchDemoProblems(searchKey.value).map(problem => ({
      id: problem.id, status: -1, title: problem.title,
      creator: "Accoding", difficulty: demoDifficultyRating(problem.difficulty),
      tags: { tagName: problem.topic }, subNum: 0, acNum: 0,
    }));
    pageCount.value = 1;
    return;
  }
  const pageCountRes = (
    await getSearchedProblemsPageService(
      {
        titleSearch: searchKey.value,
        limit: pageSize.value,
      },
      store.token,
    )
  ).data;
  const searchRes = (
    await getSearchedProblemsService(
      {
        titleSearch: searchKey.value,
        page: page,
        limit: pageSize.value,
      },
      store.token,
    )
  ).data;
  if (searchRes.status === 200 && pageCountRes.status === 200) {
    if (pageCountRes.data === 0) {
      openNotification("搜索结果反馈", `未查询到: ${searchKey.value}`, "info");
    } else {
      pageCount.value = pageCountRes.data;
      problemList.value = searchRes.data;
    }
  } else {
    openNotification("搜索结果反馈", `搜索失败: ${searchKey.value}`, "error");
  }
};

// 初始化挂载时获取数据
onMounted(updateProblemList);
</script>
<style lang=""></style>
