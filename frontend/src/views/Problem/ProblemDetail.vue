<template lang="">
  <el-backtop :right="50" :bottom="50" />
  <el-page-header @back="onBack" class="w-4/5 mx-auto" :title="isEnglishDemo ? 'Back to problem list' : '回到问题列表'">
    <template #content>
      <div class="flex items-center">
        <span class="text-large font-600 mr-3"> {{ problem.title }} </span>
        <el-tag>{{ problem.nickname }}</el-tag>
      </div>
    </template>
    <template #extra>
      <div class="flex items-center">
        <el-button
          type="info"
          round
          @click="checkoutToSubmissions"
          v-if="viewSelected === 0 && !isEnglishDemo"
          >查看评测记录</el-button
        >
        <el-button type="info" round @click="viewSelected = 0" v-else-if="!isEnglishDemo"
          >查看题目</el-button
        >
        <el-button
          type="primary"
          round
          @click="openSubmitDialog = true"
          :disabled="Boolean(viewSelected)"
          v-if="!isEnglishDemo"
          >提交</el-button
        >
        <el-tag v-if="isEnglishDemo" type="info" round>Agent discussion only · No judge</el-tag>
      </div>
    </template>
    <el-descriptions :column="4" class="mt-4" border>
      <el-descriptions-item :label="isEnglishDemo ? 'Submissions' : '提交次数'" align="center" width="150px">
        {{ problem.sub_num }}
      </el-descriptions-item>
      <el-descriptions-item :label="isEnglishDemo ? 'Accepted' : '通过数'" align="center" width="150px">
        {{ problem.ac_num }}
        <el-tag type="success" round v-if="problem.sub_num > 0"
          >{{ ((problem.ac_num / problem.sub_num) * 100).toFixed(1) }}%</el-tag
        >
      </el-descriptions-item>
      <el-descriptions-item :label="isEnglishDemo ? 'Participants' : '提交人数'" align="center" width="150px">
        {{ problem.sub_user_num }}
      </el-descriptions-item>
      <el-descriptions-item :label="isEnglishDemo ? 'Solved by' : '通过人数'" align="center" width="150px">
        {{ problem.ac_user_num }}
        <el-tag v-if="problem.sub_user_num > 0" type="success" round
          >{{
            ((problem.ac_user_num / problem.sub_user_num) * 100).toFixed(1)
          }}%</el-tag
        >
      </el-descriptions-item>
      <el-descriptions-item :label="isEnglishDemo ? 'Time limit' : '时间限制'" align="center" width="150px">
        {{ problem.time_limit }}
      </el-descriptions-item>
      <el-descriptions-item :label="isEnglishDemo ? 'Memory limit' : '空间限制'" align="center" width="150px">
        {{ problem.memory_limit }}
      </el-descriptions-item>
      <el-descriptions-item :label="isEnglishDemo ? 'Languages' : '语言'" align="center">
        <el-tag v-for="lang in langs" :key="lang.id" class="mx-2">{{
          lang.name
        }}</el-tag>
      </el-descriptions-item>
    </el-descriptions>
  </el-page-header>
  <div class="w-4/5 mx-auto mt-4">
    <mavon-editor
      v-if="viewSelected === 0"
      v-model="problem.content"
      :subfield="false"
      defaultOpen="preview"
      :toolbarsFlag="false"
      :boxShadow="false"
      previewBackground="white"
    />
    <SubmissionList
      v-else
      submission-list-type="problem"
      :page-size="20"
      :problem-id="Number(problemId)"
      :key="problemSubmissionListKey"
    />
  </div>
  <el-dialog
    v-model="openSubmitDialog"
    title="提交代码"
    width="600"
    draggable
    :lock-scroll="false"
  >
    <div class="text-blue-400">
      <el-icon>
        <InfoFilled />
      </el-icon>
      提示: 可以拖拽代码框方便查看题面
    </div>
    <el-select
      v-model="currentSubmission.languageId"
      placeholder="Select"
      class="mt-4 mx-auto"
    >
      <el-option v-for="l in langs" :key="l.id" :label="l.name" :value="l.id" />
    </el-select>
    <el-input
      v-model="currentSubmission.code"
      style="width: 560px"
      :autosize="{ minRows: 10, maxRows: 20 }"
      type="textarea"
      maxlength="1000"
      show-word-limit
      placeholder="请输入提交代码"
      class="mt-4 mx-auto"
    />
    <template #footer>
      <div class="dialog-footer">
        <el-button @click="openSubmitDialog = false">取消</el-button>
        <el-button
          type="primary"
          @click="postProblemSubmission"
          :disabled="!currentSubmission.code.length"
        >
          提交
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>
<script setup lang="ts">
import {
  type SubmissionPostData,
  getProblemInfoService,
  postProblemSubmissionService,
} from "@/apis/problem";
import { useAccodingStore } from "@/stores";
import { formatDateTime, openNotification } from "@/hooks";
import SubmissionList from "./ProblemComponents/SubmissionList.vue";
import { useAgentMascotRuntimeStore } from "@/agent/stores/agentMascotRuntime";
import { isEnglishDemo } from "@/en-demo/mode";
import { findDemoProblem } from "@/en-demo/catalog";

const route = useRoute();
const router = useRouter();
const store = useAccodingStore();
const mascotRuntime = useAgentMascotRuntimeStore();
const problemId = route.params.problemId;
const problem = ref({});
const langs = ref([]);
const viewSelected = ref(0);
const openSubmitDialog = ref(false);
const problemSubmissionListKey = ref(0);
const currentSubmission = reactive({
  languageId: 0,
  code: "",
});

const getProblemInfoById = async () => {
  if (isEnglishDemo) {
    const demo = findDemoProblem(Number(problemId));
    if (!demo) { router.push('/allProblems'); return; }
    problem.value = {
      title: `${demo.id} · ${demo.title}`,
      nickname: 'Accoding',
      content: [
        `## Problem statement\n${demo.statement}`,
        `## Input\n${demo.input}`,
        `## Output\n${demo.output}`,
        `## Constraints\n${demo.constraints}`,
        ...demo.examples.map((example, index) =>
          `## Example ${index + 1}\n**Input**\n\n\`\`\`text\n${example.input}\n\`\`\`\n\n**Output**\n\n\`\`\`text\n${example.output}\n\`\`\`\n\n${example.explanation}`,
        ),
      ].join('\n\n'),
      sub_num: 0, ac_num: 0, sub_user_num: 0, ac_user_num: 0,
      time_limit: demo.timeLimit, memory_limit: demo.memoryLimit,
    } as any;
    langs.value = [];
    return;
  }
  const infoRes = (await getProblemInfoService(Number(problemId), store.token))
    .data;
  if (infoRes.status === 200) {
    problem.value = infoRes.data.problem;
    langs.value = infoRes.data.languages;
    currentSubmission.languageId = (
      langs.value[0] as { id: number; name: string; info: string }
    ).id;
  } else {
    openNotification("题目页面通知", "获取题目信息失败", "error");
  }
};

const onBack = () => {
  router.push("/allProblems");
};

const postProblemSubmission = async () => {
  if (isEnglishDemo) return;
  const submission: SubmissionPostData = {
    languageId: currentSubmission.languageId,
    content: currentSubmission.code,
    codeLength: currentSubmission.code.length,
    problemId: Number(problemId),
    submitTime: formatDateTime(new Date()),
    questionTime: (problem.value as any).update_time,
  };
  const mascotGeneration = mascotRuntime.beginSubmission({
    problemId: Number(problemId),
    nickname: store.user.nickname,
  });
  try {
    const res = (await postProblemSubmissionService(store.token, submission))
      .data;
    if (res.status !== 200) {
      mascotRuntime.failSubmission(mascotGeneration);
      openNotification("提交通知", "提交代码失败", "error");
      return;
    }

    mascotRuntime.acknowledgeSubmission(mascotGeneration, res);
    void mascotRuntime.trackSubmission(mascotGeneration, store.token);
    openNotification("提交通知", "提交代码成功", "success");
    // 通过key值变化强行触发子组件重新渲染
    problemSubmissionListKey.value += 1;
    viewSelected.value = 1;
    openSubmitDialog.value = false;
    currentSubmission.code = "";
    currentSubmission.languageId = (langs.value[0] as any).id;
  } catch (error) {
    mascotRuntime.failSubmission(mascotGeneration);
    openNotification("提交通知", "提交代码失败", "error");
    console.warn("提交代码失败", error);
  }
};

const checkoutToSubmissions = () => {
  viewSelected.value = 1;
};

onMounted(() => {
  getProblemInfoById();
});
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
