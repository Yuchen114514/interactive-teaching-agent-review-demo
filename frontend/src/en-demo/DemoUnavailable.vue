<template>
  <div class="mx-auto max-w-3xl px-6 py-20 text-center">
    <div class="text-sm tracking-widest text-gray-500">ENGLISH RESEARCH DEMO</div>
    <h1 class="mt-5 text-3xl font-semibold">This section is not included in the demo</h1>
    <p class="mx-auto mt-5 max-w-xl leading-7 text-gray-500">
      The demo focuses on English problem statements and the teaching Agent. Courses,
      contests, rankings, and account management remain on the main OJ platform.
    </p>
    <el-button class="mt-8" type="primary" round @click="$router.push('/allProblems')">
      Browse demo problems
    </el-button>
  </div>
</template>
