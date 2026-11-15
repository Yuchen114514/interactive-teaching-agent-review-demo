<template>
  <div class="blurred-wrapper">
    <header class="main-header container">
      <ThemeToggle class="home-appearance-control" />
      <router-link to="/login" class="btn sign-up" v-if="!isLogin">{{ isEnglishDemo ? 'Enter demo' : '登录 login' }}</router-link>
      <router-link :to="isEnglishDemo ? '/allProblems' : '/allClasses'" class="btn sign-up" v-else>{{ isEnglishDemo ? 'Browse problems' : '进入 visit' }}</router-link>
    </header>
    <div class="text-center">
      <span class="text-white main-title"># Accoding Online 5TH</span>
    </div>
    <section class="hero container text-white">
      <div class="content-wrapper">
        <h2 class="title">{{ isEnglishDemo ? 'Learn programming, one step at a time' : '编程学习, 从未如此轻松' }}<span>.</span></h2>
        <p class="message">
          {{ isEnglishDemo ? 'Explore English demo problems in the original Accoding OJ, then open the teaching Agent for guided problem solving. No registration is required.' : '在爱编程平台中, 你可以加入多种多样的课程, 体验不同主题的编程挑战, 参与到日常的考核与比赛当中, 不断提高编程水平.' }}
        </p>
        <p class="glass-text mt-6">
          > Hello, World! Welcome To ACCODING 5TH
        </p>
      </div>
    </section>
  </div>
</template>
<script setup lang="ts">
import { useAccodingStore } from '@/stores';
import ThemeToggle from '@/components/ThemeToggle.vue';
import { isEnglishDemo } from '@/en-demo/mode';

const store = useAccodingStore();
const isLogin = computed(() => store.isLogin);
</script>
<style scoped>
.blurred-wrapper {
  height: 100vh;
  background:url(../assets/homepage-plane.jpg) center/cover;
  position: relative;
  z-index: 1;
  overflow: hidden;
  /* animation: hue 5s infinite alternate; */
}

.blurred-wrapper::after {
  content: "";
  width: 100%;
  height: 100%;
  background:inherit;
  position: absolute;
  top: 0;
  left: 0;
  z-index: -1;
  transform: scale(1);
}

.glass-text {
  position: relative;
  display: inline-block;
  padding: 10px 20px;
  font-weight: bold;
  color:var(--theme-ink-primary, white);
  border-radius: 1rem;
  overflow: hidden;
  font-size: 0.5rem;
}

.glass-text::before {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background:var(--theme-surface-raised, #f8faf8);
  border-radius: 10px;
  z-index: -1;
}

@keyframes hue {
  from {
    filter: hue-rotate(0deg);
  }
  to {
    filter: hue-rotate(360deg);
  }
}

.main-header {
  height: 10rem;
  display: flex;
  align-items: center;
  justify-content: right;
}

.container {
  max-width: 120rem;
  /* margin: 0 auto; */
  padding-left: 50px;
  padding-right: 50px;
}

.logo {
  color:var(--theme-ink-primary, #fff);
  text-transform: uppercase;
  font-size: 2.5rem;
  font-weight: bold;
}

.btn {
  border:2px solid var(--theme-border, rgba(255, 255, 255, 0.8));
  padding: 0.5rem 2rem;
  border-radius: 2.5rem;
  color:var(--theme-ink-primary, #fff);
  margin-left: 1rem;
}

.btn:hover {
  background-color:var(--theme-surface-raised, white);
  color:var(--theme-ink-primary, #000);
  cursor: pointer;
  transition: all 0.3s;
}

.hero {
  display: flex;
  align-items: center;
  height: calc(100vh - 12rem);
}

.content-wrapper {
  max-width: 40rem;
  transform: translateY(-4rem);
}

.tagline {
  font-size: 1rem;
  font-weight: 100;
  margin-bottom: 1rem;
}

.title {
  font-size: 2rem;
  margin-bottom: 2rem;
}

.main-title {
  font-size: 2rem;
  margin-bottom: 2rem;
  font-weight: bold;
}

.message {
  font-size: 1.2rem;
  margin-bottom: 1cqw;
}

.cta {
  background-color:var(--theme-surface-inverse, #000);
}

@media screen and (min-width: 768px) {
  .btn {
    padding: 0.5rem 2rem;
  }
  .content-wrapper {
    max-width: 70rem;
  }
  .tagline {
    font-size: 1.3rem;
  }
  .title {
    font-size: 3rem;
  }
  .main-title {
    font-size: 3rem;
  }
  .message {
    font-size: 1.5rem;
  }
  .glass-text {
    font-size: 1rem;
  }
}

</style>
