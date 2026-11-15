<template lang="">
  <el-form
    ref="registerFormRef"
    :model="registerForm"
    status-icon
    :rules="rules"
    label-width="auto"
    class="mx-auto w-1/2"
  >
    <el-form-item label="用户名" prop="username">
      <el-input
        v-model="registerForm.username"
        type="text"
        required
        placeholder="请输入您的账号（邮箱）"
      />
    </el-form-item>
    <el-form-item label="姓名" prop="nickname">
      <el-input
        v-model="registerForm.nickname"
        type="text"
        required
        placeholder="请输入您的姓名"
      />
    </el-form-item>
    <el-form-item label="密码" prop="password">
      <el-input
        v-model="registerForm.password"
        :type="passwordVisible ? 'text' : 'password'"
        required
        placeholder="请输入密码，仅支持“英文+数字+特殊字符”格式"
      >
        <template #suffix>
          <el-icon @click="togglePasswordVisibility" class="cursor-pointer">
            <View v-if="passwordVisible" />
            <Hide v-if="!passwordVisible" />
          </el-icon>
        </template>
      </el-input>
    </el-form-item>
    <el-form-item label="确认密码" prop="passwordConfirm">
      <el-input
        v-model="registerForm.passwordConfirm"
        :type="passwordVisible ? 'text' : 'password'"
        required
        placeholder="请再次输入密码，确保两次输入的密码一致"
      >
        <template #suffix>
          <el-icon @click="togglePasswordVisibility" class="cursor-pointer">
            <View v-if="passwordVisible" />
            <Hide v-if="!passwordVisible" />
          </el-icon>
        </template>
      </el-input>
    </el-form-item>
    <el-form-item label="所在学校" prop="school">
      <el-input
        v-model="registerForm.school"
        type="text"
        required
        placeholder="请选择您所在的学校"
      />
    </el-form-item>
    <el-form-item label="所属专业" prop="major">
      <el-input v-model="registerForm.major" type="text" required placeholder="请输入您的专业" />
    </el-form-item>
    <el-form-item label="学号" prop="studentId">
      <el-input
        v-model="registerForm.studentId"
        type="text"
        required
        placeholder="请输入您的学号"
      />
    </el-form-item>
    <el-col class="text-center mt-6">
      <el-button round type="primary" class="mx-auto" @click="useRegister(registerFormRef)">
        注册
      </el-button>
    </el-col>
  </el-form>
</template>

<script setup lang="ts">
import { register } from '@/apis/user'
import type { FormInstance, FormRules } from 'element-plus'
import { openNotification } from '@/hooks'
import { useRouter } from 'vue-router'

const router = useRouter()
const registerForm = reactive({
  username: '',
  nickname: '',
  password: '',
  passwordConfirm: '',
  studentId: '',
  school: '',
  major: '',
  email: '',
  description: '',
  realName: '',
})

const passwordVisible = ref(false)
const registerFormRef = ref<FormInstance>()
const emit = defineEmits<{
  registerSuccess: [username: string]
}>()

const useRegister = (formEl: FormInstance | undefined) => {
  if (!formEl) return
  formEl.validate(async (valid) => {
    if (valid) {
      const res = (await register(registerForm)).data
      if (res.status === 200) {
        openNotification('注册信息反馈', '注册成功！', 'success')
        // 通过emit发送注册成功的用户名到父组件
        emit('registerSuccess', registerForm.username)
        router.go(0)
      } else {
        openNotification('注册信息反馈', res.msg || '注册失败，请稍后再试', 'error')
      }
    } else {
      openNotification('表单异常提示', '请检查表单数据是否有误', 'warning')
    }
  })
}

const notEmptyRule = (rule: any, value: any, callback: any) => {
  if (value === '') {
    callback(new Error('请输入合法内容'))
  } else if (value.length > 30) {
    callback(new Error('请检查输入内容是否过长！'))
  } else {
    callback()
  }
}

const emailRule = (rule: any, value: any, callback: any) => {
  const emailReg = /^[\w-]+(\.[\w-]+)*@[\w-]+(\.[\w-]+)+$/;
  if (value === '') {
    callback(new Error('请输入合法内容'))
  } else if (!emailReg.test(value)) {
    callback(new Error('请输入合法的邮箱地址'))
  } else {
    callback()
  }
}

const passwordRule = (rule: any, value: any, callback: any) => {
  const regex = /^[a-zA-Z0-9!@#$%^&*(){}._]+$/
  if (value === '') {
    callback(new Error('请输入合法内容'))
  } else if (!regex.test(value)) {
    callback(new Error('请输入英文、数字或特殊字符'))
  } else {
    callback()
  }
}

const passwordConfirmRule = (rule: any, value: any, callback: any) => {
  if (value === '') {
    callback(new Error('请输入合法内容'))
  } else if (value !== registerForm.password) {
    callback(new Error('两次输入的密码不一致'))
  } else {
    callback()
  }
}

const studentIdRule = (rule: any, value: any, callback: any) => {
  const regex = /^(\d{8}|[a-zA-Z]{2}\d{7})$/
  if (value === '') {
    callback(new Error('请输入合法内容'))
  } else if (!regex.test(value)) {
    callback(new Error('学号格式错误，请输入‘八位数字’或‘两位字母+七位数字’的格式'))
  } else {
    callback()
  }
}

const togglePasswordVisibility = () => {
  passwordVisible.value = !passwordVisible.value
}

const rules = reactive<FormRules<typeof registerForm>>({
  username: [{ validator: emailRule, trigger: 'blur' }],
  password: [{ validator: passwordRule, trigger: 'blur' }],
  passwordConfirm: [{ validator: passwordConfirmRule, trigger: 'blur' }],
  nickname: [{ validator: notEmptyRule, trigger: 'blur' }],
  studentId: [{ validator: studentIdRule, trigger: 'blur' }],
  school: [{ validator: notEmptyRule, trigger: 'blur' }],
  major: [{ validator: notEmptyRule, trigger: 'blur' }],
})
</script>
