<template lang="">
  <template v-if="isEnglishDemo">
    <el-col class="text-2xl bold text-center my-6">Demo Login | LOGIN</el-col>
    <el-col class="text-center my-10 text-gray-500">
      Continue as a visitor to explore the original Accoding OJ and its English Agent demo.
    </el-col>
    <el-col v-if="store.demoSessionExpired" class="text-center my-4 text-gray-500" role="status">
      Your previous demo visit could not be restored. Enter the demo to continue.
    </el-col>
    <el-form label-width="auto" class="mx-auto w-1/2">
      <el-col class="text-center my-10">
        <el-button round type="primary" :loading="demoLoginPending" @click="useDemoLogin">Enter demo</el-button>
      </el-col>
    </el-form>
    <el-col class="text-center my-4 text-gray-500">No registration or password required.</el-col>
    <el-col v-if="demoLoginError" class="text-center my-4 text-red-500" role="alert">{{ demoLoginError }}</el-col>
  </template>
  <template v-else>
  <el-col class="text-2xl bold text-center my-6">
    <div v-if="currentType === 0">登录 | LOGIN</div>
    <div v-else-if="currentType === 1">注册 | REGISTER</div>
    <div v-else>忘记密码 | FIND PASS</div>
  </el-col>
  <OjStatistic class="my-10 w-1/2 mx-auto" />
  <el-form
    v-if="currentType === 0"
    ref="loginFormRef"
    :model="loginForm"
    status-icon
    :rules="rules"
    label-width="auto"
    class="mx-auto w-1/2"
  >
    <el-form-item label="用户名" prop="username">
      <el-input
        v-model="loginForm.username"
        type="text"
        required
        placeholder="请输入您的账号（邮箱）"
      />
    </el-form-item>
    <el-form-item label="密码" prop="password">
      <el-input v-model="loginForm.password" :type="passwordVisible ? 'text' : 'password'" required>
        <template #suffix>
          <el-icon @click="togglePasswordVisibility" class="cursor-pointer">
            <View v-if="passwordVisible" />
            <Hide v-if="!passwordVisible" />
          </el-icon>
        </template>
      </el-input>
    </el-form-item>
    <el-col class="text-center my-10">
      <el-button round type="primary" @click="useLogin(loginFormRef)" class="mx-auto">
        登录
      </el-button>
    </el-col>
  </el-form>
  <UserRegister v-else-if="currentType === 1" />
  <el-col class="text-center my-4">
    <el-button text @click="currentType = 1" v-if="currentType === 0">没有账号？点此注册</el-button>
    <el-button text @click="currentType = 0" v-else-if="currentType === 1"
      >已有账号？点此登录</el-button
    >
    <el-divider direction="vertical" />
    <el-button text @click="goFindBackPassword()">忘记密码</el-button>
  </el-col>
  </template>
</template>

<script lang="ts" setup>
import { useAccodingStore } from '@/stores'
import type { FormInstance, FormRules } from 'element-plus'
import { openNotification } from '@/hooks'
import OjStatistic from '@/components/OjStatistic.vue'
import UserRegister from './UserRegister.vue'
import { isEnglishDemo } from '@/en-demo/mode'

const demoLoginPending = ref(false)
const demoLoginError = ref('')

const currentType = ref(0)
const store = useAccodingStore()
const router = useRouter();

const useDemoLogin = async () => {
  if (demoLoginPending.value) return
  demoLoginPending.value = true
  demoLoginError.value = ''
  try { await store._loginDemoGuest() }
  catch { demoLoginError.value = 'Demo sign-in is temporarily unavailable. Please try again.' }
  finally { demoLoginPending.value = false }
}

const loginForm = reactive({
  username: '',
  password: '',
})

const loginFormRef = ref<FormInstance>()

const useLogin = (formEl: FormInstance | undefined) => {
  if (!formEl) return
  formEl.validate(async (valid) => {
    if (valid) {
      store._login(loginForm.username, loginForm.password)
    } else {
      openNotification('表单异常提示', '请检查表单数据是否有误', 'warning')
    }
  })
}
const passwordVisible = ref(false)

const notEmptyRule = (rule: any, value: any, callback: any) => {
  if (value === '') {
    callback(new Error('请输入用户名/密码'))
  } else {
    callback()
  }
}

const emailRule = (rule: any, value: any, callback: any) => {
  const emailReg = /^[\w-]+(\.[\w-]+)*@[\w-]+(\.[\w-]+)+$/
  if (value === '') {
    callback(new Error('请输入合法内容'))
  } else if (!emailReg.test(value)) {
    callback(new Error('请输入合法的邮箱地址'))
  } else {
    callback()
  }
}

const togglePasswordVisibility = () => {
  passwordVisible.value = !passwordVisible.value
}

const rules = reactive<FormRules<typeof loginForm>>({
  username: [{ validator: emailRule, trigger: 'blur' }],
  password: [{ validator: notEmptyRule, trigger: 'blur' }],
})

const goFindBackPassword = () => {
  router.push('/findBackPassword')
}

// 处理注册成功事件
const handleRegisterSuccess = async (username: string) => {
  // 切换到登录界面
  currentType.value = 0
}
</script>
