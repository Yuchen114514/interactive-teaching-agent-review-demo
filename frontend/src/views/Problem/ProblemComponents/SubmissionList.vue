<template lang="">
  <el-table
    @row-click="handleRowClick"
    class="cursor-pointer"
    :data="submissionList"
    stripe
    border
    v-loading="getSubmissionListLoading"
  >
    <el-table-column prop="id" label="ID" align="center">
      <template #header>
        <el-input
          placeholder="ID"
          v-model="searchFilter.submissionId"
        ></el-input>
      </template>
    </el-table-column>
    <el-table-column
      prop="problemId"
      label="题目ID"
      align="center"
      v-if="props.submissionListType !== 'problem'"
    >
      <template #header>
        <el-input
          placeholder="题目ID"
          v-model="searchFilter.problemId"
        ></el-input>
      </template>
    </el-table-column>
    <el-table-column prop="nickname" label="用户名" align="center">
      <template #header>
        <el-input
          placeholder="用户名"
          v-model="searchFilter.nickname"
        ></el-input>
      </template>
    </el-table-column>
    <el-table-column prop="result" label="结果" align="center" width="100px">
      <template #header>
        <el-select placeholder="结果" v-model="searchFilter.result">
          <el-option
            v-for="item in SubmissionResults"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          >
            <el-tag :type="item.tagType">{{ item.label }}</el-tag>
          </el-option>
        </el-select>
      </template>
      <template #default="scope">
        <div class="text-gray-500" v-if="scope.row.result === 'WT'">
          排队中
          <el-icon class="is-loading"><loading /></el-icon>
        </div>
        <div class="text-blue-400" v-else-if="scope.row.result === 'JG'">
          评测中
          <el-icon class="is-loading"><loading /></el-icon>
        </div>
        <el-popover
          v-else
          effect="light"
          trigger="hover"
          placement="top"
          width="auto"
          :disabled="!scope.row.detail"
        >
          <template #default>
            <div class="whitespace-pre-line">{{ scope.row.detail }}</div>
          </template>
          <template #reference>
            <el-tag type="success" v-if="scope.row.result === 'AC'">
              {{ scope.row.result }}
            </el-tag>
            <el-tag type="warning" v-else-if="scope.row.result === 'OE'">
              {{ scope.row.result }}
            </el-tag>
            <el-tag type="info" v-else-if="scope.row.result === 'PE'">
              {{ scope.row.result }}
            </el-tag>
            <el-tag type="danger" v-else>{{ scope.row.result }}</el-tag>
          </template>
        </el-popover>
      </template>
    </el-table-column>
    <el-table-column prop="score" label="分数" align="center" width="80px" />
    <el-table-column prop="language" label="语言" align="center">
      <template #default="scope">
        <el-tag round>{{ scope.row.language }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column prop="codeLength" label="长度" align="center" />
    <el-table-column prop="memoryCost" label="内存" align="center" />
    <el-table-column prop="timeCost" label="时间" align="center" />
    <el-table-column
      prop="createTime"
      label="提交时间"
      align="center"
      width="200px"
    >
      <template #default="scope">
        <span>{{
          formatRelativeTime(
            currentTime,
            convertDateFormat(scope.row.createTime),
          )
        }}</span>
      </template>
    </el-table-column>
  </el-table>
  <div class="mt-4 text-center" v-if="props.submissionListType === 'all'">
    <el-button type="info" plain :disabled="page === 1" @click="page = 1"
      >首页</el-button
    >
    <el-button type="primary" plain :disabled="page === 1" @click="page -= 1"
      >上一页</el-button
    >
    <el-button
      type="primary"
      plain
      :disabled="submissionList.length < pageSize"
      @click="page += 1"
      >下一页</el-button
    >
  </div>
  <div class="mt-4 max-w-fit mx-auto" v-else>
    <el-pagination
      v-model:current-page="page"
      :default-page-size="pageSize"
      layout="prev, pager, next, jumper"
      :page-count="pageCount"
    />
  </div>
</template>
<script setup lang="ts">
import { useAccodingStore } from "@/stores";
import {
  openNotification,
  formatRelativeTime,
  convertDateFormat,
} from "@/hooks";
import type { SubmissionFilterType, SubmissionListItemType } from "@/types";
import { SubmissionResults } from "../consts";
import {
  getAllSubmissionListService,
  getProblemSubmissionListPageCountService,
  getProblemSubmissionListService,
  getSubmissionByIdService,
} from "@/apis/problem";
import {
  getExamSubmissionListPageCountService,
  getExamSubmissionListService,
} from "@/apis/exam";
import { useAgentMascotRuntimeStore } from "@/agent/stores/agentMascotRuntime";

const props = defineProps({
  submissionListType: {
    type: String, // problem | exam | all->(only superadmin)
    required: true,
  },
  examId: {
    type: Number,
    required: false,
  },
  problemId: {
    type: Number,
    required: false,
  },
  pageSize: {
    type: Number,
    required: true,
  },
});

const store = useAccodingStore();
const mascotRuntime = useAgentMascotRuntimeStore();
const isSuper = store.user.role; // all类型权限判断
const route = useRoute();
const router = useRouter();

const page = ref(1);
const pageSize = ref(10);
const pageCount = ref(0);
const getSubmissionListLoading = ref(false);

const submissionList = reactive<any[]>([]);
const searchFilter = reactive<SubmissionFilterType>({});
const waitAndJudgingSubmissionList = <SubmissionListItemType[]>[]; // W/J可轮询列表
const waitAndJudingIteration = ref(0); // 最大轮询次数(default:5), 防止过多循环降低性能

let checkSubmissionsInterval: number | null = null;
const currentTime = ref(new Date());
let timer: number | undefined = undefined;

// init
const initSubmissionListPage = () => {
  pageSize.value = props.pageSize;
  switch (props.submissionListType) {
    case "all":
      getSubmissionList("all");
      break;
    case "problem":
      getSubmissionList("problem");
      break;
    case "exam":
      getSubmissionList("exam");
      break;
    default:
      openNotification("页面类型异常通知", "未知类型！", "error");
  }
};
const initGetService = () => {
  getSubmissionListLoading.value = true;
  submissionList.length = 0;
  waitAndJudgingSubmissionList.length = 0;
  waitAndJudingIteration.value = 0;
};

// core functions
const getResponses = async (type: string) => {
  if (type === "problem") {
    const pageCountRes = (
      await getProblemSubmissionListPageCountService(
        props.problemId as number, // 确保此处拿到的不为空
        store.token,
        page.value,
        pageSize.value,
        searchFilter,
      )
    ).data;
    const submissionListRes = (
      await getProblemSubmissionListService(
        props.problemId as number, // 确保此处拿到的不为空
        store.token,
        page.value,
        pageSize.value,
        searchFilter,
      )
    ).data;
    return { pageCountRes, submissionListRes };
  } else if (type === "exam") {
    const pageCountRes = (
      await getExamSubmissionListPageCountService(
        props.examId as number, // 确保此处拿到的不为空
        store.token,
        page.value,
        pageSize.value,
        searchFilter,
      )
    ).data;
    const submissionListRes = (
      await getExamSubmissionListService(
        props.examId as number, // 确保此处拿到的不为空
        store.token,
        page.value,
        pageSize.value,
        searchFilter,
      )
    ).data;
    return { pageCountRes, submissionListRes };
  } else {
    const pageCountRes = null;
    const submissionListRes = (
      await getAllSubmissionListService(
        store.token,
        page.value,
        pageSize.value,
        searchFilter,
      )
    ).data;
    return { pageCountRes, submissionListRes };
  }
};
const handleSubmissionList = (submissionListRes: any) => {
  submissionListRes.data.forEach((submission: any, index: number) => {
    submissionList.push(submission);
    mascotRuntime.observeSubmission(submission);
    if (submission.result === "WT" || submission.result === "JG") {
      waitAndJudgingSubmissionList.push({
        submissionId: submission.id,
        index,
      });
    }
  });
};
const getSubmissionList = async (type: string) => {
  initGetService();
  const { pageCountRes, submissionListRes } = await getResponses(type);
  if (type === "all" && submissionListRes.status === 200) {
    handleSubmissionList(submissionListRes);
    getSubmissionListLoading.value = false;
    startCheckingSubmissions();
  } else if (pageCountRes.status === 200 && submissionListRes.status === 200) {
    pageCount.value = pageCountRes.data;
    handleSubmissionList(submissionListRes);
    getSubmissionListLoading.value = false;
    startCheckingSubmissions();
  } else {
    openNotification("获取提交记录通知", "获取提交记录失败", "error");
  }
};

// check functions
const startCheckingSubmissions = () => {
  if (checkSubmissionsInterval) return;
  checkSubmissionsInterval = setInterval(async () => {
    if (waitAndJudgingSubmissionList.length > 0) {
      // 为了保证异步请求的顺序性，使用for循环遍历，尽量不使用forEach
      for (const [index, config] of waitAndJudgingSubmissionList.entries()) {
        try {
          const res = (
            await getSubmissionByIdService(config.submissionId, store.token)
          ).data;
          if (res.status === 200) {
            mascotRuntime.updateJudgeResult(
              res.data.result,
              config.submissionId,
            );
            if (res.data.result !== "WT" && res.data.result !== "JG") {
              waitAndJudgingSubmissionList.splice(index, 1);
              submissionList[config.index] = res.data;
            }
          }
        } catch (e) {
          openNotification("获取提交记录通知", `异常错误: ${e}`, "error");
        }
      }
      waitAndJudingIteration.value += 1;
    }
  }, 2000) as any;
};
const stopCheckingSubmissions = () => {
  if (checkSubmissionsInterval) {
    clearInterval(checkSubmissionsInterval);
    checkSubmissionsInterval = null;
  }
};

// utils functions
const handleRowClick = (row: any) => {
  window.open(`/submission/${row.id}`, "_blank");
};
const updateTime = () => {
  currentTime.value = new Date();
};

// watch
watch(page, () => {
  initSubmissionListPage();
});

watch(searchFilter, () => {
  page.value = 1;
  initSubmissionListPage();
});

watch(waitAndJudingIteration, () => {
  if (waitAndJudingIteration.value >= 5) {
    stopCheckingSubmissions();
  }
});

// lifecycle
onMounted(() => {
  page.value = 1;
  initSubmissionListPage();
  timer = setInterval(updateTime, 1000) as any;
});

onUnmounted(() => {
  clearInterval(timer);
  stopCheckingSubmissions();
});
</script>
